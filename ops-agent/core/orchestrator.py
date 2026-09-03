"""
编排器 - 打通全流程的主控模块
将数据采集 → 异常检测 → 决策引擎 → 执行 → 反馈 串联起来
"""
from __future__ import annotations
import time
import logging
import json
import os
from typing import Optional, Callable
from threading import Thread, Event

from .models import WorkflowEvent, AnomalyContext, ExecutionResult
from .anomaly_detector import AnomalyClassifier
from .decision_engine import DecisionEngine
from .knowledge_base import KnowledgeBase
from .config import get_config

import hashlib

logger = logging.getLogger("ops-agent.orchestrator")


class Orchestrator:
    """
    编排器 - 全流程主控
    职责: 接收事件 → 异常检测 → 决策 → 执行 → 反馈
    """

    def __init__(
        self,
        action_executor: Optional[Callable] = None,
    ):
        self._classifier = AnomalyClassifier()
        self._knowledge_base = KnowledgeBase()
        self._decision_engine = DecisionEngine(
            action_executor=action_executor,
            knowledge_base=self._knowledge_base,
        )
        self._running = False
        self._stop_event = Event()
        self._stats = {
            "events_received": 0,
            "anomalies_detected": 0,
            "auto_resolved": 0,
            "escalated": 0,
            "errors": 0,
        }
        logger.info("编排器初始化完成")

    # ============================================================
    # 事件处理
    # ============================================================

    def process_event(self, event: WorkflowEvent) -> dict:
        """
        处理单个工作流事件
        这是外部调用的主要入口
        """
        self._stats["events_received"] += 1
        start_time = time.time()

        try:
            # 1. 异常检测
            context = self._classifier.classify(event)

            if context is None:
                # 正常事件，不处理
                return {
                    "status": "normal",
                    "message": "事件正常，无需处理",
                    "event_id": event.event_id,
                    "elapsed_ms": round((time.time() - start_time) * 1000, 2),
                }

            # 2. 异常事件，进入决策引擎
            self._stats["anomalies_detected"] += 1
            logger.info(
                f"异常检测命中: [{context.severity.value}] "
                f"{context.anomaly_type.value} - "
                f"{event.workflow_name}/{event.task_name}"
            )

            result = self._decision_engine.handle_anomaly(context)

            # 更新统计
            if result["status"] == "success":
                self._stats["auto_resolved"] += 1
            elif result["status"] == "escalated":
                self._stats["escalated"] += 1

            result["elapsed_ms"] = round((time.time() - start_time) * 1000, 2)
            return result

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"事件处理异常: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"处理异常: {e}",
                "event_id": event.event_id,
                "elapsed_ms": round((time.time() - start_time) * 1000, 2),
            }

    def process_events_batch(self, events: list[WorkflowEvent]) -> list[dict]:
        """批量处理事件"""
        return [self.process_event(e) for e in events]

    # ============================================================
    # 模拟工作流
    # ============================================================

    def simulate_event(self, scenario: str = "timeout") -> dict:
        """
        模拟事件注入（用于测试和演示）
        scenario: timeout / failure / resource / queue / normal
        """
        import hashlib as _hashlib

        base = {
            "workflow_id": "WF-042",
            "workflow_name": "每日报表数据同步",
            "task_id": "T-007",
            "task_name": "Hive数据导入",
            "tenant": "data_platform",
            "timestamp": time.time(),
        }

        scenarios = {
            "timeout": {
                **base,
                "event_id": f"evt_{hashlib.md5(str(time.time()).encode()).hexdigest()[:12]}",
                "event_type": "task_timeout",
                "raw_data": {
                    "duration": 2700,          # 45min
                    "expected_duration": 1800,  # 30min
                    "retry_count": 0,
                    "cluster_cpu_avg": 88.5,
                    "worker_count": 5,
                    "queue_depth": 120,
                    "running_tasks": 45,
                    "error_message": "",
                },
            },
            "failure": {
                **base,
                "event_id": f"evt_{hashlib.md5(str(time.time()).encode()).hexdigest()[:12]}",
                "event_type": "task_failure",
                "raw_data": {
                    "duration": 300,
                    "expected_duration": 300,
                    "retry_count": 2,
                    "cluster_cpu_avg": 45.0,
                    "worker_count": 5,
                    "queue_depth": 12,
                    "running_tasks": 20,
                    "error_message": "java.lang.NullPointerException: null",
                },
            },
            "resource": {
                **base,
                "event_id": f"evt_{hashlib.md5(str(time.time()).encode()).hexdigest()[:12]}",
                "event_type": "cluster_high_load",
                "raw_data": {
                    "duration": 600,
                    "expected_duration": 600,
                    "retry_count": 0,
                    "cluster_cpu_avg": 93.2,
                    "worker_count": 5,
                    "queue_depth": 350,
                    "running_tasks": 78,
                    "error_message": "",
                },
            },
            "normal": {
                **base,
                "event_id": f"evt_{hashlib.md5(str(time.time()).encode()).hexdigest()[:12]}",
                "event_type": "task_success",
                "raw_data": {
                    "duration": 1750,
                    "expected_duration": 1800,
                    "retry_count": 0,
                    "cluster_cpu_avg": 45.0,
                    "worker_count": 5,
                    "queue_depth": 10,
                    "running_tasks": 15,
                    "error_message": "",
                },
            },
        }

        data = scenarios.get(scenario, scenarios["timeout"])
        event = WorkflowEvent(**data)
        return self.process_event(event)

    # ============================================================
    # 统计 & 管理
    # ============================================================

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            **self._stats,
            "decision_engine": self._decision_engine.get_metrics(),
            "knowledge_base": self._knowledge_base.get_statistics(),
        }

    def get_knowledge_base(self) -> KnowledgeBase:
        return self._knowledge_base