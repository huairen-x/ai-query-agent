"""
MCP SSE Gateway - 将 stdio JSON-RPC MCP Server 转为 HTTP SSE 服务
供 Windows Trae 等远程客户端连接
用法: python3 sse_gateway.py [--port 8080] [--server hive|metadata]
"""
import argparse
import json
import subprocess
import sys
import os
import threading
import queue
import uuid
import time
from flask import Flask, Response, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

SERVER_MAP = {
    "hive": "/home/admin/ai-query-agent/mcp-servers/hive_mcp_server.py",
    "metadata": "/home/admin/ai-query-agent/mcp-servers/metadata_mcp_server.py",
}

# 每个 session 维护一个子进程
sessions = {}
sessions_lock = threading.Lock()

ENV_VARS = [
    "HIVE_HOST", "HIVE_PORT", "HIVE_USER",
    "HIVE_PASSWORD", "HIVE_DATABASE", "HIVE_AUTH"
]


def get_or_create_session(session_id: str, server_type: str):
    with sessions_lock:
        if session_id in sessions:
            return sessions[session_id]
        script = SERVER_MAP.get(server_type)
        if not script:
            raise ValueError(f"Unknown server type: {server_type}")
        env = os.environ.copy()
        for var in ENV_VARS:
            if var in os.environ:
                env[var] = os.environ[var]
        proc = subprocess.Popen(
            [sys.executable, script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
        )
        resp_queue = queue.Queue()
        # 后台线程读取子进程 stdout
        def reader():
            for line in proc.stdout:
                line = line.strip()
                if line:
                    resp_queue.put(line)
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        session = {"proc": proc, "queue": resp_queue, "type": server_type}
        sessions[session_id] = session
        return session


@app.route("/sse/<server_type>", methods=["GET"])
def sse_endpoint(server_type):
    """SSE 端点 - Trae 连接此 URL 接收服务端推送"""
    session_id = str(uuid.uuid4())
    try:
        session = get_or_create_session(session_id, server_type)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    def event_stream():
        # 发送 endpoint 事件告知客户端 POST 地址
        post_url = f"/message/{session_id}"
        yield f"event: endpoint\ndata: {post_url}\n\n"
        while True:
            try:
                msg = session["queue"].get(timeout=30)
                yield f"data: {msg}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"
            except Exception:
                break

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/message/<session_id>", methods=["POST"])
def message_endpoint(session_id):
    """客户端发送 JSON-RPC 请求"""
    with sessions_lock:
        session = sessions.get(session_id)
    if not session:
        return jsonify({"error": "Invalid session"}), 404
    data = request.get_data(as_text=True)
    try:
        session["proc"].stdin.write(data.strip() + "\n")
        session["proc"].stdin.flush()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return "", 202


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "servers": list(SERVER_MAP.keys())})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    print(f"MCP SSE Gateway starting on {args.host}:{args.port}")
    print(f"Available servers: {list(SERVER_MAP.keys())}")
    print(f"SSE endpoints:")
    for name in SERVER_MAP:
        print(f"  http://{args.host}:{args.port}/sse/{name}")
    app.run(host=args.host, port=args.port, threaded=True)
