"""
智能运维 Agent - 主入口
企业级特性: 优雅关闭、健康检查、结构化指标

支持三种运行模式:
  1. MCP Server 模式: 通过 stdio JSON-RPC 与 Agent 框架通信
  2. 独立运行模式: 直接启动并在控制台输出
  3. Webhook 模式: 启动 HTTP 服务接收事件

用法:
  # MCP Server 模式（供 OpenCode 调用）
  python main.py

  # 模拟测试模式
  python main.py --simulate timeout
  python main.py --simulate failure
  python main.py --simulate resource
  python main.py --simulate normal
  python main.py --simulate all

  # 启动 Webhook 服务
  python main.py --webhook --port 8081

  # 健康检查（仅 Webhook 模式）
  curl http://localhost:8081/health
  curl http://localhost:8081/api/v1/metrics
"""
from __future__ import annotations
import sys
import json
import os
import logging
import argparse
import time
import signal
from typing import Optional

# Windows 控制台编码兼容
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        # Python < 3.7 不支持 reconfigure
        pass

# ============================================================
# 配置日志（JSON 格式，便于日志系统采集）
# ============================================================
LOG_FORMAT = '{"time":"%(asctime)s","level":"%(levelname)s","name":"%(name)s","message":"%(message)s"}'
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stderr,
)
logger = logging.getLogger("ops-agent")


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="智能运维 Agent - 企业级 AIOps 系统")
    parser.add_argument("--simulate", type=str, default=None,
                        choices=["timeout", "failure", "resource", "normal", "all"],
                        help="模拟特定场景的事件")
    parser.add_argument("--webhook", action="store_true", default=False,
                        help="启动 Webhook HTTP 服务")
    parser.add_argument("--port", type=int, default=8081,
                        help="Webhook 服务端口")
    parser.add_argument("--mcp", action="store_true", default=False,
                        help="MCP Server 模式（通过 stdin/stdout 通信）")
    parser.add_argument("--log-level", type=str, default=None,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="日志级别")
    args = parser.parse_args()

    # 设置日志级别
    if args.log_level:
        logging.getLogger().setLevel(args.log_level)

    # 默认模式: 自动检测是否通过 stdin 管道接收数据
    is_mcp_mode = args.mcp or (not sys.stdin.isatty() and not args.simulate and not args.webhook)

    if is_mcp_mode:
        run_mcp_server()
    elif args.simulate:
        run_simulation(args.simulate)
    elif args.webhook:
        run_webhook_server(args.port)
    else:
        # 交互式 demo
        run_interactive_demo()


# ============================================================
# MCP Server 模式
# ============================================================

