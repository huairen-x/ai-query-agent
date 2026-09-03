"""
核心模块单元测试
"""
import sys
import os
import time
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import (
    WorkflowEvent, AnomalyContext, AnomalyType, Severity,
    ActionPlan, ActionItem, ActionType, RiskLevel, RollbackPlan,
    ExecutionResult, ActionResult,
)
from core.config import ConfigLoader, AppConfig
from core.anomaly_detector import RuleEngine, BaselineDetector, AnomalyClassifier
from core.safety_guard import ActionValidator, RiskAssessor, CircuitBreaker, CircuitBreakerOpenError
from core.knowledge_base import KnowledgeBase
from core.orchestrator import Orchestrator


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sample_event():
    return WorkflowEvent(
        event_id="evt_test_001",
        event_type="task_timeout",
        workflow_id="WF-042",
        workflow_name="每日报表数据同步",
        task_id="T-007",
        task_name="Hive数据导入",
        tenant="data_platform",
        timestamp=time.time(),
        raw_data={
            "duration": 3000,           # 50min, > 预期30min * 1.5 = 45min
            "expected_duration": 1800,  # 30min
            "retry_count": 0,
            "cluster_cpu_avg": 50.0,    # 低CPU，避免干扰
            "worker_count": 5,
            "queue_depth": 30,          # 低队列，避免干扰
            "running_tasks": 20,
            "error_message": "",
        },
    )


@pytest.fixture
def normal_event():
    return WorkflowEvent(
        event_id="evt_normal_001",
        event_type="task_success",
        workflow_id="WF-001",
        workflow_name="正常数据同步",
        task_id="T-001",
        task_name="正常任务",
        tenant="default",
        timestamp=time.time(),
        raw_data={
            "duration": 1750,
            "expected_duration": 1800,
            "retry_count": 0,
            "cluster_cpu_avg": 45.0,
            "worker_count": 5,
            "queue_depth": 10,
            "running_tasks": 15,
            "error_message": "",
        },
    )


# ============================================================
# 测试: 数据模型
# ============================================================

class TestModels:
    def test_workflow_event_creation(self, sample_event):
        assert sample_event.event_id == "evt_test_001"
        assert sample_event.workflow_name == "每日报表数据同步"
        assert sample_event.to_json() is not None

    def test_anomaly_context_creation(self, sample_event):
        context = AnomalyContext(
            anomaly_id="anom_001",
            anomaly_type=AnomalyType.TIMEOUT,
            severity=Severity.P1,
            event=sample_event,
            expected_duration=1800,
            actual_duration=2700,
            historical_durations=[],
            retry_count=0,
            cluster_cpu_avg=88.5,
            worker_count=5,
            queue_depth=120,
            running_tasks=45,
            upstream_tasks=[],
            downstream_tasks=[],
            critical_path=False,
            event_time=time.time(),
            time_since_creation=0,
        )
        assert context.anomaly_type == AnomalyType.TIMEOUT
        assert context.severity == Severity.P1
        assert context.to_dict() is not None

    def test_action_plan_creation(self):
        actions = [
            ActionItem(
                action_type=ActionType.SCALE_WORKER,
                target="ds-worker-pool",
                params={"replicas": 2},
                order=1,
            ),
        ]
        plan = ActionPlan(
            plan_id="plan_001",
            actions=actions,
            risk_level=RiskLevel.LOW,
            root_cause="资源竞争",
            reasoning="CPU 负载过高",
            confidence=0.9,
        )
        assert len(plan.actions) == 1
        assert plan.actions[0].action_type == ActionType.SCALE_WORKER
        assert plan.to_json() is not None


# ============================================================
# 测试: 配置管理
# ============================================================

class TestConfig:
    def test_default_config(self):
        config = AppConfig()
        assert config.ds.host == "localhost"
        assert config.ds.port == 12345
        assert config.detection.timeout_ratio_threshold == 1.5
        assert config.circuit_breaker.failure_threshold == 3

    def test_config_loader(self):
        loader = ConfigLoader()
        config = loader.load()
        assert config is not None
        assert config.log_level == "INFO"


# ============================================================
# 测试: 异常检测
# ============================================================

class TestAnomalyDetection:
    def test_rule_engine_timeout(self, sample_event):
        classifier = AnomalyClassifier()
        context = classifier._build_context(sample_event)
        hit_rules = classifier.rule_engine.evaluate(context)
        assert len(hit_rules) > 0
        # 应该命中超时规则
        timeout_rules = [r for r in hit_rules if r.anomaly_type == "timeout"]
        assert len(timeout_rules) > 0

    def test_rule_engine_normal(self, normal_event):
        classifier = AnomalyClassifier()
        context = classifier._build_context(normal_event)
        hit_rules = classifier.rule_engine.evaluate(context)
        # 正常事件不应命中规则
        assert len(hit_rules) == 0

    def test_classifier_timeout(self, sample_event):
        classifier = AnomalyClassifier()
        context = classifier.classify(sample_event)
        assert context is not None
        assert context.anomaly_type == AnomalyType.TIMEOUT

    def test_classifier_normal(self, normal_event):
        classifier = AnomalyClassifier()
        context = classifier.classify(normal_event)
        # 正常事件不应被分类为异常
        assert context is None

    def test_baseline_detector(self):
        detector = BaselineDetector()
        # 更新基线
        for duration in [1750, 1820, 1690, 1780, 1710, 1850, 1760]:
            detector.update_baseline("T-001", duration)

        mean, std = detector.get_baseline("T-001")
        assert mean is not None
        assert std is not None
        assert 1700 < mean < 1900

        # 新任务应返回 None
        assert detector.get_baseline("T-999") == (None, None)


