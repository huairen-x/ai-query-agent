"""
日志查看工具 - 查看审计日志、Token 流量、查询统计
使用方式：
    python show_logs.py              # 显示最近 20 条查询
    python show_logs.py --tail 50    # 显示最近 50 条
    python show_logs.py --stats      # 显示统计汇总
    python show_logs.py --watch      # 实时监控（每 3 秒刷新）
    python show_logs.py --tool query_hive  # 按工具过滤
    python show_logs.py --error      # 只看错误查询
"""
from __future__ import annotations
import sys
import os
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_logger import read_logs, get_file_stats, AUDIT_LOG_FILE, TOKEN_LOG_FILE


def print_header(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def show_tail(limit: int = 20, tool: str = None, status: str = None):
    """显示最近 N 条查询日志"""
    entries = read_logs(limit=limit * 3, tool=tool, status=status)[-limit:]

    if not entries:
        print("  暂无日志记录。")
        return

    print(f"\n  最近 {len(entries)} 条查询记录：\n")
    print(f"  {'时间':<20} {'工具':<18} {'状态':<8} {'耗时(ms)':<10} {'Token':<8} {'参数'}")
    print(f"  {'-'*20} {'-'*18} {'-'*8} {'-'*10} {'-'*8} {'-'*30}")

    for e in entries:
        t = e.get("time", "")[11:19]  # 只显示 HH:MM:SS
        tool_name = e.get("tool", "")[:16]
        status_str = "✅" if e.get("status") == "success" else "❌"
        elapsed = e.get("elapsed_ms", 0)
        tokens = e.get("tokens", {}).get("total", 0)
        args = e.get("arguments", {})
        arg_str = str(list(args.values())[0])[:40] if args else "-"
        print(f"  {t:<20} {tool_name:<18} {status_str:<8} {elapsed:<10} {tokens:<8} {arg_str}")

    print(f"\n  提示: 查看完整日志文件: {AUDIT_LOG_FILE}")


def show_stats():
    """显示统计汇总（从文件读取）"""
    stats = get_file_stats()

    print_header("查询统计汇总")
    print(f"\n  📊 总览")
    print(f"  {'':4}总查询次数:     {stats['total_queries']}")
    print(f"  {'':4}错误次数:       {stats['errors']}")
    print(f"  {'':4}错误率:         {stats['errors']/stats['total_queries']*100:.1f}%" if stats['total_queries'] else "  N/A")
    print(f"  {'':4}总耗时:         {stats['total_time_ms']/1000:.1f}s")
    print(f"  {'':4}平均耗时:       {stats['avg_time_ms']:.1f}ms")

    print(f"\n  🔤 Token 流量")
    print(f"  {'':4}输入 Tokens:    {stats['total_tokens_input']:,}")
    print(f"  {'':4}输出 Tokens:    {stats['total_tokens_output']:,}")
    print(f"  {'':4}总 Tokens:      {stats['total_tokens_input'] + stats['total_tokens_output']:,}")
    avg_tokens = (stats['total_tokens_input'] + stats['total_tokens_output']) / stats['total_queries'] if stats['total_queries'] else 0
    print(f"  {'':4}平均/次:        {avg_tokens:.0f}")

    print(f"\n  🛠️  按工具统计")
    for tool, count in stats.get("by_tool", {}).items():
        pct = count / stats['total_queries'] * 100 if stats['total_queries'] else 0
        bar = "█" * int(pct / 5) + "░" * max(0, 20 - int(pct / 5))
        print(f"  {'':4}{tool:<18} {count:>4}次  {pct:5.1f}%  {bar}")

    if stats.get("by_agent"):
        print(f"\n  👤 按 Agent 统计")
        for agent, count in stats.get("by_agent", {}).items():
            print(f"  {'':4}{agent:<18} {count:>4}次")


def show_watch(interval: int = 3):
    """实时监控模式"""
    last_count = 0
    try:
        while True:
            stats = get_file_stats()
            current = stats["total_queries"]
            new_queries = current - last_count

            print(f"\r  [{time.strftime('%H:%M:%S')}] "
                  f"总查询: {current} | "
                  f"新增: {new_queries} | "
                  f"Token: {stats['total_tokens_input'] + stats['total_tokens_output']:,} | "
                  f"错误: {stats['errors']} | "
                  f"平均耗时: {stats['avg_time_ms']}ms", end="")

            if new_queries > 0:
                last_count = current
                # 显示最新一条
                entries = read_logs(limit=1)
                if entries:
                    e = entries[-1]
                    print(f"\n  → {e.get('tool')}: {str(list(e.get('arguments', {}).values()))[:60]}")

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  监控已停止。")


def show_token_detail():
    """显示 Token 使用详情"""
    if not os.path.exists(TOKEN_LOG_FILE):
        print("  Token 日志文件不存在，尚无查询记录。")
        return

    print_header("Token 使用明细")
    total_in = 0
    total_out = 0
    count = 0
    with open(TOKEN_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                total_in += entry.get("input_tokens", 0)
                total_out += entry.get("output_tokens", 0)
                count += 1
            except json.JSONDecodeError:
                continue

    print(f"\n  {'':4}记录数:    {count}")
    print(f"  {'':4}输入:      {total_in:,} tokens")
    print(f"  {'':4}输出:      {total_out:,} tokens")
    print(f"  {'':4}总计:      {total_in + total_out:,} tokens")
    if count:
        print(f"  {'':4}平均/次:  {(total_in + total_out)//count:,} tokens")

    print(f"\n  📄 日志文件: {TOKEN_LOG_FILE}")


def main():
    args = sys.argv[1:]

    if "--stats" in args:
        show_stats()
        show_token_detail()
    elif "--watch" in args:
        print("  📡 实时监控模式（Ctrl+C 退出）")
        show_watch()
    elif "--tool" in args:
        idx = args.index("--tool")
        tool = args[idx + 1] if idx + 1 < len(args) else None
        limit = 50
        if "--tail" in args:
            idx2 = args.index("--tail")
            limit = int(args[idx2 + 1]) if idx2 + 1 < len(args) else 50
        show_tail(limit=limit, tool=tool)
    elif "--error" in args:
        show_tail(limit=50, status="error")
    elif "--tail" in args:
        idx = args.index("--tail")
        limit = int(args[idx + 1]) if idx + 1 < len(args) else 20
        show_tail(limit=limit)
    else:
        # 默认：显示统计 + 最近 20 条
        show_stats()
        show_tail(limit=20)


if __name__ == "__main__":
    main()