def run_mcp_server():
    """MCP Server 模式 - 通过 stdin/stdout 接收 JSON-RPC 请求"""
    from core.orchestrator import Orchestrator
    from core.utils import GracefulShutdown, get_metrics

    orchestrator = Orchestrator()
    shutdown = GracefulShutdown()
    metrics = get_metrics()

    # 注册清理
    shutdown.register("orchestrator", lambda: logger.info("编排器已停止"))

    logger.info("智能运维 Agent (MCP Server) 启动, 等待 stdin 输入...")

    # 先输出工具列表
    tools_response = {
        "jsonrpc": "2.0",
        "id": "init",
        "result": {
            "tools": [
                {
                    "name": "process_event",
                    "description": "处理工作流事件，进行异常检测和自动修复",
                    "input_schema": {
                        "type": "object",
                        "required": ["event_id", "event_type", "workflow_id", "task_id", "raw_data"],
                        "properties": {
                            "event_id": {"type": "string"},
                            "event_type": {"type": "string", "enum": ["task_timeout", "task_failure", "task_success", "cluster_high_load"]},
                            "workflow_id": {"type": "string"},
                            "workflow_name": {"type": "string"},
                            "task_id": {"type": "string"},
                            "task_name": {"type": "string"},
                            "tenant": {"type": "string"},
                            "raw_data": {"type": "object"},
                        },
                    },
                },
                {
                    "name": "simulate_scenario",
                    "description": "模拟特定异常场景（用于测试）",
                    "input_schema": {
                        "type": "object",
                        "required": ["scenario"],
                        "properties": {
                            "scenario": {"type": "string", "enum": ["timeout", "failure", "resource", "normal"]},
                        },
                    },
                },
                {
                    "name": "get_stats",
                    "description": "获取系统运行统计",
                    "input_schema": {"type": "object", "properties": {}},
                },
                {
                    "name": "get_metrics",
                    "description": "获取系统性能指标（企业级可观测性）",
                    "input_schema": {"type": "object", "properties": {}},
                },
                {
                    "name": "health",
                    "description": "健康检查接口",
                    "input_schema": {"type": "object", "properties": {}},
                },
            ],
        },
    }
    sys.stdout.write(json.dumps(tools_response, ensure_ascii=False) + "\n")
    sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_mcp_request(orchestrator, request, metrics)
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError as e:
            error_resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"JSON 解析错误: {e}"}}
            sys.stdout.write(json.dumps(error_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as e:
            logger.error(f"处理请求异常: {e}", exc_info=True)
            metrics.counter("mcp_errors")


def handle_mcp_request(orchestrator, request: dict, metrics=None) -> dict:
    """处理 MCP 请求"""
    req_id = request.get("id", 0)
    method = request.get("method", "")

    if method == "mcp.list_tools":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "tools": [
                {"name": "process_event", "description": "处理工作流事件"},
                {"name": "simulate_scenario", "description": "模拟异常场景"},
                {"name": "get_stats", "description": "获取系统统计"},
                {"name": "get_metrics", "description": "获取系统性能指标"},
                {"name": "health", "description": "健康检查"},
            ],
        }}

    if method == "mcp.call_tool":
        params = request.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {})

        from core.models import WorkflowEvent

        if name == "process_event":
            event = WorkflowEvent(
                event_id=args.get("event_id", ""),
                event_type=args.get("event_type", ""),
                workflow_id=args.get("workflow_id", ""),
                workflow_name=args.get("workflow_name", ""),
                task_id=args.get("task_id", ""),
                task_name=args.get("task_name", ""),
                tenant=args.get("tenant", "default"),
                timestamp=time.time(),
                raw_data=args.get("raw_data", {}),
            )
            if metrics:
                metrics.counter("events_processed")
            result = orchestrator.process_event(event)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}

        elif name == "simulate_scenario":
            scenario = args.get("scenario", "timeout")
            if metrics:
                metrics.counter("simulations_run")
            result = orchestrator.simulate_event(scenario)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}

        elif name == "get_stats":
            return {"jsonrpc": "2.0", "id": req_id, "result": orchestrator.get_stats()}

        elif name == "get_metrics" and metrics:
            return {"jsonrpc": "2.0", "id": req_id, "result": metrics.snapshot()}

        elif name == "health":
            from core.config import get_config
            config = get_config()
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "status": "healthy",
                "version": "0.1.0",
                "uptime": time.time(),
                "mock_mode": config.mock_mode,
                "circuit_breaker": orchestrator.get_stats().get("decision_engine", {}).get("circuit_breaker", {}),
            }}

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"未知方法: {method}"}}


# ============================================================
# 模拟模式
# ============================================================

def run_simulation(scenario: str):
    """运行模拟测试"""
    from core.orchestrator import Orchestrator

    orchestrator = Orchestrator()
    print(f"\n{'='*60}")
    print(f"  智能运维 Agent - 模拟模式")
    print(f"  场景: {scenario}")
    print(f"{'='*60}\n")

    scenarios = ["timeout", "failure", "resource", "normal"]
    if scenario == "all":
        for s in scenarios:
            print(f"\n{'─'*40}")
            print(f"  场景: {s}")
            print(f"{'─'*40}")
            result = orchestrator.simulate_event(s)
            pretty_print(result)
    else:
        result = orchestrator.simulate_event(scenario)
        pretty_print(result)

    # 打印统计
    print(f"\n{'='*60}")
    print(f"  系统统计")
    print(f"{'='*60}")
    stats = orchestrator.get_stats()
    pretty_print(stats)

    # 打印知识库统计
    print(f"\n{'='*60}")
    print(f"  知识库")
    print(f"{'='*60}")
    kb = orchestrator.get_knowledge_base()
    recent = kb.search_similar(limit=3)
    for entry in recent:
        print(f"  [{entry.anomaly_type.value}] {entry.context_summary}")
        print(f"  根因: {entry.root_cause[:60]}...")
        print(f"  结果: {'[成功]' if entry.success else '[失败]'}")
        print()


# ============================================================
# Webhook 模式
# ============================================================

