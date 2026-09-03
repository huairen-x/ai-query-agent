"""
异常检测引擎 - 规则检测 + 基线检测 + 关联分析
企业级设计：可扩展检测器链，支持动态加载
"""
from __future__ import annotations
import json
import time
import math
import hashlib
import logging
import statistics
from typing import Optional
from dataclasses import dataclass
from collections import defaultdict

from .models import (
    WorkflowEvent, AnomalyContext, AnomalyType, Severity
)
from .config import get_config, DetectionConfig

import ast
import re

logger = logging.getLogger("ops-agent.detector")


# ============================================================
# 规则引擎
# ============================================================

@dataclass
class DetectionRule:
    """检测规则"""
    id: str
    name: str
    description: str
    anomaly_type: str
    severity: str
    condition: str       # 条件表达式 (eval 安全子集)
    params: dict
    actions: list[str]

    @classmethod
    def from_dict(cls, data: dict) -> "DetectionRule":
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            anomaly_type=data["anomaly_type"],
            severity=data["severity"],
            condition=data["condition"],
            params=data.get("params", {}),
            actions=data.get("actions", []),
        )


class RuleEngine:
    """
    规则引擎 - 确定性异常检测
    支持条件表达式匹配，快速路径
    """

    def __init__(self, rules_path: Optional[str] = None):
        self.rules: list[DetectionRule] = []
        self._load_rules(rules_path)
        logger.info(f"规则引擎初始化完成, 加载 {len(self.rules)} 条规则")

    def _load_rules(self, rules_path: Optional[str] = None):
        """加载规则配置"""
        import os
        import json

        if not rules_path:
            # 优先尝试 JSON，再尝试 YAML
            base = os.path.join(os.path.dirname(__file__), "..", "config")
            # 检查 PyYAML 是否可用
            try:
                import yaml
                has_yaml = True
            except ImportError:
                has_yaml = False
            # 有 YAML 时优先用 yaml 格式，否则用 json
            extensions = (".yaml", ".json") if has_yaml else (".json", ".yaml")
            for ext in extensions:
                candidate = os.path.join(base, f"rules{ext}")
                if os.path.exists(candidate):
                    rules_path = candidate
                    break

        if not rules_path or not os.path.exists(rules_path):
            logger.warning(f"规则文件不存在: {rules_path}")
            return

        with open(rules_path, "r", encoding="utf-8") as f:
            content = f.read()

        data = None
        if rules_path.endswith(".json"):
            data = json.loads(content)
        else:
            import yaml
            data = yaml.safe_load(content)

        if data is None:
            return

        for rule_data in data.get("rules", []):
            self.rules.append(DetectionRule.from_dict(rule_data))

    def evaluate(self, context: AnomalyContext) -> list[DetectionRule]:
        """
        评估所有规则，返回命中的规则列表
        使用安全的条件匹配逻辑
        """
        hit_rules = []
        for rule in self.rules:
            if self._match_rule(rule, context):
                hit_rules.append(rule)
                logger.info(f"规则命中: {rule.id} ({rule.name}), severity={rule.severity}")
        return hit_rules

    def _match_rule(self, rule: DetectionRule, context: AnomalyContext) -> bool:
        """匹配单条规则（安全的条件表达式求值）"""
        try:
            # 构建安全求值环境
            env = {
                "actual_duration": context.actual_duration,
                "expected_duration": context.expected_duration,
                "retry_count": context.retry_count,
                "cluster_cpu_avg": context.cluster_cpu_avg,
                "queue_depth": context.queue_depth,
                "worker_count": context.worker_count,
                "running_tasks": context.running_tasks,
                "timeout_ratio_threshold": get_config().detection.timeout_ratio_threshold,
                "retry_threshold": get_config().detection.retry_threshold,
                "cpu_high_threshold": get_config().detection.cpu_high_threshold,
                "queue_depth_threshold": get_config().detection.queue_depth_threshold,
                "error_message": (context.event.raw_data.get("error_message") or ""),
            }

            # 处理 contains 运算符 → Python in 运算符
            # 例如: "error_message contains 'NullPointer'" → "'NullPointer' in error_message"
            condition = re.sub(
                r"(\w+)\s+contains\s+'([^']*)'",
                r"'\2' in \1",
                rule.condition
            )

            # 安全 eval - 只允许布尔表达式
            allowed_names = set(env.keys())
            tree = ast.parse(condition, mode="eval")
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    if node.id not in allowed_names:
                        logger.warning(f"规则 {rule.id} 包含非法变量: {node.id}")
                        return False

            result = eval(condition, {"__builtins__": {}}, env)
            return bool(result)
        except Exception as e:
            logger.error(f"规则匹配异常: rule_id={rule.id}, error={e}")
            return False


