"""
配置管理 - 支持 YAML 文件 + 环境变量 + 默认值三层覆盖
企业级配置体系：默认值 < 配置文件 < 环境变量 < 运行时参数
"""
from __future__ import annotations
import os
import json
import logging
from dataclasses import dataclass, field, is_dataclass
from typing import Optional

logger = logging.getLogger("ops-agent.config")

# 尝试导入 yaml，不可用时降级到 json
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False
    logger.warning("PyYAML 未安装，配置使用 JSON 格式")

# ============================================================
# 配置数据类
# ============================================================

@dataclass
class DSConfig:
    """DolphinScheduler 连接配置"""
    host: str = "localhost"
    port: int = 12345
    api_prefix: str = "/dolphinscheduler"
    token: str = ""
    api_version: str = "v2"
    timeout: int = 30
    poll_interval: int = 30        # 状态轮询间隔(秒)
    event_webhook_port: int = 8081  # Webhook 监听端口


@dataclass
class K8sConfig:
    """Kubernetes 配置"""
    in_cluster: bool = True        # 是否在集群内运行
    kubeconfig_path: str = ""      # 集群外时使用
    worker_deployment: str = "ds-worker"
    worker_namespace: str = "ds"
    default_scale_step: int = 2    # 默认扩缩容步长
    max_replicas: int = 20
    min_replicas: int = 3


@dataclass
class DetectionConfig:
    """异常检测配置"""
    timeout_ratio_threshold: float = 1.5       # 耗时超过预期 50% 视为超时
    baseline_std_threshold: float = 3.0         # 偏离基线 3σ
    retry_threshold: int = 3                    # 重试超过 3 次
    cpu_high_threshold: float = 85.0            # CPU 高水位(%)
    queue_depth_threshold: int = 100            # 队列积压阈值
    min_samples_for_baseline: int = 7           # 基线建模最少样本数
    enable_ml_detection: bool = True            # 是否启用 ML 检测


@dataclass
class LLMConfig:
    """LLM 推理配置"""
    provider: str = "deepseek"                   # deepseek / openai / qwen
    model: str = "deepseek-chat"
    api_key: str = ""
    api_base: str = ""
    temperature: float = 0.1                     # 低温度保证确定性
    max_tokens: int = 2000
    timeout: int = 60
    enable_reasoning: bool = True


@dataclass
class CircuitBreakerConfig:
    """熔断器配置"""
    failure_threshold: int = 3                   # 连续失败 N 次触发熔断
    recovery_timeout: int = 300                  # 熔断持续时间(秒)
    half_open_max_retries: int = 2               # 半开状态最大尝试次数
    metrics_window: int = 3600                   # 统计窗口(秒)


@dataclass
class NotificationConfig:
    """通知配置"""
    dingtalk_webhook: str = ""
    wecom_webhook: str = ""
    sms_gateway: str = ""
    on_call_phone: str = ""
    enable_auto_notify: bool = True
    p0_phone_alert: bool = True                  # P0 是否电话告警


@dataclass
class AppConfig:
    """应用总配置"""
    ds: DSConfig = field(default_factory=DSConfig)
    k8s: K8sConfig = field(default_factory=K8sConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    notification: NotificationConfig = field(default_factory=NotificationConfig)
    log_level: str = "INFO"
    data_dir: str = "./data"
    mock_mode: bool = False                      # Mock 模式(无外部依赖)


# ============================================================
# 配置加载器
# ============================================================

class ConfigLoader:
    """
    配置加载器
    优先级: 默认值 < config.json/config.yaml < 环境变量(APP_前缀)
    """

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or os.environ.get(
            "APP_CONFIG_PATH", ""
        )
        # 如果未指定路径，自动查找
        if not self.config_path:
            base = os.path.join(os.path.dirname(__file__), "..", "config")
            # PyYAML 不可用时优先 JSON
            if HAS_YAML:
                for name in ("default.yaml", "default.json"):
                    candidate = os.path.join(base, name)
                    if os.path.exists(candidate):
                        self.config_path = candidate
                        break
            else:
                for name in ("default.json", "default.yaml"):
                    candidate = os.path.join(base, name)
                    if os.path.exists(candidate):
                        self.config_path = candidate
                        break

    def load(self) -> AppConfig:
        config = AppConfig()

        # 1. 加载 YAML 配置文件
        yaml_config = self._load_yaml()
        self._merge_dict(config, yaml_config)

        # 2. 环境变量覆盖
        self._apply_env_overrides(config)

        logger.info(f"配置加载完成: {self.config_path}, mock_mode={config.mock_mode}")
        return config

    def _load_yaml(self) -> dict:
        if not os.path.exists(self.config_path):
            logger.warning(f"配置文件不存在, 使用默认配置: {self.config_path}")
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                content = f.read()
                if self.config_path.endswith(".json"):
                    return json.loads(content)
                elif HAS_YAML:
                    return yaml.safe_load(content) or {}
                else:
                    logger.warning("PyYAML 未安装, 无法加载 YAML 配置文件")
                    return {}
        except Exception as e:
            logger.warning(f"配置文件加载失败: {e}, 使用默认配置")
            return {}

    def _merge_dict(self, config: AppConfig, data: dict):
        """递归合并字典到 dataclass 对象"""
        if not data:
            return
        for key, value in data.items():
            if hasattr(config, key):
                attr = getattr(config, key)
                if is_dataclass(attr) and isinstance(value, dict):
                    self._merge_dict(attr, value)
                else:
                    setattr(config, key, value)

    def _apply_env_overrides(self, config: AppConfig):
        """环境变量覆盖: APP_DS_HOST, APP_LLM_API_KEY 等"""
        mapping = {
            "APP_DS_HOST": ("ds", "host"),
            "APP_DS_PORT": ("ds", "port"),
            "APP_DS_TOKEN": ("ds", "token"),
            "APP_LLM_API_KEY": ("llm", "api_key"),
            "APP_LLM_PROVIDER": ("llm", "provider"),
            "APP_LLM_MODEL": ("llm", "model"),
            "APP_LOG_LEVEL": ("", "log_level"),
            "APP_MOCK_MODE": ("", "mock_mode"),
            "APP_DINGTALK_WEBHOOK": ("notification", "dingtalk_webhook"),
        }
        for env_key, (sub, attr) in mapping.items():
            value = os.environ.get(env_key)
            if value is not None:
                if sub:
                    getattr(config, sub).__setattr__(attr, self._cast_type(value))
                else:
                    config.__setattr__(attr, self._cast_type(value))

    @staticmethod
    def _cast_type(value: str):
        """类型转换"""
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
        if value.isdigit():
            return int(value)
        try:
            return float(value)
        except ValueError:
            return value


# 全局单例
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """获取全局配置"""
    global _config
    if _config is None:
        loader = ConfigLoader()
        _config = loader.load()
    return _config


def reload_config(path: Optional[str] = None) -> AppConfig:
    """重新加载配置"""
    global _config
    loader = ConfigLoader(path)
    _config = loader.load()
    return _config