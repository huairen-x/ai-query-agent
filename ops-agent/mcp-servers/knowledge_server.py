"""
knowledge MCP Server - 运维知识库
MCP over stdio JSON-RPC
功能：查询相似故障、记录处理结果、知识库统计
"""
from __future__ import annotations
import sys
import json
import os
import logging
import time
import hashlib

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
logger = logging.getLogger("knowledge-mcp")

from core.knowledge_base import KnowledgeBase
from core.models import KnowledgeEntry, AnomalyType


# ============================================================
# MCP 协议处理
# ============================================================

class KnowledgeMCPServer:
    """
    knowledge MCP Server
    工具清单:
      - search_similar: 搜索相似故障案例
      - record_case: 记录故障处理案例
      - get_statistics: 获取知识库统计
      - get_recent_cases: 获取最近案例
    """

    def __init__(self):
        self._kb = KnowledgeBase()
        self._tools = {
            "search_similar": self._handle_search,
            "record_case": self._handle_record,
            "get_statistics": self._handle_stats,
            "get_recent_cases": self._handle_recent,
        }
        logger.info("knowledge MCP Server 初始化完成, 注册工具: %s",
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

    def _handle_search(self, args: dict) -> dict:
        """搜索相似故障案例，支持按类型和关键词过滤"""
        anomaly_type_str = args.get("anomaly_type")
        keyword = args.get("keyword", "")
        limit = args.get("limit", 5)

        anomaly_type = None
        if anomaly_type_str:
            try:
                anomaly_type = AnomalyType(anomaly_type_str)
            except ValueError:
                pass

        entries = self._kb.search_similar(anomaly_type, keyword, limit)
        return {
            "total": len(entries),
            "cases": [
                {
                    "entry_id": e.entry_id,
                    "anomaly_type": e.anomaly_type.value,
                    "root_cause": e.root_cause,
                    "action_taken": e.action_taken,
                    "success": e.success,
                    "context_summary": e.context_summary,
                    "tags": e.tags,
                    "created_at": e.created_at,
                }
                for e in entries
            ],
        }

    def _handle_record(self, args: dict) -> dict:
        """记录故障处理案例到知识库"""
        entry = KnowledgeEntry(
            entry_id=f"kb_{hashlib.md5(str(time.time()).encode()).hexdigest()[:12]}",
            anomaly_type=AnomalyType(args.get("anomaly_type", "unknown")),
            root_cause=args.get("root_cause", ""),
            action_taken=json.dumps(args.get("actions", []), ensure_ascii=False),
            success=args.get("success", True),
            context_summary=args.get("summary", ""),
            tags=args.get("tags", []),
            created_at=time.time(),
        )
        entry_id = self._kb.add_entry(entry)
        return {"entry_id": entry_id, "status": "recorded"}

    def _handle_stats(self, args: dict) -> dict:
        """获取知识库统计信息"""
        return self._kb.get_statistics()

    def _handle_recent(self, args: dict) -> dict:
        """获取最近记录的案例"""
        limit = args.get("limit", 10)
        entries = self._kb.search_similar(limit=limit)
        return {
            "total": len(entries),
            "cases": [
                {
                    "entry_id": e.entry_id,
                    "anomaly_type": e.anomaly_type.value,
                    "root_cause": e.root_cause,
                    "success": e.success,
                    "context_summary": e.context_summary,
                    "created_at": e.created_at,
                }
                for e in entries
            ],
        }

    # ---- 辅助 ----

    @staticmethod
    def _get_input_schema(name: str) -> dict:
        schemas = {
            "search_similar": {
                "type": "object",
                "properties": {
                    "anomaly_type": {
                        "type": "string",
                        "enum": [t.value for t in AnomalyType],
                        "description": "异常类型过滤",
                    },
                    "keyword": {"type": "string", "description": "关键词搜索"},
                    "limit": {"type": "integer", "description": "返回条数"},
                },
            },
            "record_case": {
                "type": "object",
                "required": ["anomaly_type", "root_cause"],
                "properties": {
                    "anomaly_type": {"type": "string"},
                    "root_cause": {"type": "string"},
                    "actions": {"type": "array", "description": "执行的动作列表"},
                    "success": {"type": "boolean"},
                    "summary": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
            },
            "get_statistics": {
                "type": "object",
                "properties": {},
            },
            "get_recent_cases": {
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
    server = KnowledgeMCPServer()
    logger.info("knowledge MCP Server 启动, 等待 stdin 输入...")

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