# ============================================================
# 基线检测器
# ============================================================

class BaselineDetector:
    """
    基线检测器 - 基于统计的异常检测
    使用历史数据建立基线，检测偏离
    """

    def __init__(self):
        # 任务ID -> 历史耗时列表
        self._baselines: dict[str, list[float]] = defaultdict(list)
        # 任务ID -> 最后一次更新基线的时间
        self._last_update: dict[str, float] = {}
        logger.info("基线检测器初始化完成")

    def update_baseline(self, task_id: str, duration: float):
        """更新基线数据"""
        self._baselines[task_id].append(duration)
        # 只保留最近 N 个样本
        max_samples = get_config().detection.min_samples_for_baseline * 10
        if len(self._baselines[task_id]) > max_samples:
            self._baselines[task_id] = self._baselines[task_id][-max_samples:]
        self._last_update[task_id] = time.time()

    def get_baseline(self, task_id: str) -> tuple[Optional[float], Optional[float]]:
        """
        获取基线: (mean, std)
        如果样本不足返回 None
        """
        samples = self._baselines.get(task_id, [])
        min_samples = get_config().detection.min_samples_for_baseline
        if len(samples) < min_samples:
            return None, None
        return statistics.mean(samples), statistics.stdev(samples)

    def detect(self, context: AnomalyContext) -> Optional[AnomalyType]:
        """
        基线检测
        返回检测到的异常类型，无异常返回 None
        """
        if not get_config().detection.enable_ml_detection:
            return None

        mean, std = self.get_baseline(context.event.task_id)
        if mean is None or std is None or std == 0:
            return None

        threshold = get_config().detection.baseline_std_threshold
        deviation = (context.actual_duration - mean) / std if std > 0 else 0

        if deviation > threshold:
            logger.info(
                f"基线检测异常: task={context.event.task_id}, "
                f"actual={context.actual_duration:.1f}s, mean={mean:.1f}s, "
                f"std={std:.1f}s, deviation={deviation:.1f}σ"
            )
            return AnomalyType.TIMEOUT

        return None

    def get_expected_duration(self, task_id: str) -> Optional[float]:
        """获取预期耗时（中位数，更鲁棒）"""
        samples = self._baselines.get(task_id, [])
        if len(samples) < 3:
            return None
        return statistics.median(samples)


# ============================================================
# 关联分析器
# ============================================================

class DependencyAnalyzer:
    """
    关联分析器 - 分析任务依赖关系和资源竞争
    构建 DAG 依赖图，识别上下游影响
    """

    def __init__(self):
        # 任务ID -> 上游任务列表
        self._upstream: dict[str, list[str]] = defaultdict(list)
        # 任务ID -> 下游任务列表
        self._downstream: dict[str, list[str]] = defaultdict(list)
        # 工作流ID -> 任务列表（按依赖顺序）
        self._workflow_tasks: dict[str, list[str]] = defaultdict(list)

    def update_dag(self, workflow_id: str, tasks: list[dict]):
        """
        更新 DAG 依赖图
        tasks: [{task_id, name, upstream_tasks: [...]}, ...]
        """
        for task in tasks:
            tid = task["task_id"]
            self._workflow_tasks[workflow_id].append(tid)
            for up in task.get("upstream_tasks", []):
                self._upstream[tid].append(up)
                self._downstream[up].append(tid)

    def analyze(self, context: AnomalyContext) -> dict:
        """
        关联分析
        返回: {上游任务状态, 下游影响范围, 资源竞争任务}
        """
        result = {
            "upstream_status": self._check_upstream(context),
            "downstream_impact": self._check_downstream(context),
            "resource_competitors": [],
            "critical_path": self._is_critical_path(context),
        }
        return result

    def _check_upstream(self, context: AnomalyContext) -> list[dict]:
        """检查上游任务状态"""
        upstream = self._upstream.get(context.event.task_id, [])
        return [{"task_id": tid} for tid in upstream]

    def _check_downstream(self, context: AnomalyContext) -> list[dict]:
        """检查下游影响范围"""
        downstream = self._downstream.get(context.event.task_id, [])
        return [{"task_id": tid} for tid in downstream]

    def _is_critical_path(self, context: AnomalyContext) -> bool:
        """判断是否在核心链路上"""
        # 简化实现：如果有下游任务，视为核心链路
        return len(self._downstream.get(context.event.task_id, [])) > 0


