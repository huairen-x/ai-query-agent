"""
ds-monitor MCP Server - 监控数据采集
MCP over stdio JSON-RPC
功能：查询工作流状态、任务日志、集群指标、异常历史
"""
from __future__ import annotations
import sys
import json
import os
import logging
import time
import threading
import hashlib
from typing import Optional

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ============================================================
# 日志配置（JSON 格式，便于日志系统采集）
# ============================================================
LOG_FORMAT = '{"time":"%(asctime)s","level":"%(levelname)s","name":"%(name)s","message":"%(message)s"}'
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stderr
)
logger = logging.getLogger("ds-monitor-mcp")


# ============================================================
# DS API 客户端（生产环境替换为真实 DS API 调用）
# ============================================================

class DSAPIClient:
    """
    DolphinScheduler API 客户端
    生产环境: 替换为 requests 调用真实 DS REST API
    当前: 模拟数据用于演示和测试
    """

    def __init__(self, mock: bool = True):
        self._mock = mock
        self._host = os.environ.get("DS_HOST", "localhost")
        self._port = int(os.environ.get("DS_PORT", "12345"))
        self._token = os.environ.get("DS_TOKEN", "")
        logger.info(f"DS API 客户端初始化: host={self._host}, port={self._port}, mock={mock}")

    # ---- 工作流查询 ----

    def list_workflows(self, page: int = 1, size: int = 20) -> dict:
        """列出工作流"""
        if self._mock:
            return self._mock_list_workflows(page, size)
        # TODO: 调用真实 DS API
        # resp = requests.get(f"{self._base_url}/projects/1/process-definition", ...)
        raise NotImplementedError("生产环境需实现 DS API 调用")

    def get_workflow_status(self, workflow_id: str) -> dict:
        """查询工作流实例状态"""
        if self._mock:
            return self._mock_workflow_status(workflow_id)
        raise NotImplementedError("生产环境需实现 DS API 调用")

    def get_task_status(self, workflow_id: str, task_id: str) -> dict:
        """查询任务实例状态"""
        if self._mock:
            return self._mock_task_status(workflow_id, task_id)
        raise NotImplementedError("生产环境需实现 DS API 调用")

    def get_task_log(self, task_instance_id: str) -> str:
        """获取任务日志"""
        if self._mock:
            return f"[模拟日志] 任务 {task_instance_id} 执行日志..."
        raise NotImplementedError("生产环境需实现 DS API 调用")

    def get_workflow_dag(self, workflow_id: str) -> dict:
        """获取工作流 DAG 定义"""
        if self._mock:
            return self._mock_dag(workflow_id)
        raise NotImplementedError("生产环境需实现 DS API 调用")

    # ---- 集群指标 ----

    def get_cluster_metrics(self) -> dict:
        """获取集群指标"""
        if self._mock:
            return self._mock_cluster_metrics()
        raise NotImplementedError("生产环境需实现 DS API 调用")

    def get_worker_load(self) -> list[dict]:
        """获取各 Worker 负载"""
        if self._mock:
            return self._mock_worker_load()
        raise NotImplementedError("生产环境需实现 DS API 调用")

    def get_queue_status(self) -> dict:
        """获取队列状态"""
        if self._mock:
            return self._mock_queue_status()
        raise NotImplementedError("生产环境需实现 DS API 调用")

    # ---- 轮询器 ----

    def start_polling(self, interval: int = 30, callback=None):
        """启动轮询"""
        def _poll():
            while True:
                try:
                    workflows = self.list_workflows(size=100)
                    metrics = self.get_cluster_metrics()
                    if callback:
                        callback(workflows, metrics)
                except Exception as e:
                    logger.error(f"轮询异常: {e}")
                time.sleep(interval)

        thread = threading.Thread(target=_poll, daemon=True)
        thread.start()
        logger.info(f"轮询器已启动, interval={interval}s")

    # ---- Mock 数据 ----

    def _mock_list_workflows(self, page: int, size: int) -> dict:
        return {
            "total": 1024,
            "page": page,
            "size": size,
            "items": [
                {"id": f"WF-{i:04d}", "name": f"工作流-{i}", "status": "running" if i % 10 == 0 else "success"}
                for i in range((page - 1) * size + 1, min(page * size, 1024) + 1)
            ],
        }

    def _mock_workflow_status(self, workflow_id: str) -> dict:
        return {
            "id": workflow_id,
            "name": "每日报表数据同步",
            "status": "running",
            "schedule_time": "2026-09-03 02:00:00",
            "start_time": "2026-09-03 02:00:05",
            "end_time": None,
            "duration": 2700,
            "running_tasks": 3,
            "failed_tasks": 0,
        }

    def _mock_task_status(self, workflow_id: str, task_id: str) -> dict:
        return {
            "id": task_id,
            "name": "Hive数据导入",
            "status": "running",
            "start_time": "2026-09-03 02:00:05",
            "duration": 2650,
            "retry_count": 0,
            "worker": "worker-3",
            "tenant": "data_platform",
        }

    def _mock_dag(self, workflow_id: str) -> dict:
        return {
            "workflow_id": workflow_id,
            "tasks": [
                {"task_id": "T-001", "name": "数据源抽取", "upstream": []},
                {"task_id": "T-002", "name": "数据清洗", "upstream": ["T-001"]},
                {"task_id": "T-003", "name": "数据转换", "upstream": ["T-002"]},
                {"task_id": "T-004", "name": "Hive数据导入", "upstream": ["T-003"]},
                {"task_id": "T-005", "name": "数据质量校验", "upstream": ["T-004"]},
                {"task_id": "T-006", "name": "报表生成", "upstream": ["T-005"]},
                {"task_id": "T-007", "name": "通知发送", "upstream": ["T-006"]},
            ],
        }

    def _mock_cluster_metrics(self) -> dict:
        import random
        return {
            "cpu_avg": round(random.uniform(30, 95), 1),
            "memory_avg": round(random.uniform(40, 90), 1),
            "worker_count": 5,
            "worker_active": 5,
            "queue_depth": random.randint(5, 200),
            "running_tasks": random.randint(10, 80),
            "total_tasks_today": 1250,
            "failed_tasks_today": random.randint(0, 15),
        }

    def _mock_worker_load(self) -> list[dict]:
        return [
            {"id": "worker-1", "cpu": 45.2, "memory": 62.1, "tasks": 12},
            {"id": "worker-2", "cpu": 78.5, "memory": 71.3, "tasks": 18},
            {"id": "worker-3", "cpu": 91.2, "memory": 85.7, "tasks": 25},
            {"id": "worker-4", "cpu": 55.0, "memory": 58.9, "tasks": 14},
            {"id": "worker-5", "cpu": 88.3, "memory": 79.4, "tasks": 22},
        ]

    def _mock_queue_status(self) -> dict:
        import random
        return {
            "queue_depth": random.randint(5, 200),
            "waiting_tasks": random.randint(3, 80),
            "avg_wait_time": round(random.uniform(5, 300), 1),
        }


