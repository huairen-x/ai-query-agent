"""
ds-operator MCP Server - 运维操作执行
MCP over stdio JSON-RPC
功能：重跑任务、调整优先级、扩缩容 Worker、终止任务、阻断下游
"""
from __future__ import annotations
import sys
import json
import os
import logging
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ============================================================
# 日志
# ============================================================
LOG_FORMAT = '{"time":"%(asctime)s","level":"%(levelname)s","name":"%(name)s","message":"%(message)s"}'
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stderr
)
logger = logging.getLogger("ds-operator-mcp")


# ============================================================
# DS 操作客户端
# ============================================================

class DSOperatorClient:
    """
    DolphinScheduler 操作客户端
    生产环境: 替换为真实 DS REST API 调用
    """

    def __init__(self, mock: bool = True):
        self._mock = mock
        self._operation_log: list[dict] = []
        logger.info(f"DS Operator 客户端初始化: mock={mock}")

    def rerun_task(self, workflow_id: str, task_id: str, params: dict | None = None) -> dict:
        """重跑任务"""
        if self._mock:
            return self._mock_operation("rerun_task", workflow_id, task_id, True)
        raise NotImplementedError("生产环境需实现 DS API 调用")

    def adjust_priority(self, workflow_id: str, priority: str) -> dict:
        """调整工作流优先级"""
        if self._mock:
            return self._mock_operation("adjust_priority", workflow_id, "", True,
                                        {"priority": priority})
        raise NotImplementedError("生产环境需实现 DS API 调用")

    def kill_task(self, workflow_id: str, task_id: str) -> dict:
        """终止任务"""
        if self._mock:
            return self._mock_operation("kill_task", workflow_id, task_id, True)
        raise NotImplementedError("生产环境需实现 DS API 调用")

    def block_downstream(self, workflow_id: str, task_id: str) -> dict:
        """阻断下游依赖"""
        if self._mock:
            return self._mock_operation("block_downstream", workflow_id, task_id, True)
        raise NotImplementedError("生产环境需实现 DS API 调用")

    def scale_worker(self, replicas: int, namespace: str = "ds") -> dict:
        """扩缩容 Worker（K8s API）"""
        if self._mock:
            return self._mock_operation("scale_worker", namespace, "", True,
                                        {"replicas": replicas})
        raise NotImplementedError("生产环境需实现 K8s API 调用")

    def adjust_task_timeout(self, workflow_id: str, task_id: str, timeout: int) -> dict:
        """调整任务超时时间"""
        if self._mock:
            return self._mock_operation("adjust_timeout", workflow_id, task_id, True,
                                        {"timeout": timeout})
        raise NotImplementedError("生产环境需实现 DS API 调用")

    def get_operation_history(self, limit: int = 20) -> list[dict]:
        """获取操作历史"""
        return self._operation_log[-limit:]

    def _mock_operation(self, op: str, target1: str, target2: str, success: bool,
                        extra: dict | None = None) -> dict:
        import random
        time.sleep(0.1)  # 模拟网络延迟

        result = {
            "operation": op,
            "target": target1,
            "sub_target": target2 if target2 else None,
            "success": success,
            "duration_ms": round(random.uniform(50, 500), 1),
            "timestamp": time.time(),
            "message": f"{op} 操作成功" if success else f"{op} 操作失败",
        }
        if extra:
            result["params"] = extra

        self._operation_log.append(result)
        logger.info(f"模拟操作: {op} -> {target1}/{target2}, success={success}")
        return result


# ============================================================
# MCP 协议处理
# ============================================================

