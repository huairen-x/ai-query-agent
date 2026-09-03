"""
数据模型定义 - 企业级数据类
使用 dataclass + 类型注解，支持序列化/反序列化
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Any
from enum import Enum, auto


# ============================================================
# 枚举定义
# ============================================================

class Severity(Enum):
    """异常严重等级"""
    P0 = "P0"  # 核心链路阻断，需立即处理
    P1 = "P1"  # 非核心失败，需自动修复
    P2 = "P2"  # 性能劣化，需观察
    P3 = "P3"  # 偶发告警，仅记录


class RiskLevel(Enum):
    """操作风险等级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AnomalyType(Enum):
    """异常类型"""
    TIMEOUT = "timeout"              # 超时
    TASK_FAILURE = "task_failure"    # 任务失败
    RESOURCE_CONTENTION = "resource_contention"  # 资源竞争
    QUEUE_BACKLOG = "queue_backlog"  # 队列积压
    DATA_NOT_READY = "data_not_ready"  # 数据未就绪
    CONNECTION_TIMEOUT = "connection_timeout"  # 连接超时
    ERROR_PATTERN = "error_pattern"  # 错误码匹配
    UNKNOWN = "unknown"              # 未知


class ActionType(Enum):
    """动作类型"""
    RERUN_TASK = "rerun_task"
    ADJUST_PRIORITY = "adjust_priority"
    KILL_TASK = "kill_task"
    BLOCK_DOWNSTREAM = "block_downstream"
    SCALE_WORKER = "scale_worker"
    ADJUST_RESOURCES = "adjust_resources"
    NOTIFY_ONLY = "notify_only"
    ESCALATE = "escalate"


class CircuitBreakerState(Enum):
    """熔断器状态"""
    CLOSED = "closed"       # 正常
    OPEN = "open"           # 熔断开启
    HALF_OPEN = "half_open"  # 半开（尝试恢复）


# ============================================================
# 核心数据类
# ============================================================

@dataclass
class WorkflowEvent:
    """
    工作流事件 - 数据采集层的统一输出格式
    """
    event_id: str
    event_type: str                    # task_timeout / task_failure / ...
    workflow_id: str
    workflow_name: str
    task_id: str
    task_name: str
    tenant: str
    timestamp: float
    raw_data: dict                     # 原始数据
    extra: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class AnomalyContext:
    """
    异常上下文 - 传递给决策引擎的完整上下文
    """
    # 异常基本信息
    anomaly_id: str
    anomaly_type: AnomalyType
    severity: Severity
    event: WorkflowEvent

    # 任务历史
    expected_duration: float           # 预期耗时(秒)
    actual_duration: float             # 实际耗时(秒)
    historical_durations: list[float]  # 历史耗时分布
    retry_count: int                   # 已重试次数

    # 集群状态
    cluster_cpu_avg: float             # 集群平均 CPU 使用率
    worker_count: int                  # Worker 节点数
    queue_depth: int                   # 队列深度
    running_tasks: int                 # 运行中任务数

    # 依赖关系
    upstream_tasks: list[str]          # 上游任务
    downstream_tasks: list[str]        # 下游任务
    critical_path: bool                # 是否在核心链路上

    # 时间
    event_time: float
    time_since_creation: float

    def to_dict(self) -> dict:
        result = asdict(self)
        result['anomaly_type'] = self.anomaly_type.value
        result['severity'] = self.severity.value
        result['event'] = asdict(self.event)
        return result


@dataclass
class ActionPlan:
    """
    行动计划 - 决策引擎的输出
    """
    plan_id: str
    actions: list[ActionItem]
    risk_level: RiskLevel
    root_cause: str
    reasoning: str
    confidence: float
    rollback_plan: Optional[RollbackPlan] = None
    created_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({
            "plan_id": self.plan_id,
            "actions": [a.to_dict() for a in self.actions],
            "risk_level": self.risk_level.value,
            "root_cause": self.root_cause,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "rollback_plan": asdict(self.rollback_plan) if self.rollback_plan else None,
            "created_at": self.created_at,
        }, ensure_ascii=False)


@dataclass
class ActionItem:
    """单个动作"""
    action_type: ActionType
    target: str                          # 操作目标 (task_id / worker pool)
    params: dict                         # 操作参数
    order: int                           # 执行顺序

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type.value,
            "target": self.target,
            "params": self.params,
            "order": self.order,
        }


@dataclass
class RollbackPlan:
    """回滚预案"""
    trigger_condition: str               # 触发条件
    rollback_actions: list[ActionItem]   # 回滚动作
    timeout_seconds: int                 # 等待超时时间


@dataclass
class ExecutionResult:
    """执行结果"""
    plan_id: str
    action_results: list[ActionResult]
    overall_status: str                   # success / partial / failed
    error_message: Optional[str] = None


@dataclass
class ActionResult:
    """单个动作的执行结果"""
    action_type: ActionType
    target: str
    success: bool
    error_message: Optional[str] = None
    response_data: Optional[dict] = None
    duration_ms: float = 0.0


@dataclass
class KnowledgeEntry:
    """
    知识库条目 - 经验沉淀
    """
    entry_id: str
    anomaly_type: AnomalyType
    root_cause: str
    action_taken: str
    success: bool
    context_summary: str
    tags: list[str]
    created_at: float
    embedding: Optional[list[float]] = None  # 向量嵌入，用于语义检索