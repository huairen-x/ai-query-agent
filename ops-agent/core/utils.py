"""
企业级工具模块 - 重试、优雅关闭、线程安全、指标收集

功能:
- retry: 带指数退避的重试装饰器
- GracefulShutdown: 信号处理 + 优雅关闭
- ThreadSafeDb: 线程安全的数据库连接包装
- MetricsCollector: 结构化指标收集器
"""
from __future__ import annotations
import time
import random
import logging
import signal
import threading
import sqlite3
from typing import Optional, Callable, TypeVar, Any
from functools import wraps
from dataclasses import dataclass, field

logger = logging.getLogger("ops-agent.utils")

T = TypeVar("T")


# ============================================================
# 重试装饰器（指数退避 + 抖动）
# ============================================================

def retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    exceptions: tuple = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None,
):
    """
    重试装饰器 - 指数退避 + 随机抖动

    Args:
        max_retries: 最大重试次数
        base_delay: 初始延迟(秒)
        max_delay: 最大延迟(秒)
        exponential_base: 指数退避基数
        jitter: 是否启用随机抖动
        exceptions: 需要重试的异常类型
        on_retry: 重试回调函数

    Usage:
        @retry(max_retries=3, exceptions=(ConnectionError, TimeoutError))
        def fetch_data(url: str) -> dict:
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(
                            base_delay * (exponential_base ** attempt),
                            max_delay,
                        )
                        if jitter:
                            delay = delay * (0.5 + random.random() * 0.5)
                        if on_retry:
                            on_retry(e, attempt + 1)
                        logger.warning(
                            f"重试 {func.__name__}: 第 {attempt + 1}/{max_retries} 次, "
                            f"延迟 {delay:.1f}s, 异常: {e}"
                        )
                        time.sleep(delay)
            raise last_exception  # type: ignore
        return wrapper
    return decorator


# ============================================================
# 优雅关闭管理器
# ============================================================

class GracefulShutdown:
    """
    优雅关闭管理器 - 捕获 SIGTERM/SIGINT 信号，协调资源释放

    Usage:
        shutdown = GracefulShutdown()
        shutdown.register("db", lambda: db.close())
        shutdown.register("server", lambda: server.stop())

        # 在启动后调用
        shutdown.wait_for_shutdown()
    """

    def __init__(self):
        self._shutdown_event = threading.Event()
        self._cleanup_handlers: list[tuple[str, Callable]] = []
        self._shutdown_started = False
        self._lock = threading.Lock()

        # 注册信号处理
        if threading.current_thread() is threading.main_thread():
            try:
                signal.signal(signal.SIGTERM, self._signal_handler)
                signal.signal(signal.SIGINT, self._signal_handler)
                logger.info("优雅关闭管理器已注册 SIGTERM/SIGINT")
            except ValueError:
                logger.warning("非主线程，跳过信号注册")

    def _signal_handler(self, signum: int, frame) -> None:
        """信号处理回调"""
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        logger.info(f"收到 {sig_name}, 开始优雅关闭...")
        self.shutdown()

    def register(self, name: str, handler: Callable) -> None:
        """注册清理处理器"""
        self._cleanup_handlers.append((name, handler))
        logger.debug(f"注册清理处理器: {name}")

    def shutdown(self) -> None:
        """执行优雅关闭"""
        with self._lock:
            if self._shutdown_started:
                return
            self._shutdown_started = True

        logger.info("开始执行优雅关闭...")
        for name, handler in reversed(self._cleanup_handlers):
            try:
                logger.info(f"清理: {name}")
                handler()
            except Exception as e:
                logger.error(f"清理失败 {name}: {e}")

        self._shutdown_event.set()
        logger.info("优雅关闭完成")

    def wait_for_shutdown(self, timeout: Optional[float] = None) -> bool:
        """
        等待关闭信号
        Returns: 是否收到关闭信号
        """
        return self._shutdown_event.wait(timeout=timeout)

    @property
    def is_shutting_down(self) -> bool:
        return self._shutdown_started


# ============================================================
# 线程安全数据库连接
# ============================================================

class ThreadSafeDb:
    """
    线程安全数据库连接包装

    使用 threading.local 为每个线程维护独立连接
    避免 SQLite 多线程写入冲突

    Usage:
        db = ThreadSafeDb("path/to/db.db")
        db.execute("CREATE TABLE ...")
        rows = db.query("SELECT * FROM ...")
        db.close()
    """

    def __init__(self, db_path: str, timeout: float = 10.0):
        self._db_path = db_path
        self._timeout = timeout
        self._local = threading.local()
        self._write_lock = threading.Lock()
        logger.info(f"线程安全数据库初始化: {db_path}")

    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的连接"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self._db_path,
                timeout=self._timeout,
                check_same_thread=False,  # 由本类负责线程安全
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """执行写操作（线程安全）"""
        with self._write_lock:
            conn = self._get_conn()
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor

    def executemany(self, sql: str, params_list: list) -> sqlite3.Cursor:
        """批量执行写操作（线程安全）"""
        with self._write_lock:
            conn = self._get_conn()
            cursor = conn.executemany(sql, params_list)
            conn.commit()
            return cursor

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """执行读操作（无需锁）"""
        conn = self._get_conn()
        return conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """查询单行"""
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self):
        """关闭当前线程的连接"""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

    def close_all(self):
        """关闭所有线程的连接（线程不安全，仅用于主线程关闭）"""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None


# ============================================================
# 结构化指标收集器
# ============================================================

@dataclass
class Metric:
    """指标数据点"""
    name: str
    value: float
    tags: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """
    结构化指标收集器

    支持：
    - 计数器 (counter)
    - 平均值 (gauge)
    - 直方图 (histogram)
    - 快照导出

    Usage:
        metrics = MetricsCollector()
        metrics.counter("events_received")
        metrics.gauge("queue_depth", 42)
        metrics.histogram("processing_time", 1.5)
        snapshot = metrics.snapshot()
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}
        self._metrics_log: list[Metric] = []

    def counter(self, name: str, delta: int = 1) -> int:
        """递增计数器"""
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + delta
            return self._counters[name]

    def gauge(self, name: str, value: float) -> None:
        """设置仪表值"""
        with self._lock:
            self._gauges[name] = value

    def histogram(self, name: str, value: float) -> None:
        """记录直方图样本"""
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = []
            self._histograms[name].append(value)
            # 只保留最近 1000 个样本
            if len(self._histograms[name]) > 1000:
                self._histograms[name] = self._histograms[name][-1000:]

    def record(self, name: str, value: float, tags: Optional[dict] = None) -> None:
        """记录带标签的指标"""
        self._metrics_log.append(Metric(
            name=name,
            value=value,
            tags=tags or {},
        ))
        # 限制日志大小
        if len(self._metrics_log) > 10000:
            self._metrics_log = self._metrics_log[-5000:]

    def snapshot(self) -> dict:
        """
        获取指标快照
        """
        with self._lock:
            histograms = {}
            for name, values in self._histograms.items():
                if values:
                    sorted_vals = sorted(values)
                    n = len(sorted_vals)
                    histograms[name] = {
                        "count": n,
                        "min": min(values),
                        "max": max(values),
                        "avg": sum(values) / n,
                        "p50": sorted_vals[int(n * 0.50)],
                        "p90": sorted_vals[int(n * 0.90)],
                        "p99": sorted_vals[int(n * 0.99)],
                    }

            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": histograms,
                "total_metrics_logged": len(self._metrics_log),
            }

    def reset(self) -> None:
        """重置所有指标"""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._metrics_log.clear()


# 全局指标收集器实例
_global_metrics = MetricsCollector()


def get_metrics() -> MetricsCollector:
    """获取全局指标收集器"""
    return _global_metrics