"""
决策引擎 - 智能运维 Agent 的核心大脑
LLM 推理 + 规则快速路径 + 动作规划 + 安全护栏
"""
from __future__ import annotations
import json
import time
import hashlib
import logging
import os
from typing import Optional, Callable
from dataclasses import dataclass

from .models import (
    AnomalyContext, AnomalyType, Severity, RiskLevel,
    ActionPlan, ActionItem, ActionType, RollbackPlan,
    ExecutionResult, ActionResult, CircuitBreakerState,
    KnowledgeEntry,
)
from .safety_guard import (
    ActionValidator, RiskAssessor, CircuitBreaker, CircuitBreakerOpenError
)
from .knowledge_base import KnowledgeBase
from .config import get_config, LLMConfig

import re

logger = logging.getLogger("ops-agent.decision")


# ============================================================
# LLM 客户端抽象
# ============================================================

class LLMClient:
    """
    LLM 客户端抽象层
    支持多 provider: deepseek / openai / qwen
    可通过环境变量 APP_LLM_PROVIDER 切换
    """

    def __init__(self, config: LLMConfig):
        self._config = config
        self._client = None
        self._init_client()

    def _init_client(self):
        """初始化 LLM 客户端"""
        provider = self._config.provider
        try:
            if provider in ("deepseek", "openai", "qwen"):
                from openai import OpenAI
                api_base = self._config.api_base or {
                    "deepseek": "https://api.deepseek.com",
                    "openai": "https://api.openai.com/v1",
                    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                }.get(provider, "")

                self._client = OpenAI(
                    api_key=self._config.api_key,
                    base_url=api_base,
                    timeout=self._config.timeout,
                )
                logger.info(f"LLM 客户端初始化: provider={provider}, model={self._config.model}")
            else:
                logger.warning(f"不支持的 LLM provider: {provider}, 使用模拟模式")
                self._client = None
        except ImportError:
            logger.warning("openai 库未安装, 使用模拟 LLM 模式")
            self._client = None
        except Exception as e:
            logger.warning(f"LLM 客户端初始化失败: {e}, 使用模拟模式")
            self._client = None

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """调用 LLM 对话"""
        if self._client is None:
            return self._mock_reason(system_prompt, user_prompt)

        try:
            resp = self._client.chat.completions.create(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self._config.temperature,
                max_tokens=self._config.max_tokens,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}, 降级到模拟推理")
            return self._mock_reason(system_prompt, user_prompt)

    def _mock_reason(self, system_prompt: str, user_prompt: str) -> str:
        """
        模拟 LLM 推理（无外部依赖时的降级方案）
        基于规则模板生成决策
        """
        logger.info("使用模拟 LLM 推理（降级模式）")

        # 从 prompt 中提取关键信息
        context = json.loads(user_prompt) if user_prompt.startswith("{") else {}

        anomaly_type = context.get("anomaly_type", "unknown")
        severity = context.get("severity", "P3")
        task_name = context.get("event", {}).get("task_name", "unknown")
        workflow_name = context.get("event", {}).get("workflow_name", "unknown")
        retry_count = context.get("retry_count", 0)
        cluster_cpu = context.get("cluster_cpu_avg", 0)
        queue_depth = context.get("queue_depth", 0)
        critical_path = context.get("critical_path", False)

        # 基于规则的决策模板
        if anomaly_type == "timeout":
            if cluster_cpu > 85 or queue_depth > 100:
                root_cause = "资源竞争导致任务执行缓慢"
                actions = [
                    {"action_type": "scale_worker", "target": "ds-worker-pool", "params": {"replicas": 2}, "order": 1},
                    {"action_type": "adjust_priority", "target": task_name, "params": {"priority": "HIGH"}, "order": 2},
                ]
                risk = "low"
            else:
                root_cause = "数据量异常增长导致耗时增加"
                actions = [
                    {"action_type": "rerun_task", "target": task_name, "params": {"timeout": 3600}, "order": 1},
                ]
                risk = "low"

        elif anomaly_type == "task_failure":
            error_msg = context.get("event", {}).get("raw_data", {}).get("error_message", "")
            if "NullPointer" in error_msg or "OOM" in error_msg:
                root_cause = f"代码缺陷: {error_msg[:100]}"
                actions = [
                    {"action_type": "notify_only", "target": "oncall", "params": {"message": error_msg}, "order": 1},
                ]
                risk = "high"
            elif retry_count < 3:
                root_cause = "临时性异常，可通过重试恢复"
                actions = [
                    {"action_type": "rerun_task", "target": task_name, "params": {"retry_delay": 30}, "order": 1},
                ]
                risk = "low"
            else:
                root_cause = f"持续失败(已重试{retry_count}次)，需人工介入"
                actions = [
                    {"action_type": "block_downstream", "target": task_name, "params": {}, "order": 1},
                    {"action_type": "escalate", "target": "oncall", "params": {"severity": severity}, "order": 2},
                ]
                risk = "critical"

        elif anomaly_type == "resource_contention":
            root_cause = "集群资源不足"
            actions = [
                {"action_type": "scale_worker", "target": "ds-worker-pool", "params": {"replicas": 3}, "order": 1},
            ]
            risk = "low"

        elif anomaly_type == "queue_backlog":
            root_cause = "任务队列积压"
            actions = [
                {"action_type": "scale_worker", "target": "ds-worker-pool", "params": {"replicas": 2}, "order": 1},
            ]
            risk = "low"

        else:
            root_cause = f"未知异常类型: {anomaly_type}"
            actions = [
                {"action_type": "notify_only", "target": "oncall", "params": {"message": f"未知异常: {anomaly_type}"}, "order": 1},
            ]
            risk = "medium"

        return json.dumps({
            "root_cause": root_cause,
            "reasoning": f"异常类型={anomaly_type}, severity={severity}, "
                         f"workflow={workflow_name}, task={task_name}, "
                         f"retry_count={retry_count}, cpu={cluster_cpu}%, queue={queue_depth}",
            "confidence": 0.85 if risk != "critical" else 0.65,
            "actions": actions,
            "risk_level": risk,
            "rollback_plan": {
                "trigger_condition": "30min后无改善",
                "rollback_actions": [
                    {"action_type": "scale_worker", "target": "ds-worker-pool", "params": {"replicas": -2}, "order": 1}
                ] if risk == "low" else [],
                "timeout_seconds": 1800,
            } if risk == "low" else None,
        }, ensure_ascii=False)


