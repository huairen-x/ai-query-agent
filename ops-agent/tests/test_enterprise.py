"""
企业级测试套件 - 边界情况、并发安全、集成测试

测试范围:
- 工具模块: retry, GracefulShutdown, ThreadSafeDb, MetricsCollector
- 边界情况: 空输入、异常输入、极限值
- 并发安全: 多线程写入竞争
- 集成测试: 端到端全流程
"""
import sys
import os
import time
import json
import threading
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils import retry, ThreadSafeDb, MetricsCollector, get_metrics
from core.models import (
    WorkflowEvent, AnomalyContext, AnomalyType, Severity,
    ActionPlan, ActionItem, ActionType, RiskLevel, RollbackPlan,
    ExecutionResult, ActionResult,
)
from core.safety_guard import ActionValidator, RiskAssessor, CircuitBreaker, CircuitBreakerOpenError
from core.knowledge_base import KnowledgeBase


# ============================================================
# 测试: retry 装饰器
# ============================================================

class TestRetry:
    def test_retry_success_on_first_try(self):
        """首次尝试即成功"""
        call_count = 0

        @retry(max_retries=3)
        def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = succeed()
        assert result == "ok"
        assert call_count == 1

    def test_retry_after_failures(self):
        """失败后重试最终成功"""
        call_count = 0

        @retry(max_retries=3, base_delay=0.01)
        def eventually_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError(f"模拟失败 {call_count}")
            return "ok"

        result = eventually_succeed()
        assert result == "ok"
        assert call_count == 3

    def test_retry_exhausted(self):
        """重试耗尽后抛出异常"""
        call_count = 0

        @retry(max_retries=2, base_delay=0.01)
        def always_fail():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("始终失败")

        with pytest.raises(RuntimeError):
            always_fail()
        assert call_count == 3  # 1次尝试 + 2次重试

    def test_retry_specific_exception(self):
        """只重试指定异常类型"""
        @retry(max_retries=2, base_delay=0.01, exceptions=(ValueError,))
        def raise_type_error():
            raise TypeError("类型错误不应重试")

        with pytest.raises(TypeError):
            raise_type_error()

    def test_retry_on_retry_callback(self):
        """验证 on_retry 回调被调用"""
        retry_attempts = []

        @retry(max_retries=2, base_delay=0.01, on_retry=lambda e, n: retry_attempts.append(n))
        def failing():
            raise ValueError("fail")

        with pytest.raises(ValueError):
            failing()
        assert retry_attempts == [1, 2]


# ============================================================
# 测试: MetricsCollector
# ============================================================

