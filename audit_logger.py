"""
审计日志模块 - 记录所有查询、token 流量、耗时
支持：持久化日志文件、token 估算、统计汇总
"""
from __future__ import annotations
import os
import json
import time
import logging
import threading
from datetime import datetime

# ============================================================
# 配置
# ============================================================
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
AUDIT_LOG_FILE = os.path.join(LOG_DIR, "audit.log")
TOKEN_LOG_FILE = os.path.join(LOG_DIR, "token_usage.log")
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10MB 轮转

# ============================================================
# Token 估算（参考 GPT-4 的 token 化比例）
# ============================================================
# 中文约 1.5 tokens/字，英文约 0.25 tokens/字符
TOKEN_RATE_CN = 1.5
TOKEN_RATE_EN = 0.25


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数"""
    if not text:
        return 0
    cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    en_chars = len(text) - cn_chars
    return int(cn_chars * TOKEN_RATE_CN + en_chars * TOKEN_RATE_EN)


# ============================================================
# 审计日志
# ============================================================
_lock = threading.Lock()

# 内存统计（用于 show_logs 快速查询）
_stats = {
    "total_queries": 0,
    "total_tokens_input": 0,
    "total_tokens_output": 0,
    "total_time_ms": 0,
    "by_tool": {},
    "by_agent": {},
    "errors": 0,
}


def _ensure_log_dir():
    """确保日志目录存在"""
    os.makedirs(LOG_DIR, exist_ok=True)


def _rotate_if_needed(filepath: str):
    """日志文件轮转"""
    if os.path.exists(filepath) and os.path.getsize(filepath) > MAX_LOG_SIZE:
        base, ext = os.path.splitext(filepath)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.rename(filepath, f"{base}.{timestamp}{ext}")


def log_query(
    tool_name: str,
    arguments: dict,
    result: dict,
    elapsed_ms: float,
    agent: str = "unknown",
    status: str = "success",
):
    """记录一次查询审计日志"""
    global _stats
    sql = arguments.get("sql", "") or arguments.get("keyword", "") or str(arguments)
    input_tokens = estimate_tokens(sql)
    output_text = ""
    if result and "result" in result:
        content = result["result"].get("content", [])
        if content:
            output_text = content[0].get("text", "")
    output_tokens = estimate_tokens(output_text)
    is_error = result.get("result", {}).get("isError", False) if result else True

    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": time.time(),
        "agent": agent,
        "tool": tool_name,
        "arguments": {k: (v[:200] + "..." if isinstance(v, str) and len(v) > 200 else v)
                      for k, v in arguments.items()} if arguments else {},
        "status": "error" if is_error else status,
        "elapsed_ms": round(elapsed_ms, 1),
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
        },
        "error": result.get("result", {}).get("content", [{}])[0].get("text", "")[:200]
        if is_error else None,
    }

    with _lock:
        _ensure_log_dir()
        _rotate_if_needed(AUDIT_LOG_FILE)

        # 写审计日志
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # 更新内存统计
        _stats["total_queries"] += 1
        _stats["total_tokens_input"] += input_tokens
        _stats["total_tokens_output"] += output_tokens
        _stats["total_time_ms"] += elapsed_ms
        if is_error:
            _stats["errors"] += 1
        _stats["by_tool"][tool_name] = _stats["by_tool"].get(tool_name, 0) + 1
        _stats["by_agent"][agent] = _stats["by_agent"].get(agent, 0) + 1

        # 写 token 日志（单独文件方便分析）
        with open(TOKEN_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "time": entry["time"],
                "tool": tool_name,
                "agent": agent,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "elapsed_ms": round(elapsed_ms, 1),
            }, ensure_ascii=False) + "\n")


def get_stats() -> dict:
    """获取当前进程内统计汇总"""
    with _lock:
        avg_time = round(_stats["total_time_ms"] / _stats["total_queries"], 1) if _stats["total_queries"] else 0
        return {
            **{k: v for k, v in _stats.items() if k in ("total_queries", "total_tokens_input", "total_tokens_output", "total_time_ms", "errors")},
            "avg_time_ms": avg_time,
            "by_tool": dict(sorted(_stats["by_tool"].items(), key=lambda x: -x[1])),
            "by_agent": dict(sorted(_stats["by_agent"].items(), key=lambda x: -x[1])),
        }


def get_file_stats() -> dict:
    """从日志文件读取统计汇总（跨进程）"""
    entries = read_logs(limit=999999)
    if not entries:
        return get_stats()

    total_queries = len(entries)
    total_tokens_input = 0
    total_tokens_output = 0
    total_time_ms = 0
    errors = 0
    by_tool = {}
    by_agent = {}

    for e in entries:
        tokens = e.get("tokens", {})
        total_tokens_input += tokens.get("input", 0)
        total_tokens_output += tokens.get("output", 0)
        total_time_ms += e.get("elapsed_ms", 0)
        if e.get("status") == "error":
            errors += 1
        tool = e.get("tool", "unknown")
        by_tool[tool] = by_tool.get(tool, 0) + 1
        agent = e.get("agent", "unknown")
        by_agent[agent] = by_agent.get(agent, 0) + 1

    avg_time = round(total_time_ms / total_queries, 1) if total_queries else 0
    return {
        "total_queries": total_queries,
        "total_tokens_input": total_tokens_input,
        "total_tokens_output": total_tokens_output,
        "total_time_ms": total_time_ms,
        "errors": errors,
        "avg_time_ms": avg_time,
        "by_tool": dict(sorted(by_tool.items(), key=lambda x: -x[1])),
        "by_agent": dict(sorted(by_agent.items(), key=lambda x: -x[1])),
    }


def read_logs(limit: int = 50, tool: str = None, status: str = None) -> list[dict]:
    """读取审计日志"""
    if not os.path.exists(AUDIT_LOG_FILE):
        return []
    entries = []
    with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if tool and entry.get("tool") != tool:
                    continue
                if status and entry.get("status") != status:
                    continue
                entries.append(entry)
            except json.JSONDecodeError:
                continue
    return entries[-limit:]


def reset_stats():
    """重置统计（仅内存）"""
    global _stats
    with _lock:
        _stats = {
            "total_queries": 0,
            "total_tokens_input": 0,
            "total_tokens_output": 0,
            "total_time_ms": 0,
            "by_tool": {},
            "by_agent": {},
            "errors": 0,
        }