# ============================================================
# 决策引擎
# ============================================================

class DecisionEngine:
    """
    决策引擎 - 智能运维 Agent 的核心
    流程: 上下文聚合 → 根因推理 → 动作规划 → 安全校验 → 执行 → 反馈
    """

    def __init__(
        self,
        action_executor: Optional[Callable] = None,
        knowledge_base: Optional[KnowledgeBase] = None,
    ):
        self._llm = LLMClient(get_config().llm)
        self._validator = ActionValidator()
        self._risk_assessor = RiskAssessor()
        self._circuit_breaker = CircuitBreaker()
        self._knowledge_base = knowledge_base or KnowledgeBase()
        self._action_executor = action_executor
        self._metrics = {
            "total_decisions": 0,
            "auto_resolved": 0,
            "escalated": 0,
            "circuit_breaker_triggered": 0,
        }
        logger.info("决策引擎初始化完成")

    def handle_anomaly(self, context: AnomalyContext) -> dict:
        """
        处理异常 - 主入口
        返回处理结果摘要
        """
        self._metrics["total_decisions"] += 1
        start_time = time.time()

        try:
            # 1. 检查熔断器状态
            if self._circuit_breaker.state == CircuitBreakerState.OPEN:
                self._metrics["circuit_breaker_triggered"] += 1
                return self._build_response(
                    status="circuit_breaker_open",
                    message="熔断器已开启，自动降级为只告警",
                    context=context,
                )

            # 2. 查询相似案例（快速路径）
            similar = self._search_similar_cases(context)

            # 3. LLM 推理生成行动计划
            plan = self._reason_and_plan(context, similar)

            # 4. 安全校验
            violations = self._validator.validate(plan)
            risk_level = self._risk_assessor.assess(plan, len(violations))
            plan.risk_level = risk_level

            # 5. 根据风险等级决策
            if risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH) or violations:
                return self._handle_high_risk(context, plan, violations)

            # 6. 执行行动计划
            result = self._execute_plan(plan)

            # 7. 记录结果
            success = result.overall_status == "success"
            self._knowledge_base.record_action_result(context, plan, result, success)
            self._risk_assessor.record_result(
                plan.actions[0].action_type.value, success
            )

            if success:
                self._metrics["auto_resolved"] += 1

            elapsed = time.time() - start_time
            logger.info(
                f"异常处理完成: anomaly_id={context.anomaly_id}, "
                f"status={result.overall_status}, elapsed={elapsed:.1f}s"
            )

            return self._build_response(
                status=result.overall_status,
                message=self._summarize_result(context, plan, result),
                context=context,
                plan=plan,
                result=result,
                elapsed=elapsed,
            )

        except CircuitBreakerOpenError as e:
            self._metrics["circuit_breaker_triggered"] += 1
            return self._build_response(
                status="circuit_breaker_open",
                message=str(e),
                context=context,
            )
        except Exception as e:
            logger.error(f"决策引擎异常: {e}", exc_info=True)
            return self._build_response(
                status="error",
                message=f"决策引擎内部错误: {e}",
                context=context,
            )

    def _reason_and_plan(
        self,
        context: AnomalyContext,
        similar_cases: list[KnowledgeEntry],
    ) -> ActionPlan:
        """LLM 推理并生成行动计划"""
        # 构建 LLM 输入
        system_prompt = """你是一个智能运维 Agent，负责处理 DolphinScheduler 工作流调度异常。
你的任务是根据异常上下文，分析根因并制定行动计划。

分析原则：
1. 先判断异常原因（资源不足？代码bug？数据延迟？临时抖动？）
2. 根据原因选择最合适的动作
3. 动作要有可回滚性
4. 核心链路异常优先处理

输出格式为 JSON，包含:
- root_cause: 根因分析
- reasoning: 推理过程
- confidence: 置信度(0-1)
- actions: 动作列表 [{action_type, target, params, order}]
- risk_level: low/medium/high/critical
- rollback_plan: 回滚预案

可用动作类型:
- rerun_task: 重跑任务
- adjust_priority: 调整优先级
- kill_task: 终止任务
- block_downstream: 阻断下游
- scale_worker: 扩缩容 Worker
- adjust_resources: 调整资源配额
- notify_only: 仅通知
- escalate: 升级到人工"""

        # 构建用户 prompt
        user_prompt = json.dumps({
            "anomaly_context": context.to_dict(),
            "similar_cases": [
                {
                    "root_cause": c.root_cause,
                    "action_taken": c.action_taken,
                    "success": c.success,
                }
                for c in similar_cases[:3]
            ],
        }, ensure_ascii=False)

        # 调用 LLM
        llm_response = self._llm.chat(system_prompt, user_prompt)

        # 解析 LLM 输出
        plan_data = self._parse_llm_response(llm_response, context)

        # 构建 ActionPlan
        actions = [
            ActionItem(
                action_type=ActionType(a["action_type"]),
                target=a["target"],
                params=a.get("params", {}),
                order=a.get("order", i + 1),
            )
            for i, a in enumerate(plan_data.get("actions", []))
        ]

        rollback = plan_data.get("rollback_plan")
        rollback_plan = None
        if rollback and rollback.get("rollback_actions"):
            rollback_plan = RollbackPlan(
                trigger_condition=rollback.get("trigger_condition", "30min后无改善"),
                rollback_actions=[
                    ActionItem(
                        action_type=ActionType(ra["action_type"]),
                        target=ra["target"],
                        params=ra.get("params", {}),
                        order=ra.get("order", i + 1),
                    )
                    for i, ra in enumerate(rollback["rollback_actions"])
                ],
                timeout_seconds=rollback.get("timeout_seconds", 1800),
            )

        raw = f"{context.anomaly_id}_{time.time()}"
        plan_id = f"plan_{hashlib.md5(raw.encode()).hexdigest()[:12]}"

        return ActionPlan(
            plan_id=plan_id,
            actions=actions,
            risk_level=RiskLevel(plan_data.get("risk_level", "medium")),
            root_cause=plan_data.get("root_cause", "未知"),
            reasoning=plan_data.get("reasoning", ""),
            confidence=plan_data.get("confidence", 0.5),
            rollback_plan=rollback_plan,
        )

    def _parse_llm_response(self, response: str, context: AnomalyContext) -> dict:
        """解析 LLM 响应，提取结构化数据"""
        try:
            # 尝试直接解析 JSON
            return json.loads(response)
        except json.JSONDecodeError:
            # 尝试从 markdown 代码块中提取
            match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass

            # 解析失败，返回基于规则的默认决策
            logger.warning("LLM 响应解析失败, 使用规则降级")
            return self._default_decision(context)

    def _default_decision(self, context: AnomalyContext) -> dict:
        """LLM 降级时的默认决策"""
        if context.anomaly_type == AnomalyType.TIMEOUT:
            return {
                "root_cause": "任务超时(规则降级)",
                "reasoning": "LLM 解析失败，使用规则降级决策",
                "confidence": 0.6,
                "actions": [{"action_type": "rerun_task", "target": context.event.task_id, "params": {}, "order": 1}],
                "risk_level": "low",
            }
        return {
            "root_cause": f"未知异常(规则降级): {context.anomaly_type.value}",
            "reasoning": "LLM 解析失败，使用规则降级决策",
            "confidence": 0.4,
            "actions": [{"action_type": "notify_only", "target": "oncall", "params": {}, "order": 1}],
            "risk_level": "medium",
        }

    def _search_similar_cases(self, context: AnomalyContext) -> list[KnowledgeEntry]:
        """搜索相似案例"""
        return self._knowledge_base.search_similar(
            anomaly_type=context.anomaly_type,
            keyword=context.event.task_name,
            limit=3,
        )

    def _handle_high_risk(
        self,
        context: AnomalyContext,
        plan: ActionPlan,
        violations: list[str],
    ) -> dict:
        """处理高风险决策 - 升级到人工"""
        self._metrics["escalated"] += 1
        logger.warning(
            f"高风险操作, 需人工确认: risk={plan.risk_level.value}, "
            f"violations={violations}"
        )
        return self._build_response(
            status="escalated",
            message=f"高风险操作需人工确认: {plan.root_cause}",
            context=context,
            plan=plan,
            violations=violations,
        )

    def _execute_plan(self, plan: ActionPlan) -> ExecutionResult:
        """执行行动计划"""
        if self._action_executor is None:
            logger.info(f"模拟执行计划: {plan.plan_id}, actions={len(plan.actions)}")
            return ExecutionResult(
                plan_id=plan.plan_id,
                action_results=[
                    ActionResult(
                        action_type=a.action_type,
                        target=a.target,
                        success=True,
                        duration_ms=100,
                    )
                    for a in plan.actions
                ],
                overall_status="success",
            )

        # 熔断保护执行
        return self._circuit_breaker.call(
            self._action_executor, plan
        )

    def _build_response(
        self,
        status: str,
        message: str,
        context: Optional[AnomalyContext] = None,
        plan: Optional[ActionPlan] = None,
        result: Optional[ExecutionResult] = None,
        violations: Optional[list[str]] = None,
        elapsed: Optional[float] = None,
    ) -> dict:
        """构建统一响应格式"""
        return {
            "status": status,
            "message": message,
            "anomaly_id": context.anomaly_id if context else None,
            "anomaly_type": context.anomaly_type.value if context else None,
            "severity": context.severity.value if context else None,
            "plan": {
                "plan_id": plan.plan_id,
                "actions": [a.action_type.value for a in plan.actions],
                "root_cause": plan.root_cause,
                "risk_level": plan.risk_level.value,
            } if plan else None,
            "execution": {
                "status": result.overall_status,
                "action_results": [
                    {"action": r.action_type.value, "target": r.target, "success": r.success}
                    for r in (result.action_results if result else [])
                ],
            } if result else None,
            "violations": violations or [],
            "elapsed_seconds": round(elapsed, 2) if elapsed else None,
            "timestamp": time.time(),
        }

    @staticmethod
    def _summarize_result(
        context: AnomalyContext,
        plan: ActionPlan,
        result: ExecutionResult,
    ) -> str:
        """生成处理摘要"""
        action_summary = ", ".join(
            f"{r.action_type.value}({r.target})"
            for r in result.action_results
        )
        return (
            f"异常自动处理完成: [{context.anomaly_type.value}] "
            f"{context.event.workflow_name}/{context.event.task_name} "
            f"→ 根因: {plan.root_cause} "
            f"→ 动作: {action_summary} "
            f"→ 结果: {result.overall_status}"
        )

    def get_metrics(self) -> dict:
        """获取引擎指标"""
        return {
            **self._metrics,
            "circuit_breaker": self._circuit_breaker.get_metrics(),
            "knowledge_base": self._knowledge_base.get_statistics(),
        }