def run_webhook_server(port: int):
    """启动 Webhook HTTP 服务（企业级: 优雅关闭、健康检查、指标端点）"""
    try:
        from flask import Flask, request, jsonify
    except ImportError:
        logger.error("Flask 未安装, 无法启动 Webhook 服务")
        logger.error("请执行: pip install flask")
        sys.exit(1)

    from core.orchestrator import Orchestrator
    from core.models import WorkflowEvent
    from core.utils import GracefulShutdown, get_metrics

    app = Flask(__name__)
    orchestrator = Orchestrator()
    shutdown = GracefulShutdown()
    metrics = get_metrics()

    # ---- 健康检查 ----

    @app.route("/health", methods=["GET"])
    def health():
        """健康检查端点"""
        return jsonify({
            "status": "healthy",
            "version": "0.1.0",
            "timestamp": time.time(),
            "uptime": time.time() - app_start_time,
        })

    @app.route("/api/v1/metrics", methods=["GET"])
    def handle_metrics():
        """指标暴露端点（用于 Prometheus 采集）"""
        return jsonify(metrics.snapshot())

    # ---- 业务端点 ----

    @app.route("/api/v1/event", methods=["POST"])
    def handle_event():
        """接收工作流事件"""
        data = request.get_json()
        if not data:
            metrics.counter("api_errors")
            return jsonify({"error": "empty body"}), 400

        event = WorkflowEvent(
            event_id=data.get("event_id", ""),
            event_type=data.get("event_type", ""),
            workflow_id=data.get("workflow_id", ""),
            workflow_name=data.get("workflow_name", ""),
            task_id=data.get("task_id", ""),
            task_name=data.get("task_name", ""),
            tenant=data.get("tenant", "default"),
            timestamp=time.time(),
            raw_data=data.get("raw_data", {}),
        )
        start_time = time.time()
        result = orchestrator.process_event(event)
        elapsed = time.time() - start_time

        metrics.counter("events_received")
        metrics.histogram("event_processing_time", elapsed)
        metrics.record("event_processing", elapsed, tags={"status": result.get("status", "unknown")})

        return jsonify(result)

    @app.route("/api/v1/stats", methods=["GET"])
    def handle_stats():
        """系统运行统计"""
        return jsonify(orchestrator.get_stats())

    @app.route("/api/v1/simulate/<scenario>", methods=["POST"])
    def handle_simulate(scenario):
        """模拟异常场景"""
        if scenario not in ("timeout", "failure", "resource", "normal"):
            return jsonify({"error": f"未知场景: {scenario}"}), 400
        result = orchestrator.simulate_event(scenario)
        return jsonify(result)

    # ---- 启动 ----
    app_start_time = time.time()

    logger.info(f"Webhook 服务启动: http://0.0.0.0:{port}")
    print(f"\n  Webhook 服务已启动:")
    print(f"    POST /api/v1/event       - 接收工作流事件")
    print(f"    POST /api/v1/simulate/:sc - 模拟异常场景")
    print(f"    GET  /api/v1/stats        - 系统统计")
    print(f"    GET  /api/v1/metrics      - 性能指标")
    print(f"    GET  /health              - 健康检查\n")

    try:
        app.run(host="0.0.0.0", port=port, debug=False)
    except KeyboardInterrupt:
        logger.info("收到中断信号, 开始优雅关闭...")
        shutdown.shutdown()


# ============================================================
# 交互式 Demo
# ============================================================

def run_interactive_demo():
    """交互式演示模式"""
    from core.orchestrator import Orchestrator

    orchestrator = Orchestrator()
    print(f"\n{'='*60}")
    print(f"  智能运维 Agent - 交互式 Demo")
    print(f"{'='*60}")
    print(f"  可用命令:")
    print(f"    simulate <场景>  - 模拟事件 (timeout/failure/resource/normal/all)")
    print(f"    stats            - 查看统计")
    print(f"    metrics          - 查看性能指标")
    print(f"    kb               - 查看知识库")
    print(f"    quit             - 退出")
    print(f"{'='*60}\n")

    while True:
        try:
            cmd = input("ops-agent> ").strip()
            if not cmd:
                continue
            if cmd == "quit":
                break
            elif cmd == "stats":
                pretty_print(orchestrator.get_stats())
            elif cmd == "metrics":
                from core.utils import get_metrics
                pretty_print(get_metrics().snapshot())
            elif cmd == "kb":
                kb = orchestrator.get_knowledge_base()
                recent = kb.search_similar(limit=5)
                for entry in recent:
                    print(f"  [{entry.anomaly_type.value}] {entry.context_summary}")
                    print(f"    根因: {entry.root_cause[:60]}")
            elif cmd.startswith("simulate"):
                parts = cmd.split()
                scenario = parts[1] if len(parts) > 1 else "timeout"
                result = orchestrator.simulate_event(scenario)
                pretty_print(result)
            else:
                print(f"未知命令: {cmd}")
        except KeyboardInterrupt:
            print()
            break
        except EOFError:
            break

    print("Bye!")


# ============================================================
# 辅助
# ============================================================

def pretty_print(data: dict):
    """格式化打印 JSON"""
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print()


if __name__ == "__main__":
    main()