# ============================================================
# MCP 协议处理
# ============================================================

class MonitorMCPServer:
    """
    ds-monitor MCP Server
    协议: JSON-RPC over stdio
    工具清单:
      - list_workflows: 列出工作流
      - get_workflow_status: 查询工作流状态
      - get_task_status: 查询任务状态
      - get_task_log: 获取任务日志
      - get_cluster_metrics: 获取集群指标
      - get_worker_load: 获取 Worker 负载
      - get_queue_status: 获取队列状态
      - get_workflow_dag: 获取 DAG 定义
    """

    def __init__(self):
        self._client = DSAPIClient(mock=True)
        self._tools = {
            "list_workflows": self._handle_list_workflows,
            "get_workflow_status": self._handle_get_workflow_status,
            "get_task_status": self._handle_get_task_status,
            "get_task_log": self._handle_get_task_log,
            "get_cluster_metrics": self._handle_get_cluster_metrics,
            "get_worker_load": self._handle_get_worker_load,
            "get_queue_status": self._handle_get_queue_status,
            "get_workflow_dag": self._handle_get_workflow_dag,
        }
        logger.info("ds-monitor MCP Server 初始化完成, 注册工具: %s",
                    ", ".join(self._tools.keys()))

    def handle_request(self, request: dict) -> dict:
        """处理 JSON-RPC 请求"""
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
            })
        return self._make_response(req_id, {"tools": tools})

    def _handle_call_tool(self, req_id, params: dict) -> dict:
        name = params.get("name", "") if isinstance(params, dict) else ""
        args = params.get("arguments", {}) if isinstance(params, dict) else {}

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

    def _handle_list_workflows(self, args: dict) -> dict:
        """列出工作流列表"""
        page = args.get("page", 1)
        size = args.get("size", 20)
        return self._client.list_workflows(page, size)

    def _handle_get_workflow_status(self, args: dict) -> dict:
        """查询工作流实例状态"""
        workflow_id = args.get("workflow_id", "")
        return self._client.get_workflow_status(workflow_id)

    def _handle_get_task_status(self, args: dict) -> dict:
        """查询任务实例状态"""
        workflow_id = args.get("workflow_id", "")
        task_id = args.get("task_id", "")
        return self._client.get_task_status(workflow_id, task_id)

    def _handle_get_task_log(self, args: dict) -> dict:
        """获取任务日志"""
        task_instance_id = args.get("task_instance_id", "")
        log = self._client.get_task_log(task_instance_id)
        return {"log": log}

    def _handle_get_cluster_metrics(self, args: dict) -> dict:
        """获取集群指标: CPU, 内存, 队列深度等"""
        return self._client.get_cluster_metrics()

    def _handle_get_worker_load(self, args: dict) -> dict:
        """获取各 Worker 节点负载"""
        return {"workers": self._client.get_worker_load()}

    def _handle_get_queue_status(self, args: dict) -> dict:
        """获取任务队列状态"""
        return self._client.get_queue_status()

    def _handle_get_workflow_dag(self, args: dict) -> dict:
        """获取工作流 DAG 定义"""
        workflow_id = args.get("workflow_id", "")
        return self._client.get_workflow_dag(workflow_id)

    # ---- 辅助 ----

    @staticmethod
    def _get_input_schema(name: str) -> dict:
        schemas = {
            "list_workflows": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "页码"},
                    "size": {"type": "integer", "description": "每页数量"},
                },
            },
            "get_workflow_status": {
                "type": "object",
                "required": ["workflow_id"],
                "properties": {
                    "workflow_id": {"type": "string", "description": "工作流ID"},
                },
            },
            "get_task_status": {
                "type": "object",
                "required": ["workflow_id", "task_id"],
                "properties": {
                    "workflow_id": {"type": "string"},
                    "task_id": {"type": "string"},
                },
            },
            "get_task_log": {
                "type": "object",
                "required": ["task_instance_id"],
                "properties": {
                    "task_instance_id": {"type": "string"},
                },
            },
            "get_cluster_metrics": {"type": "object", "properties": {}},
            "get_worker_load": {"type": "object", "properties": {}},
            "get_queue_status": {"type": "object", "properties": {}},
            "get_workflow_dag": {
                "type": "object",
                "required": ["workflow_id"],
                "properties": {
                    "workflow_id": {"type": "string"},
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
    server = MonitorMCPServer()
    logger.info("ds-monitor MCP Server 启动, 等待 stdin 输入...")

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