# ============================================================
# 异常分类器
# ============================================================

class AnomalyClassifier:
    """
    异常分类器 - 综合规则检测 + 基线检测 + 关联分析结果
    输出统一的异常事件
    """

    def __init__(self):
        self.rule_engine = RuleEngine()
        self.baseline_detector = BaselineDetector()
        self.dependency_analyzer = DependencyAnalyzer()
        logger.info("异常分类器初始化完成")

    def classify(self, event: WorkflowEvent) -> Optional[AnomalyContext]:
        """
        对工作流事件进行分类检测
        返回 AnomalyContext (如果检测到异常) 或 None
        """
        # 1. 构建基础上下文
        context = self._build_context(event)

        # 2. 规则引擎检测
        hit_rules = self.rule_engine.evaluate(context)
        if not hit_rules:
            # 无规则命中，不做基线检测（避免误报）
            return None

        # 3. 取最高严重等级的规则作为主异常类型
        primary_rule = max(hit_rules, key=lambda r: self._severity_score(r.severity))
        context.anomaly_type = AnomalyType(primary_rule.anomaly_type)
        context.severity = Severity(primary_rule.severity)

        # 4. 基线检测（补充）
        if self.baseline_detector.detect(context):
            # 基线也检测到异常，增强置信度
            pass

        # 5. 关联分析
        analysis = self.dependency_analyzer.analyze(context)
        context.critical_path = analysis["critical_path"]

        logger.info(
            f"异常分类完成: type={context.anomaly_type.value}, "
            f"severity={context.severity.value}, "
            f"task={event.task_name}, workflow={event.workflow_name}"
        )
        return context

    def _build_context(self, event: WorkflowEvent) -> AnomalyContext:
        """构建初始上下文"""
        raw = event.raw_data
        actual_duration = raw.get("duration", 0)

        # 从基线获取预期耗时
        expected = self.baseline_detector.get_expected_duration(event.task_id)
        if expected is None:
            expected = raw.get("expected_duration", actual_duration)

        return AnomalyContext(
            anomaly_id=self._gen_anomaly_id(event),
            anomaly_type=AnomalyType.UNKNOWN,  # 待规则引擎确定
            severity=Severity.P3,               # 待规则引擎确定
            event=event,
            expected_duration=expected,
            actual_duration=actual_duration,
            historical_durations=self.baseline_detector._baselines.get(event.task_id, []),
            retry_count=raw.get("retry_count", 0),
            cluster_cpu_avg=raw.get("cluster_cpu_avg", 0),
            worker_count=raw.get("worker_count", 0),
            queue_depth=raw.get("queue_depth", 0),
            running_tasks=raw.get("running_tasks", 0),
            upstream_tasks=[],
            downstream_tasks=[],
            critical_path=False,
            event_time=event.timestamp,
            time_since_creation=time.time() - event.timestamp,
        )

    @staticmethod
    def _gen_anomaly_id(event: WorkflowEvent) -> str:
        raw = f"{event.workflow_id}_{event.task_id}_{event.timestamp}"
        return f"anom_{hashlib.md5(raw.encode()).hexdigest()[:12]}"

    @staticmethod
    def _severity_score(severity: str) -> int:
        return {"P0": 4, "P1": 3, "P2": 2, "P3": 1}.get(severity, 0)