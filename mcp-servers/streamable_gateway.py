"""
MCP Streamable HTTP Gateway - 兼容 Trae 等现代 MCP 客户端
协议: MCP Streamable HTTP (2025-03-26)
用法: python3 streamable_gateway.py [--port 8080]
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

sessions = {}
sessions_lock = threading.Lock()

ENV_VARS = ["HIVE_HOST", "HIVE_PORT", "HIVE_USER", "HIVE_PASSWORD", "HIVE_DATABASE", "HIVE_AUTH"]


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
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, text=True, bufsize=1,
        )
        resp_queue = queue.Queue()
        def reader():
            for line in proc.stdout:
                line = line.strip()
                if line:
                    resp_queue.put(line)
        threading.Thread(target=reader, daemon=True).start()
        session = {"proc": proc, "queue": resp_queue, "type": server_type}
        sessions[session_id] = session
        return session


@app.route("/mcp/<server_type>", methods=["POST"])
def mcp_endpoint(server_type):
    """Streamable HTTP endpoint - handles JSON-RPC requests directly"""
    if server_type not in SERVER_MAP:
        return jsonify({"error": f"Unknown server: {server_type}"}), 404
    
    session_id = request.headers.get("Mcp-Session-Id", str(uuid.uuid4()))
    try:
        session = get_or_create_session(session_id, server_type)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    
    data = request.get_data(as_text=True).strip()
    if not data:
        return jsonify({"error": "Empty request"}), 400
    
    try:
        session["proc"].stdin.write(data + "\n")
        session["proc"].stdin.flush()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    # Wait for response with timeout
    try:
        response_line = session["queue"].get(timeout=30)
        resp = json.loads(response_line)
    except queue.Empty:
        return jsonify({"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": "Timeout"}}), 504
    except json.JSONDecodeError:
        return jsonify({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON response"}}), 500
    
    return jsonify(resp), 200, {"Mcp-Session-Id": session_id}


@app.route("/sse/<server_type>", methods=["GET"])
def sse_endpoint(server_type):
    """Legacy SSE endpoint for backward compatibility"""
    if server_type not in SERVER_MAP:
        return jsonify({"error": f"Unknown server: {server_type}"}), 404
    session_id = str(uuid.uuid4())
    try:
        session = get_or_create_session(session_id, server_type)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    def event_stream():
        yield f"event: endpoint\ndata: /mcp/{server_type}\n\n"
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


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "servers": list(SERVER_MAP.keys()), "protocol": "streamable-http+sse"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    print(f"MCP Streamable HTTP Gateway on {args.host}:{args.port}")
    print(f"Servers: {list(SERVER_MAP.keys())}")
    print(f"Endpoints:")
    for name in SERVER_MAP:
        print(f"  POST http://{args.host}:{args.port}/mcp/{name}")
        print(f"  GET  http://{args.host}:{args.port}/sse/{name}")
    app.run(host=args.host, port=args.port, threaded=True)
