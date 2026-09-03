"""
安全护栏模块 - 动作验证 + 风险评估 + 熔断器
确保 Agent 不会执行危险操作，具备自我保护能力
"""
from __future__ import annotations
import time
import logging
from collections import defaultdict
from typing import Optional

from .models import (
    ActionPlan, ActionItem, ActionType, RiskLevel,
    CircuitBreakerState, ExecutionResult
)
from .config import get_config, CircuitBreakerConfig

logger = logging.getLogger("ops-agent.safety")


# ============================================================
# 动作验证器
# ============================================================

class ActionValidator:
    """
    动作验证器 - 第一道安全防线
    检查: 白名单、频次限制、灰度范围、影响范围
    """

    # 高风险操作，需人工审批
    HIGH_RISK_ACTIONS = {
        ActionType.KILL_TASK,
        ActionType.BLOCK_DOWNSTREAM,
        ActionType.ESCALATE,
    }

    # 低风险操作，可自动执行
    LOW_RISK_ACTIONS = {
        ActionType.RERUN_TASK,
        ActionType.ADJUST_PRIORITY,
        ActionType.SCALE_WORKER,
        ActionType.ADJUST_RESOURCES,
        ActionType.NOTIFY_ONLY,
    }

    def __init__(self):
        # 操作频次计数器: (action_type, target) -> (count, window_start)
        self._rate_limits: dict[tuple, tuple[int, float]] = defaultdict(
            lambda: (0, time.time())
        )
        # 每日操作计数器
        self._daily_counts: dict[ActionType, int] = defaultdict(int)
        self._last_daily_reset = time.time()

        logger.info("动作验证器初始化完成")

    def validate(self, plan: ActionPlan) -> list[str]:
        """
        验证行动计划，返回违规列表
        空列表 = 全部通过
        """
        violations = []

        for action in plan.actions:
            violations.extend(self._validate_action(action, plan))

        return violations

    def _validate_action(self, action: ActionItem, plan: ActionPlan) -> list[str]:
        """验证单个动作"""
        violations = []

        # 1. 检查动作类型是否在白名单
        if action.action_type not in self.LOW_RISK_ACTIONS and \
           action.action_type not in self.HIGH_RISK_ACTIONS:
            violations.append(f"未知动作类型: {action.action_type.value}")

        # 2. 高风险操作需标记
        if action.action_type in self.HIGH_RISK_ACTIONS:
            violations.append(
                f"高风险操作需人工确认: {action.action_type.value} -> {action.target}"
            )

        # 3. 频次限制
        key = (action.action_type, action.target)
        count, window_start = self._rate_limits[key]
        if time.time() - window_start > 3600:
            # 重置窗口
            self._rate_limits[key] = (1, time.time())
        else:
            self._rate_limits[key] = (count + 1, window_start)
            if count >= 5:
                violations.append(
                    f"操作频次超限: {action.action_type.value} -> {action.target}, "
                    f"1小时内已执行 {count} 次"
                )

        # 4. 每日操作总量限制
        self._daily_cleanup()
        self._daily_counts[action.action_type] += 1
        if self._daily_counts[action.action_type] > 50:
            violations.append(
                f"每日操作量超限: {action.action_type.value}, "
                f"今日已执行 {self._daily_counts[action.action_type]} 次"
            )

        # 5. 扩缩容限制
        if action.action_type == ActionType.SCALE_WORKER:
            config = get_config().k8s
            new_replicas = action.params.get("replicas", 0)
            if new_replicas > config.max_replicas:
                violations.append(
                    f"扩缩容超出上限: {new_replicas} > {config.max_replicas}"
                )
            if new_replicas < config.min_replicas:
                violations.append(
                    f"扩缩容低于下限: {new_replicas} < {config.min_replicas}"
                )

        return violations

    def _daily_cleanup(self):
        """每日计数器重置"""
        now = time.time()
        if now - self._last_daily_reset > 86400:
            self._daily_counts.clear()
            self._last_daily_reset = now


# ============================================================
# 风险评估器
# ============================================================

