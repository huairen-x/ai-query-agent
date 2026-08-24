"""
MCP HTTP/SSE Gateway - 将 stdio MCP Server 转为 HTTP 服务
供 Windows Trae 远程调用
"""
import json
import uuid
import queue
import threading
import time
import importlib.util
import sys
import os
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ============================================================
# 加载 MCP Server 模块（复用 handle_request）
# ============================================================
def load_mcp_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MOCK_MODE = os.environ.get("MOCK_MODE", "true").lower() == "true"

if MOCK_MODE:
    hive_mod = load_mcp_module("mock_mcp_server", os.path.join(BASE_DIR, "mcp-servers/mock_mcp_server.py"))
    meta_mod = hive_mod  # mock 模式下两者共用同一模块
    print("[Gateway] Mock 模式已启用")
else:
    hive_mod = load_mcp_module("hive_mcp_server", os.path.join(BASE_DIR, "mcp-servers/hive_mcp_server.py"))
    meta_mod = load_mcp_module("metadata_mcp_server", os.path.join(BASE_DIR, "mcp-servers/metadata_mcp_server.py"))
    print("[Gateway] 生产模式已启用")

# ============================================================
# SSE Session 管理
# ============================================================
sessions = {}  # session_id -> {"request_queue": Queue, "response_queue": Queue}

def create_session():
    sid = str(uuid.uuid4())
    sessions[sid] = {
        "request_queue": queue.Queue(),
        "response_queue": queue.Queue(),
        "created_at": time.time()
    }
    return sid

# ============================================================
# 路由
# ============================================================
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "mock_mode": MOCK_MODE})

@app.route("/mcp/<server_name>/sse", methods=["GET"])
def sse_endpoint(server_name):
    """SSE 端点 - Trae 连接后持续接收消息"""
    if server_name not in ("hive-query", "metadata"):
        return jsonify({"error": f"Unknown server: {server_name}"}), 404

    sid = create_session()
    session = sessions[sid]

    def event_stream():
        # 发送 session id 作为初始事件
        yield f"event: endpoint\ndata: /mcp/{server_name}/message?session_id={sid}\n\n"
        while True:
            try:
                msg = session["response_queue"].get(timeout=30)
                yield f"event: message\ndata: {json.dumps(msg)}\n\n"
            except queue.Empty:
                # 心跳保活
                yield f": heartbeat\n\n"

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/mcp/<server_name>/message", methods=["POST"])
def message_endpoint(server_name):
    """消息端点 - 接收 JSON-RPC 请求并返回响应"""
    if server_name not in ("hive-query", "metadata"):
        return jsonify({"error": f"Unknown server: {server_name}"}), 404

    sid = request.args.get("session_id")
    if not sid or sid not in sessions:
        return jsonify({"error": "Invalid or missing session_id"}), 400

    body = request.get_json(force=True)
    method = body.get("method", "")

    # 选择对应模块
    mod = hive_mod if resolved == "hive-query" else meta_mod

    # 直接同步处理（简单可靠）
    try:
        response = mod.handle_request(body)
        return jsonify(response)
    except Exception as e:
        error_resp = {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {"code": -32603, "message": str(e)}
        }
        return jsonify(error_resp), 500

@app.route("/mcp/<server_name>", methods=["GET", "POST", "DELETE"])
def streamable_http_endpoint(server_name):
    """MCP Streamable HTTP endpoint (2025-03-26 spec)"""
    resolved = resolve_server(server_name) if server_name in SERVER_ALIASES else server_name
    if resolved not in ("hive-query", "metadata"):
        return jsonify({"error": f"Unknown server: {server_name}"}), 404

    mod = hive_mod if resolved == "hive-query" else meta_mod

    # GET: Open SSE stream for server-to-client messages
    if request.method == "GET":
        accept = request.headers.get("Accept", "")
        if "text/event-stream" not in accept:
            return jsonify({"error": "Accept must include text/event-stream"}), 406
        sid = create_session()
        session = sessions[sid]
        def event_stream():
            yield ": connected\n\n"
            while True:
                try:
                    msg = session["response_queue"].get(timeout=30)
                    yield f"event: message\ndata: {json.dumps(msg)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        resp = Response(event_stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        resp.headers["Mcp-Session-Id"] = sid
        return resp

    # DELETE: Terminate session
    if request.method == "DELETE":
        session_id = request.headers.get("Mcp-Session-Id")
        if session_id and session_id in sessions:
            del sessions[session_id]
        return "", 204

    # POST: Handle JSON-RPC messages
    body = request.get_json(force=True)
    method = body.get("method", "")

    try:
        response = mod.handle_request(body)

        # For initialize, upgrade protocol version, add serverInfo and session
        if method == "initialize" and "result" in response:
            response["result"]["protocolVersion"] = "2025-03-26"
            response["result"]["serverInfo"] = {
                "name": resolved,
                "version": "1.0.0"
            }
            if "capabilities" not in response["result"]:
                response["result"]["capabilities"] = {}
            sid = create_session()
            resp = jsonify(response)
            resp.headers["Mcp-Session-Id"] = sid
            return resp

        # For notifications/responses only (no id), return 202
        if "id" not in body or body.get("id") is None:
            return "", 202

        return jsonify(response)
    except Exception as e:
        error_resp = {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {"code": -32603, "message": str(e)}
        }
        return jsonify(error_resp), 500

# ============================================================
# Trae 兼容路由 - 支持 /sse/<name> 短路径和别名
# ============================================================
SERVER_ALIASES = {
    "hive": "hive-query",
    "hive-query": "hive-query",
    "metadata": "metadata",
}

def resolve_server(name):
    return SERVER_ALIASES.get(name)

@app.route("/sse/<server_name>", methods=["GET"])
def sse_compat_endpoint(server_name):
    """兼容 Trae 旧路径 /sse/hive -> SSE for hive-query"""
    resolved = resolve_server(server_name)
    if not resolved:
        return jsonify({"error": f"Unknown server: {server_name}"}), 404
    sid = create_session()
    session = sessions[sid]
    def event_stream():
        yield f"event: endpoint\ndata: /mcp/{resolved}/message?session_id={sid}\n\n"
        while True:
            try:
                msg = session["response_queue"].get(timeout=30)
                yield f"event: message\ndata: {json.dumps(msg)}\n\n"
            except queue.Empty:
                yield f": heartbeat\n\n"
    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/sse/<server_name>/message", methods=["POST"])
def sse_compat_message(server_name):
    """兼容 SSE message 端点"""
    resolved = resolve_server(server_name)
    if not resolved:
        return jsonify({"error": f"Unknown server: {server_name}"}), 404
    sid = request.args.get("session_id")
    if not sid or sid not in sessions:
        return jsonify({"error": "Invalid or missing session_id"}), 400
    body = request.get_json(force=True)
    mod = hive_mod if resolved == "hive-query" else meta_mod
    try:
        response = mod.handle_request(body)
        return jsonify(response)
    except Exception as e:
        return jsonify({"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32603, "message": str(e)}}), 500

# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("MCP_HTTP_PORT", "8080"))
    host = os.environ.get("MCP_HTTP_HOST", "0.0.0.0")
    print(f"[Gateway] Starting on http://{host}:{port}")
    print(f"[Gateway] Endpoints:")
    print(f"  Health:   GET  http://{host}:{port}/health")
    print(f"  SSE:      GET  http://{host}:{port}/mcp/<server>/sse")
    print(f"  Message:  POST http://{host}:{port}/mcp/<server>/message?session_id=xxx")
    print(f"  Simple:   POST http://{host}:{port}/mcp/<server>")
    app.run(host=host, port=port, threaded=True)
