"""
MCP Server 协议测试
测试 MCP Server 的 JSON-RPC 协议兼容性
"""
import sys
import os
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# mcp-servers 目录名含连字符，用 importlib 加载
import importlib.util as import_util

_mcp_servers_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "mcp-servers"
)

def _load_module(name, file_path):
    spec = import_util.spec_from_file_location(name, file_path)
    mod = import_util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

monitor_mod = _load_module("ds_monitor", os.path.join(_mcp_servers_path, "ds_monitor_server.py"))
operator_mod = _load_module("ds_operator", os.path.join(_mcp_servers_path, "ds_operator_server.py"))
knowledge_mod = _load_module("knowledge", os.path.join(_mcp_servers_path, "knowledge_server.py"))

MonitorMCPServer = monitor_mod.MonitorMCPServer
OperatorMCPServer = operator_mod.OperatorMCPServer
KnowledgeMCPServer = knowledge_mod.KnowledgeMCPServer


# ============================================================
# MCP 协议基础测试
# ============================================================

class TestMCPProtocol:
    """测试 MCP 协议基础功能"""

    @pytest.fixture
    def monitor(self):
        return MonitorMCPServer()

    @pytest.fixture
    def operator(self):
        return OperatorMCPServer()

    @pytest.fixture
    def knowledge(self):
        return KnowledgeMCPServer()

    def test_list_tools(self, monitor):
        request = {"jsonrpc": "2.0", "id": 1, "method": "mcp.list_tools"}
        response = monitor.handle_request(request)
        assert "result" in response
        assert "tools" in response["result"]
        assert len(response["result"]["tools"]) > 0

    def test_unknown_method(self, monitor):
        request = {"jsonrpc": "2.0", "id": 1, "method": "unknown.method"}
        response = monitor.handle_request(request)
        assert "error" in response
        assert response["error"]["code"] == -32601

    def test_invalid_json(self, monitor):
        """测试无效 JSON 输入"""
        # 通过 parse 错误测试
        request = {"jsonrpc": "2.0", "id": 1, "method": "mcp.call_tool", "params": "invalid"}
        response = monitor.handle_request(request)
        # 不应该崩溃
        assert response is not None


# ============================================================
# ds-monitor Server 测试
# ============================================================

class TestMonitorServer:
    @pytest.fixture
    def server(self):
        return MonitorMCPServer()

    def test_list_workflows(self, server):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "mcp.call_tool",
            "params": {
                "name": "list_workflows",
                "arguments": {"page": 1, "size": 5},
            },
        }
        response = server.handle_request(request)
        assert "result" in response
        assert "items" in response["result"]

    def test_get_cluster_metrics(self, server):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "mcp.call_tool",
            "params": {
                "name": "get_cluster_metrics",
                "arguments": {},
            },
        }
        response = server.handle_request(request)
        assert "result" in response
        assert "cpu_avg" in response["result"]


# ============================================================
# ds-operator Server 测试
# ============================================================

class TestOperatorServer:
    @pytest.fixture
    def server(self):
        return OperatorMCPServer()

    def test_rerun_task(self, server):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "mcp.call_tool",
            "params": {
                "name": "rerun_task",
                "arguments": {"workflow_id": "WF-001", "task_id": "T-001"},
            },
        }
        response = server.handle_request(request)
        assert "result" in response
        assert response["result"].get("success") is True

    def test_high_risk_requires_confirmation(self, server):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "mcp.call_tool",
            "params": {
                "name": "kill_task",
                "arguments": {"workflow_id": "WF-001", "task_id": "T-001"},
            },
        }
        response = server.handle_request(request)
        assert "result" in response
        # 应该要求确认
        assert response["result"].get("status") == "confirmation_required"


# ============================================================
# knowledge Server 测试
# ============================================================

class TestKnowledgeServer:
    @pytest.fixture
    def server(self):
        return KnowledgeMCPServer()

    def test_get_statistics(self, server):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "mcp.call_tool",
            "params": {
                "name": "get_statistics",
                "arguments": {},
            },
        }
        response = server.handle_request(request)
        assert "result" in response
        assert "total_entries" in response["result"]

    def test_search_similar(self, server):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "mcp.call_tool",
            "params": {
                "name": "search_similar",
                "arguments": {"keyword": "超时", "limit": 3},
            },
        }
        response = server.handle_request(request)
        assert "result" in response
        assert "cases" in response["result"]


# ============================================================
# 运行
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])