class TestMetricsCollector:
    def test_counter(self):
        metrics = MetricsCollector()
        assert metrics.counter("requests") == 1
        assert metrics.counter("requests") == 2
        assert metrics.counter("requests", 5) == 7

    def test_gauge(self):
        metrics = MetricsCollector()
        metrics.gauge("cpu", 85.5)
        metrics.gauge("cpu", 90.0)
        snapshot = metrics.snapshot()
        assert snapshot["gauges"]["cpu"] == 90.0

    def test_histogram(self):
        metrics = MetricsCollector()
        for v in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]:
            metrics.histogram("latency", v)
        snapshot = metrics.snapshot()
        hist = snapshot["histograms"]["latency"]
        assert hist["count"] == 10
        assert hist["min"] == 1.0
        assert hist["max"] == 10.0
        assert hist["avg"] == 5.5
        assert hist["p50"] == 5.0 or hist["p50"] == 6.0  # 取决于排序后取中位数的方式

    def test_record_with_tags(self):
        metrics = MetricsCollector()
        metrics.record("api_call", 0.5, tags={"endpoint": "/health", "method": "GET"})
        snapshot = metrics.snapshot()
        assert snapshot["total_metrics_logged"] == 1

    def test_reset(self):
        metrics = MetricsCollector()
        metrics.counter("test", 10)
        metrics.gauge("test", 1.0)
        metrics.reset()
        snapshot = metrics.snapshot()
        assert snapshot["counters"] == {}
        assert snapshot["gauges"] == {}

    def test_histogram_overflow(self):
        """直方图超过 1000 个样本应截断"""
        metrics = MetricsCollector()
        for i in range(1100):
            metrics.histogram("overflow", float(i))
        snapshot = metrics.snapshot()
        # 应该只保留最近 1000 个
        assert snapshot["histograms"]["overflow"]["count"] == 1000

    def test_concurrent_counter(self):
        """并发计数器应线程安全"""
        metrics = MetricsCollector()
        n_threads = 10
        increments_per_thread = 100

        def increment():
            for _ in range(increments_per_thread):
                metrics.counter("concurrent")

        threads = [threading.Thread(target=increment) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert metrics.snapshot()["counters"]["concurrent"] == n_threads * increments_per_thread


# ============================================================
# 测试: ThreadSafeDb
# ============================================================

class TestThreadSafeDb:
    @pytest.fixture
    def db(self, tmp_path):
        db_path = os.path.join(tmp_path, "test_threadsafe.db")
        return ThreadSafeDb(db_path)

    def test_create_and_query(self, db):
        db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO test VALUES (?, ?)", (1, "hello"))
        rows = db.query("SELECT * FROM test")
        assert len(rows) == 1
        assert rows[0]["name"] == "hello"

    def test_query_one(self, db):
        db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO test VALUES (?, ?)", (1, "hello"))
        row = db.query_one("SELECT * FROM test WHERE id=?", (1,))
        assert row is not None
        assert row["name"] == "hello"
        assert db.query_one("SELECT * FROM test WHERE id=?", (999,)) is None

    def test_executemany(self, db):
        db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
        db.executemany("INSERT INTO test VALUES (?, ?)",
                       [(i, f"name_{i}") for i in range(10)])
        rows = db.query("SELECT COUNT(*) as cnt FROM test")
        assert rows[0]["cnt"] == 10

    def test_concurrent_writes(self, db):
        """并发写入应线程安全"""
        db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
        n_threads = 5
        writes_per_thread = 50

        def writer(thread_id):
            for i in range(writes_per_thread):
                db.execute("INSERT INTO test VALUES (?, ?)",
                          (thread_id * writes_per_thread + i, f"thread_{thread_id}"))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        rows = db.query("SELECT COUNT(*) as cnt FROM test")
        assert rows[0]["cnt"] == n_threads * writes_per_thread

    def test_close(self, db):
        db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
        db.close()
        # 关闭后不应崩溃
        db.close_all()


# ============================================================
# 测试: 边界情况
# ============================================================

class TestEdgeCases:
    def test_empty_event_raw_data(self):
        """空 raw_data 应能处理"""
        event = WorkflowEvent(
            event_id="evt_empty",
            event_type="unknown",
            workflow_id="WF-000",
            workflow_name="",
            task_id="T-000",
            task_name="",
            tenant="default",
            timestamp=time.time(),
            raw_data={},
        )
        assert event.raw_data == {}

    def test_extreme_values_in_context(self):
        """极限值应能处理"""
        context = AnomalyContext(
            anomaly_id="anom_extreme",
            anomaly_type=AnomalyType.UNKNOWN,
            severity=Severity.P3,
            event=WorkflowEvent(
                event_id="e", event_type="t", workflow_id="w",
                workflow_name="w", task_id="t", task_name="t",
                tenant="t", timestamp=time.time(), raw_data={},
            ),
            expected_duration=0,           # 零耗时
            actual_duration=999999999,     # 极大值
            historical_durations=[],
            retry_count=999,               # 极大重试次数
            cluster_cpu_avg=100.0,         # 100% CPU
            worker_count=0,                # 零 Worker
            queue_depth=999999,            # 极大队列
            running_tasks=99999,
            upstream_tasks=[],
            downstream_tasks=[],
            critical_path=True,
            event_time=time.time(),
            time_since_creation=0,
        )
        assert context.actual_duration == 999999999
        assert context.worker_count == 0

    def test_action_plan_no_rollback(self):
        """无回滚预案的 ActionPlan"""
        plan = ActionPlan(
            plan_id="plan_no_rollback",
            actions=[],
            risk_level=RiskLevel.LOW,
            root_cause="测试",
            reasoning="测试",
            confidence=0.5,
        )
        assert plan.rollback_plan is None
        assert plan.to_json() is not None

    def test_risk_assessor_no_actions(self):
        """空动作列表的风险评估"""
        assessor = RiskAssessor()
        plan = ActionPlan(
            plan_id="plan_empty",
            actions=[],
            risk_level=RiskLevel.LOW,
            root_cause="",
            reasoning="",
            confidence=0.0,
        )
        risk = assessor.assess(plan, 0)
        assert risk == RiskLevel.LOW  # 空动作列表风险最低

    def test_circuit_breaker_metrics(self):
        """熔断器指标导出"""
        breaker = CircuitBreaker()
        metrics = breaker.get_metrics()
        assert "state" in metrics
        assert "failure_count" in metrics
        assert "total_failures" in metrics
        assert "total_successes" in metrics
        assert metrics["state"] == "closed"

    def test_rule_engine_no_rules(self):
        """无规则时不应匹配"""
        from core.anomaly_detector import RuleEngine, RuleEngine as RE
        engine = RE()
        # 使用空规则列表
        engine.rules = []
        context = AnomalyContext(
            anomaly_id="test", anomaly_type=AnomalyType.UNKNOWN,
            severity=Severity.P3,
            event=WorkflowEvent("e","t","w","w","t","t","t",time.time(),{}),
            expected_duration=0, actual_duration=0,
            historical_durations=[], retry_count=0,
            cluster_cpu_avg=0, worker_count=0, queue_depth=0, running_tasks=0,
            upstream_tasks=[], downstream_tasks=[], critical_path=False,
            event_time=time.time(), time_since_creation=0,
        )
        hits = engine.evaluate(context)
        assert len(hits) == 0

    def test_malformed_rule_condition(self):
        """损坏的规则条件不应导致崩溃"""
        from core.anomaly_detector import DetectionRule, RuleEngine
        engine = RuleEngine()
        # 注入一个损坏的规则
        bad_rule = DetectionRule(
            id="bad_rule",
            name="损坏规则",
            description="",
            anomaly_type="unknown",
            severity="P3",
            condition="1/0",  # 除零错误
            params={},
            actions=[],
        )
        engine.rules = [bad_rule]
        context = AnomalyContext(
            anomaly_id="test", anomaly_type=AnomalyType.UNKNOWN,
            severity=Severity.P3,
            event=WorkflowEvent("e","t","w","w","t","t","t",time.time(),{}),
            expected_duration=10, actual_duration=20,
            historical_durations=[], retry_count=0,
            cluster_cpu_avg=50, worker_count=5, queue_depth=10, running_tasks=20,
            upstream_tasks=[], downstream_tasks=[], critical_path=False,
            event_time=time.time(), time_since_creation=0,
        )
        # 不应崩溃，应返回空列表
        hits = engine.evaluate(context)
        assert len(hits) == 0


# ============================================================
# 测试: 知识库集成
# ============================================================

class TestKnowledgeBaseIntegration:
    @pytest.fixture
    def kb(self, tmp_path):
        db_path = os.path.join(tmp_path, "test_integration.db")
        return KnowledgeBase(db_path)

    def test_record_action_result(self, kb):
        """验证 record_action_result 完整流程"""
        event = WorkflowEvent(
            event_id="evt_int_001",
            event_type="task_timeout",
            workflow_id="WF-INT",
            workflow_name="集成测试工作流",
            task_id="T-INT",
            task_name="集成测试任务",
            tenant="test",
            timestamp=time.time(),
            raw_data={"duration": 3000, "expected_duration": 1800},
        )
        context = AnomalyContext(
            anomaly_id="anom_int_001",
            anomaly_type=AnomalyType.TIMEOUT,
            severity=Severity.P1,
            event=event,
            expected_duration=1800,
            actual_duration=3000,
            historical_durations=[],
            retry_count=0,
            cluster_cpu_avg=50,
            worker_count=5,
            queue_depth=10,
            running_tasks=20,
            upstream_tasks=[],
            downstream_tasks=[],
            critical_path=True,
            event_time=time.time(),
            time_since_creation=0,
        )
        plan = ActionPlan(
            plan_id="plan_int_001",
            actions=[
                ActionItem(ActionType.SCALE_WORKER, "pool", {"replicas": 2}, 1),
                ActionItem(ActionType.ADJUST_PRIORITY, "T-INT", {"priority": "HIGH"}, 2),
            ],
            risk_level=RiskLevel.LOW,
            root_cause="资源竞争",
            reasoning="模拟集成测试",
            confidence=0.85,
            rollback_plan=RollbackPlan("5min后无改善", [], 300),
        )
        result = ExecutionResult(
            plan_id="plan_int_001",
            action_results=[
                ActionResult(ActionType.SCALE_WORKER, "pool", True, duration_ms=100),
                ActionResult(ActionType.ADJUST_PRIORITY, "T-INT", True, duration_ms=50),
            ],
            overall_status="success",
        )

        entry_id = kb.record_action_result(context, plan, result, success=True)
        assert entry_id is not None
        assert entry_id.startswith("kb_")

        # 验证搜索
        results = kb.search_similar(anomaly_type=AnomalyType.TIMEOUT)
        assert len(results) >= 1
        assert results[0].root_cause == "资源竞争"

        # 验证统计
        stats = kb.get_statistics()
        assert stats["total_entries"] >= 1
        assert stats["success_rate"] > 0

    def test_search_with_keyword(self, kb):
        """关键词搜索"""
        from core.models import KnowledgeEntry
        kb.add_entry(KnowledgeEntry(
            entry_id="kw_test_001",
            anomaly_type=AnomalyType.TIMEOUT,
            root_cause="数据库连接超时",
            action_taken="{}",
            success=True,
            context_summary="测试关键词搜索",
            tags=["timeout"],
            created_at=time.time(),
        ))

        results = kb.search_similar(keyword="数据库")
        assert len(results) >= 1


# ============================================================
# 运行
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])