# ============================================================
# 测试: 安全护栏
# ============================================================

class TestSafetyGuard:
    def test_action_validator_low_risk(self):
        validator = ActionValidator()
        actions = [
            ActionItem(
                action_type=ActionType.SCALE_WORKER,
                target="ds-worker-pool",
                params={"replicas": 5},
                order=1,
            ),
        ]
        plan = ActionPlan(
            plan_id="plan_001",
            actions=actions,
            risk_level=RiskLevel.LOW,
            root_cause="test",
            reasoning="test",
            confidence=0.9,
        )
        violations = validator.validate(plan)
        assert len(violations) == 0

    def test_action_validator_high_risk(self):
        validator = ActionValidator()
        actions = [
            ActionItem(
                action_type=ActionType.KILL_TASK,
                target="T-001",
                params={},
                order=1,
            ),
        ]
        plan = ActionPlan(
            plan_id="plan_002",
            actions=actions,
            risk_level=RiskLevel.HIGH,
            root_cause="test",
            reasoning="test",
            confidence=0.9,
        )
        violations = validator.validate(plan)
        assert len(violations) > 0  # 高风险操作需要确认

    def test_risk_assessor(self):
        assessor = RiskAssessor()
        actions = [
            ActionItem(
                action_type=ActionType.RERUN_TASK,
                target="T-001",
                params={},
                order=1,
            ),
        ]
        plan = ActionPlan(
            plan_id="plan_003",
            actions=actions,
            risk_level=RiskLevel.LOW,
            root_cause="test",
            reasoning="test",
            confidence=0.9,
            rollback_plan=RollbackPlan(
                trigger_condition="5min后无改善",
                rollback_actions=[
                    ActionItem(
                        action_type=ActionType.NOTIFY_ONLY,
                        target="oncall",
                        params={},
                        order=1,
                    )
                ],
                timeout_seconds=300,
            ),
        )
        risk = assessor.assess(plan, 0)
        assert risk == RiskLevel.LOW

    def test_circuit_breaker(self):
        breaker = CircuitBreaker()
        assert breaker.state.value == "closed"

        # 模拟连续失败
        def failing_fn():
            raise RuntimeError("模拟失败")

        for _ in range(3):
            try:
                breaker.call(failing_fn)
            except (RuntimeError, CircuitBreakerOpenError):
                pass

        # 第 4 次应触发熔断
        with pytest.raises(CircuitBreakerOpenError):
            breaker.call(failing_fn)


# ============================================================
# 测试: 知识库
# ============================================================

class TestKnowledgeBase:
    @pytest.fixture
    def kb(self, tmp_path):
        db_path = os.path.join(tmp_path, "test_knowledge.db")
        return KnowledgeBase(db_path)

    def test_add_and_search(self, kb):
        from core.models import KnowledgeEntry
        entry = KnowledgeEntry(
            entry_id="test_001",
            anomaly_type=AnomalyType.TIMEOUT,
            root_cause="资源竞争导致超时",
            action_taken='[{"action": "scale_worker"}]',
            success=True,
            context_summary="WF-042 超时测试",
            tags=["timeout", "success"],
            created_at=time.time(),
        )
        kb.add_entry(entry)

        results = kb.search_similar(anomaly_type=AnomalyType.TIMEOUT)
        assert len(results) > 0
        assert results[0].root_cause == "资源竞争导致超时"

    def test_statistics(self, kb):
        from core.models import KnowledgeEntry
        for i in range(3):
            entry = KnowledgeEntry(
                entry_id=f"test_{i:03d}",
                anomaly_type=AnomalyType.TIMEOUT,
                root_cause=f"测试案例 {i}",
                action_taken="{}",
                success=i % 2 == 0,
                context_summary="",
                tags=[],
                created_at=time.time(),
            )
            kb.add_entry(entry)

        stats = kb.get_statistics()
        assert stats["total_entries"] == 3
        assert stats["success_rate"] > 0


# ============================================================
# 测试: 编排器
# ============================================================

class TestOrchestrator:
    def test_normal_event(self, normal_event):
        orchestrator = Orchestrator()
        result = orchestrator.process_event(normal_event)
        assert result["status"] == "normal"

    def test_timeout_event(self, sample_event):
        orchestrator = Orchestrator()
        result = orchestrator.process_event(sample_event)
        assert result["status"] in ("success", "escalated", "circuit_breaker_open")
        print(f"\n超时事件处理结果: status={result['status']}")

    def test_simulation_timeout(self):
        orchestrator = Orchestrator()
        result = orchestrator.simulate_event("timeout")
        assert result["status"] is not None
        print(f"\n模拟超时: {result['status']} - {result['message'][:50]}")

    def test_simulation_failure(self):
        orchestrator = Orchestrator()
        result = orchestrator.simulate_event("failure")
        # 空指针异常应升级到人工
        assert result["status"] in ("escalated", "success")

    def test_simulation_resource(self):
        orchestrator = Orchestrator()
        result = orchestrator.simulate_event("resource")
        assert result["status"] is not None

    def test_simulation_normal(self):
        orchestrator = Orchestrator()
        result = orchestrator.simulate_event("normal")
        assert result["status"] == "normal"

    def test_stats(self):
        orchestrator = Orchestrator()
        stats = orchestrator.get_stats()
        assert "events_received" in stats
        assert "anomalies_detected" in stats
        assert "decision_engine" in stats
        assert "knowledge_base" in stats


# ============================================================
# 运行
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])