class RiskAssessor:
    """
    风险评估器 - 第二道安全防线
    评估操作的影响范围、回滚成本、历史成功率
    """

    def __init__(self):
        # 历史成功率: action_type -> (success, total)
        self._history: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
        logger.info("风险评估器初始化完成")

    def assess(self, plan: ActionPlan, violation_count: int) -> RiskLevel:
        """
        评估风险等级
        基于: 操作类型 + 历史成功率 + 违规次数 + 影响范围
        """
        score = 0.0

        # 1. 操作类型基础分
        action_types = set(a.action_type for a in plan.actions)
        for at in action_types:
            if at in ActionValidator.HIGH_RISK_ACTIONS:
                score += 40
            elif at == ActionType.SCALE_WORKER:
                score += 20
            elif at == ActionType.ADJUST_PRIORITY:
                score += 15
            elif at == ActionType.RERUN_TASK:
                score += 5

        # 2. 历史成功率
        for action in plan.actions:
            success, total = self._history[action.action_type.value]
            if total > 0:
                rate = success / total
                if rate < 0.5:
                    score += 30  # 历史成功率低，风险高
                elif rate < 0.8:
                    score += 15

        # 3. 违规次数
        score += violation_count * 10

        # 4. 多个动作组合增加风险
        if len(plan.actions) > 2:
            score += 10

        # 5. 没有回滚预案
        if plan.rollback_plan is None:
            score += 15

        # 映射到风险等级
        if score >= 60:
            return RiskLevel.CRITICAL
        elif score >= 40:
            return RiskLevel.HIGH
        elif score >= 20:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW

    def record_result(self, action_type: str, success: bool):
        """记录操作结果，用于后续风险评估"""
        s, t = self._history[action_type]
        self._history[action_type] = (s + (1 if success else 0), t + 1)


# ============================================================
# 熔断器
# ============================================================

class CircuitBreaker:
    """
    熔断器 - 第三道安全防线（自我保护）
    状态机: CLOSED -> OPEN -> HALF_OPEN -> CLOSED
    """

    def __init__(self):
        config = get_config().circuit_breaker
        self._config = config
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._half_open_attempts = 0
        self._total_failures = 0
        self._total_successes = 0
        logger.info("熔断器初始化完成, 阈值: threshold=%s, timeout=%ss",
                     config.failure_threshold, config.recovery_timeout)

    @property
    def state(self) -> CircuitBreakerState:
        """当前状态（自动检查是否需要从 OPEN -> HALF_OPEN）"""
        if self._state == CircuitBreakerState.OPEN:
            if time.time() - self._last_failure_time > self._config.recovery_timeout:
                self._state = CircuitBreakerState.HALF_OPEN
                self._half_open_attempts = 0
                logger.info("熔断器进入半开状态, 尝试恢复")
        return self._state

    def call(self, action_fn, *args, **kwargs):
        """
        熔断保护调用
        在 CLOSED/HALF_OPEN 状态下执行，OPEN 状态直接拒绝
        """
        current_state = self.state

        if current_state == CircuitBreakerState.OPEN:
            logger.warning("熔断器开启, 拒绝执行")
            raise CircuitBreakerOpenError("熔断器已开启，拒绝执行操作")

        try:
            result = action_fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self):
        """成功回调"""
        self._total_successes += 1
        if self._state == CircuitBreakerState.HALF_OPEN:
            self._half_open_attempts += 1
            if self._half_open_attempts >= self._config.half_open_max_retries:
                self._reset()
                logger.info("熔断器恢复: HALF_OPEN -> CLOSED")

    def _on_failure(self):
        """失败回调"""
        self._total_failures += 1
        self._last_failure_time = time.time()

        if self._state == CircuitBreakerState.HALF_OPEN:
            self._state = CircuitBreakerState.OPEN
            logger.warning("熔断器半开状态失败, 回到 OPEN")
        else:
            self._failure_count += 1
            if self._failure_count >= self._config.failure_threshold:
                self._state = CircuitBreakerState.OPEN
                logger.warning(
                    f"熔断器触发! 连续失败 {self._failure_count} 次, "
                    f"熔断 {self._config.recovery_timeout}s"
                )

    def _reset(self):
        """重置熔断器"""
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._half_open_attempts = 0

    def get_metrics(self) -> dict:
        """获取熔断器指标"""
        return {
            "state": self.state.value,
            "failure_count": self._failure_count,
            "total_failures": self._total_failures,
            "total_successes": self._total_successes,
            "last_failure_time": self._last_failure_time,
            "threshold": self._config.failure_threshold,
            "recovery_timeout": self._config.recovery_timeout,
        }


class CircuitBreakerOpenError(Exception):
    """熔断器开启异常"""
    pass