class OperatorMCPServer:
    """
    ds-operator MCP Server
    工具清单:
      - rerun_task: 重跑任务
      - adjust_priority: 调整工作流优先级
      - kill_task: 终止任务（高风险）
      - block_downstream: 阻断下游依赖（高风险）
      - scale_worker: 扩缩容 Worker
      - adjust_task_timeout: 调整任务超时
      - get_operation_history: 获取操作历史
    """

    # 高风险操作标记
    HIGH_RISK_OPERATIONS = {"kill_task", "block_downstream"}

    def __init__(self):
        self._client = DSOperatorClient(mock=True)
        self._tools = {
            "rerun_task": self._handle_rerun_task,
            "adjust_priority": self._handle_adjust_priority,
            "kill_task": self._handle_kill_task,
            "block_downstream": self._handle_block_downstream,
            "scale_worker": self._handle_scale_worker,
            "adjust_task_timeout": self._handle_adjust_timeout,
            "get_operation_history": self._handle_get_history,
        }
        logger.info("ds-operator MCP Server 初始化完成, 注册工具: %s",
                    ", ".join(self._tools.keys()))

    def handle_request(self, request: dict) -> dict:
        req_id = request.get("id", 0)
        method = request.get("method", "")

        if method == "mcp.list_tools":
            return self._handle_list_tools(req_id)
        elif method == "mcp.call_tool":
            return self._handle_call_tool(req_id, request.get("params", {}))
        else:
            return self._make_error(req_id, -32601, f"未知方法: {method}")

    def _handle_list_tools(self, req_id) -> dict:
        tools = []
        for name, handler in self._tools.items():
            tools.append({
                "name": name,
                "description": handler.__doc__ or "",
                "input_schema": self._get_input_schema(name),
                "risk_level": "high" if name in self.HIGH_RISK_OPERATIONS else "low",
            })
        return self._make_response(req_id, {"tools": tools})

    def _handle_call_tool(self, req_id, params: dict) -> dict:
        name = params.get("name", "") if isinstance(params, dict) else ""
        args = params.get("arguments", {}) if isinstance(params, dict) else {}

        # 高风险操作需要确认
        if name in self.HIGH_RISK_OPERATIONS and not args.get("confirmed"):
            return self._make_response(req_id, {
                "status": "confirmation_required",
                "message": f"高风险操作需要确认: {name}",
                "tool": name,
                "arguments": args,
                "hint": "请添加参数 \"confirmed\": true 确认执行",
            })

        handler = self._tools.get(name)
        if not handler:
            return self._make_error(req_id, -32602, f"未知工具: {name}")

        try:
            result = handler(args)
            return self._make_response(req_id, result)
        except Exception as e:
            logger.error(f"工具执行异常: {name}, error={e}")
            return self._make_error(req_id, -32603, str(e))

    # ---- 工具处理 ----

    def _handle_rerun_task(self, args: dict) -> dict:
        """重跑失败的任务"""
        workflow_id = args.get("workflow_id", "")
        task_id = args.get("task_id", "")
        params = args.get("params", {})
        return self._client.rerun_task(workflow_id, task_id, params)

    def _handle_adjust_priority(self, args: dict) -> dict:
        """调整工作流优先级 (HIGH/NORMAL/LOW)"""
        workflow_id = args.get("workflow_id", "")
        priority = args.get("priority", "HIGH")
        return self._client.adjust_priority(workflow_id, priority)

    def _handle_kill_task(self, args: dict) -> dict:
        """终止卡死的任务（高风险，需确认）"""
        workflow_id = args.get("workflow_id", "")
        task_id = args.get("task_id", "")
        return self._client.kill_task(workflow_id, task_id)

    def _handle_block_downstream(self, args: dict) -> dict:
        """阻断下游依赖，防止上游脏数据扩散（高风险，需确认）"""
        workflow_id = args.get("workflow_id", "")
        task_id = args.get("task_id", "")
        return self._client.block_downstream(workflow_id, task_id)

    def _handle_scale_worker(self, args: dict) -> dict:
        """扩缩容 Worker 节点"""
        replicas = args.get("replicas", 0)
        namespace = args.get("namespace", "ds")
        return self._client.scale_worker(replicas, namespace)

    def _handle_adjust_timeout(self, args: dict) -> dict:
        """调整任务超时时间（秒）"""
        workflow_id = args.get("workflow_id", "")
        task_id = args.get("task_id", "")
        timeout = args.get("timeout", 3600)
        return self._client.adjust_task_timeout(workflow_id, task_id, timeout)

    def _handle_get_history(self, args: dict) -> dict:
        """获取最近的操作历史"""
        limit = args.get("limit", 20)
        return {"operations": self._client.get_operation_history(limit)}

    # ---- 辅助 ----

    @staticmethod
    def _get_input_schema(name: str) -> dict:
        schemas = {
            "rerun_task": {
                "type": "object",
                "required": ["workflow_id", "task_id"],
                "properties": {
                    "workflow_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "params": {"type": "object", "description": "重跑参数"},
                },
            },
            "adjust_priority": {
                "type": "object",
                "required": ["workflow_id", "priority"],
                "properties": {
                    "workflow_id": {"type": "string"},
                    "priority": {"type": "string", "enum": ["HIGH", "NORMAL", "LOW"]},
                },
            },
            "kill_task": {
                "type": "object",
                "required": ["workflow_id", "task_id"],
                "properties": {
                    "workflow_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "confirmed": {"type": "boolean", "description": "确认执行高风险操作"},
                },
            },
            "block_downstream": {
                "type": "object",
                "required": ["workflow_id", "task_id"],
                "properties": {
                    "workflow_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
            },
            "scale_worker": {
                "type": "object",
                "required": ["replicas"],
                "properties": {
                    "replicas": {"type": "integer", "description": "目标副本数"},
                    "namespace": {"type": "string", "description": "K8s 命名空间"},
                },
            },
            "adjust_task_timeout": {
                "type": "object",
                "required": ["workflow_id", "task_id", "timeout"],
                "properties": {
                    "workflow_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "timeout": {"type": "integer", "description": "超时时间(秒)"},
                },
            },
            "get_operation_history": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回条数"},
                },
            },
        }
        return schemas.get(name, {"type": "object", "properties": {}})

    @staticmethod
    def _make_response(req_id, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _make_error(req_id, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# ============================================================
# 主入口
# ============================================================

def main():
    server = OperatorMCPServer()
    logger.info("ds-operator MCP Server 启动, 等待 stdin 输入...")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = server.handle_request(request)
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError as e:
            error_resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"JSON 解析错误: {e}"}}
            sys.stdout.write(json.dumps(error_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as e:
            logger.error(f"处理请求异常: {e}", exc_info=True)


if __name__ == "__main__":
    main()