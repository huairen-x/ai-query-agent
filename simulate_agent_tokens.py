"""
模拟 Agent Token 消耗 - 模拟完整查询流程中各 Agent 的 Token 使用
运行后查看：python show_logs.py --stats
"""
from __future__ import annotations
import sys
import os
import time
import json
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_logger import log_query, reset_stats

# ============================================================
# 模拟场景：3 个查询问题，覆盖完整工作流
# ============================================================
SCENARIOS = [
    {
        "question": "各门店上月线索量排名",
        "steps": [
            # 1. Sisyphus 分析需求
            {
                "agent": "sisyphus",
                "tool": "llm_chat",
                "arguments": {"prompt": "用户问题：各门店上月线索量排名\n\n请分析用户意图，提取关键要素：查询对象、时间范围、聚合方式、排序要求。", "model": "gpt-4"},
                "input_tokens": 85,
                "output_tokens": 120,
                "elapsed_ms": 1800,
            },
            # 2. Librarian 查元数据
            {
                "agent": "librarian",
                "tool": "list_tables",
                "arguments": {"database": "dndc_dw"},
                "input_tokens": 15,
                "output_tokens": 180,
                "elapsed_ms": 320,
            },
            {
                "agent": "librarian",
                "tool": "search_columns",
                "arguments": {"keyword": "线索"},
                "input_tokens": 12,
                "output_tokens": 95,
                "elapsed_ms": 180,
            },
            {
                "agent": "librarian",
                "tool": "describe_table",
                "arguments": {"table_name": "t_dw_nev_sac_sale_clue"},
                "input_tokens": 18,
                "output_tokens": 210,
                "elapsed_ms": 250,
            },
            {
                "agent": "librarian",
                "tool": "describe_table",
                "arguments": {"table_name": "t_dim_dlr_info"},
                "input_tokens": 16,
                "output_tokens": 126,
                "elapsed_ms": 230,
            },
            # 3. Sisyphus 整合信息
            {
                "agent": "sisyphus",
                "tool": "llm_chat",
                "arguments": {"prompt": "已获取以下元数据：\n1. t_dw_nev_sac_sale_clue (clue_id, dlr_code, create_date, status)\n2. t_dim_dlr_info (dlr_code, dlr_short_name, province, city)\n\n请整合信息，为SQL生成准备。", "model": "gpt-4"},
                "input_tokens": 180,
                "output_tokens": 95,
                "elapsed_ms": 1500,
            },
            # 4. Oracle 生成 SQL
            {
                "agent": "oracle",
                "tool": "llm_chat",
                "arguments": {"prompt": "需求：各门店上月线索量排名\n\n表结构：\n- t_dw_nev_sac_sale_clue: clue_id, dlr_code, create_date, status\n- t_dim_dlr_info: dlr_code, dlr_short_name, province, city\n\n请生成 Hive SQL。", "model": "gpt-4"},
                "input_tokens": 220,
                "output_tokens": 180,
                "elapsed_ms": 2800,
            },
            # 5. Validator 审查 SQL
            {
                "agent": "validator",
                "tool": "validate_sql",
                "arguments": {"sql": "SELECT d.dlr_short_name, COUNT(c.clue_id) AS clue_cnt FROM t_dw_nev_sac_sale_clue c JOIN t_dim_dlr_info d ON c.dlr_code = d.dlr_code WHERE c.create_date >= '2026-07-01' AND c.create_date < '2026-08-01' GROUP BY d.dlr_short_name ORDER BY clue_cnt DESC"},
                "input_tokens": 95,
                "output_tokens": 45,
                "elapsed_ms": 150,
            },
            {
                "agent": "validator",
                "tool": "llm_chat",
                "arguments": {"prompt": "审查以下SQL：\n[SQL语句]\n\n检查：1. 表名是否存在 2. JOIN条件正确 3. WHERE条件合理 4. 性能问题", "model": "gpt-4"},
                "input_tokens": 260,
                "output_tokens": 150,
                "elapsed_ms": 2200,
            },
            # 6. Sisyphus 执行查询
            {
                "agent": "sisyphus",
                "tool": "query_hive",
                "arguments": {"sql": "SELECT d.dlr_short_name, COUNT(c.clue_id) AS clue_cnt FROM t_dw_nev_sac_sale_clue c JOIN t_dim_dlr_info d ON c.dlr_code = d.dlr_code WHERE c.create_date >= '2026-07-01' AND c.create_date < '2026-08-01' GROUP BY d.dlr_short_name ORDER BY clue_cnt DESC", "max_rows": 10},
                "input_tokens": 95,
                "output_tokens": 320,
                "elapsed_ms": 4500,
            },
            # 7. Sisyphus 解读结果
            {
                "agent": "sisyphus",
                "tool": "llm_chat",
                "arguments": {"prompt": "查询结果：\n| 门店名称 | 线索量 |\n| 北京朝阳店 | 2 |\n| 上海浦东店 | 2 |\n...\n\n请用自然语言解读查询结果。", "model": "gpt-4"},
                "input_tokens": 150,
                "output_tokens": 200,
                "elapsed_ms": 2100,
            },
        ]
    },
    {
        "question": "本月大定净量前十的门店",
        "steps": [
            {"agent": "sisyphus", "tool": "llm_chat", "arguments": {"prompt": "用户问题：本月大定净量前十的门店", "model": "gpt-4"}, "input_tokens": 75, "output_tokens": 110, "elapsed_ms": 1600},
            {"agent": "librarian", "tool": "search_columns", "arguments": {"keyword": "大定"}, "input_tokens": 10, "output_tokens": 85, "elapsed_ms": 160},
            {"agent": "librarian", "tool": "describe_table", "arguments": {"table_name": "t_dw_nev_sac_sale_achievement"}, "input_tokens": 20, "output_tokens": 240, "elapsed_ms": 280},
            {"agent": "librarian", "tool": "describe_table", "arguments": {"table_name": "t_dim_dlr_info"}, "input_tokens": 16, "output_tokens": 126, "elapsed_ms": 230},
            {"agent": "sisyphus", "tool": "llm_chat", "arguments": {"prompt": "整合元数据信息", "model": "gpt-4"}, "input_tokens": 165, "output_tokens": 85, "elapsed_ms": 1400},
            {"agent": "oracle", "tool": "llm_chat", "arguments": {"prompt": "生成大定净量 SQL", "model": "gpt-4"}, "input_tokens": 250, "output_tokens": 220, "elapsed_ms": 3200},
            {"agent": "validator", "tool": "validate_sql", "arguments": {"sql": "SELECT d.dlr_short_name, SUM(CASE WHEN a.ach_type_code=1001 THEN a.amount ELSE 0 END) - SUM(CASE WHEN a.ach_type_code=1002 THEN a.amount ELSE 0 END) AS net_dading FROM t_dw_nev_sac_sale_achievement a JOIN t_dim_dlr_info d ON a.dlr_code=d.dlr_code GROUP BY d.dlr_short_name ORDER BY net_dading DESC LIMIT 10"}, "input_tokens": 120, "output_tokens": 45, "elapsed_ms": 160},
            {"agent": "validator", "tool": "llm_chat", "arguments": {"prompt": "审查SQL", "model": "gpt-4"}, "input_tokens": 290, "output_tokens": 170, "elapsed_ms": 2400},
            {"agent": "sisyphus", "tool": "query_hive", "arguments": {"sql": "SELECT d.dlr_short_name, SUM(CASE WHEN a.ach_type_code=1001 THEN a.amount ELSE 0 END) - SUM(CASE WHEN a.ach_type_code=1002 THEN a.amount ELSE 0 END) AS net_dading FROM t_dw_nev_sac_sale_achievement a JOIN t_dim_dlr_info d ON a.dlr_code=d.dlr_code GROUP BY d.dlr_short_name ORDER BY net_dading DESC LIMIT 10", "max_rows": 10}, "input_tokens": 120, "output_tokens": 280, "elapsed_ms": 3800},
            {"agent": "sisyphus", "tool": "llm_chat", "arguments": {"prompt": "解读查询结果", "model": "gpt-4"}, "input_tokens": 140, "output_tokens": 190, "elapsed_ms": 1900},
        ]
    },
    {
        "question": "各车系交车完成率对比",
        "steps": [
            {"agent": "sisyphus", "tool": "llm_chat", "arguments": {"prompt": "用户问题：各车系交车完成率对比", "model": "gpt-4"}, "input_tokens": 78, "output_tokens": 115, "elapsed_ms": 1700},
            {"agent": "librarian", "tool": "search_columns", "arguments": {"keyword": "交车"}, "input_tokens": 10, "output_tokens": 80, "elapsed_ms": 150},
            {"agent": "librarian", "tool": "describe_table", "arguments": {"table_name": "t_dw_nev_sac_sale_achievement"}, "input_tokens": 20, "output_tokens": 240, "elapsed_ms": 280},
            {"agent": "librarian", "tool": "describe_table", "arguments": {"table_name": "t_dim_car_config"}, "input_tokens": 16, "output_tokens": 110, "elapsed_ms": 210},
            {"agent": "sisyphus", "tool": "llm_chat", "arguments": {"prompt": "整合元数据", "model": "gpt-4"}, "input_tokens": 170, "output_tokens": 90, "elapsed_ms": 1500},
            {"agent": "oracle", "tool": "llm_chat", "arguments": {"prompt": "生成交车完成率 SQL", "model": "gpt-4"}, "input_tokens": 280, "output_tokens": 250, "elapsed_ms": 3500},
            {"agent": "validator", "tool": "validate_sql", "arguments": {"sql": "SELECT c.car_series, SUM(CASE WHEN a.ach_type_code=1005 THEN a.amount ELSE 0 END) AS delivery_cnt, SUM(CASE WHEN a.ach_type_code=1001 THEN a.amount ELSE 0 END) AS order_cnt, ROUND(SUM(CASE WHEN a.ach_type_code=1005 THEN a.amount ELSE 0 END) / NULLIF(SUM(CASE WHEN a.ach_type_code=1001 THEN a.amount ELSE 0 END), 0) * 100, 1) AS completion_rate FROM t_dw_nev_sac_sale_achievement a JOIN t_dim_car_config c ON a.car_series=c.series_name GROUP BY c.car_series ORDER BY completion_rate DESC"}, "input_tokens": 150, "output_tokens": 50, "elapsed_ms": 180},
            {"agent": "validator", "tool": "llm_chat", "arguments": {"prompt": "审查SQL", "model": "gpt-4"}, "input_tokens": 320, "output_tokens": 190, "elapsed_ms": 2600},
            {"agent": "sisyphus", "tool": "query_hive", "arguments": {"sql": "SELECT c.car_series, SUM(CASE WHEN a.ach_type_code=1005 THEN a.amount ELSE 0 END) AS delivery_cnt, SUM(CASE WHEN a.ach_type_code=1001 THEN a.amount ELSE 0 END) AS order_cnt, ROUND(SUM(CASE WHEN a.ach_type_code=1005 THEN a.amount ELSE 0 END) / NULLIF(SUM(CASE WHEN a.ach_type_code=1001 THEN a.amount ELSE 0 END), 0) * 100, 1) AS completion_rate FROM t_dw_nev_sac_sale_achievement a JOIN t_dim_car_config c ON a.car_series=c.series_name GROUP BY c.car_series ORDER BY completion_rate DESC", "max_rows": 20}, "input_tokens": 150, "output_tokens": 300, "elapsed_ms": 4200},
            {"agent": "sisyphus", "tool": "llm_chat", "arguments": {"prompt": "解读查询结果", "model": "gpt-4"}, "input_tokens": 145, "output_tokens": 200, "elapsed_ms": 2000},
        ]
    },
]


def simulate():
    """模拟查询流程，记录审计日志"""
    print("=" * 60)
    print("  Agent Token 消耗模拟")
    print("=" * 60)

    # 重置统计
    reset_stats()

    total_input = 0
    total_output = 0
    total_elapsed = 0
    total_steps = 0

    for idx, scenario in enumerate(SCENARIOS, 1):
        print(f"\n  [{idx}/{len(SCENARIOS)}] 场景: {scenario['question']}")
        print(f"  {'─' * 50}")

        for step in scenario["steps"]:
            agent = step["agent"]
            tool = step["tool"]
            in_tokens = step["input_tokens"]
            out_tokens = step["output_tokens"]
            elapsed = step["elapsed_ms"]

            # 构造模拟的 MCP 响应
            mock_response = {
                "result": {
                    "content": [{"type": "text", "text": f"[{agent}] 模拟响应 ({in_tokens + out_tokens} tokens)"}],
                    "isError": False,
                }
            }

            # 通过审计日志记录
            log_query(
                tool_name=tool,
                arguments=step["arguments"],
                result=mock_response,
                elapsed_ms=elapsed,
                agent=agent,
            )

            total_input += in_tokens
            total_output += out_tokens
            total_elapsed += elapsed
            total_steps += 1

            print(f"    {agent:<12} {tool:<14} 输入:{in_tokens:>4} + 输出:{out_tokens:>4} = {in_tokens + out_tokens:>4} tokens | {elapsed:>5}ms")

        print(f"  {'─' * 50}")
        scenario_tokens = sum(s["input_tokens"] + s["output_tokens"] for s in scenario["steps"])
        scenario_time = sum(s["elapsed_ms"] for s in scenario["steps"])
        scenario_steps = len(scenario["steps"])
        print(f"  小计: {scenario_steps} 步, {scenario_tokens} tokens, {scenario_time:.1f}s")

    print(f"\n{'=' * 60}")
    print(f"  模拟完成！")
    print(f"  {'─' * 40}")
    print(f"  总场景数:    {len(SCENARIOS)}")
    print(f"  总步骤数:    {total_steps}")
    print(f"  总输入:      {total_input:,} tokens")
    print(f"  总输出:      {total_output:,} tokens")
    print(f"  总 Token:    {total_input + total_output:,}")
    print(f"  总耗时:      {total_elapsed / 1000:.1f}s")
    print(f"  平均/步:     {(total_input + total_output) // total_steps} tokens")
    print(f"\n  按 Agent 统计:")
    agent_stats = {}
    for s in SCENARIOS:
        for step in s["steps"]:
            a = step["agent"]
            if a not in agent_stats:
                agent_stats[a] = {"calls": 0, "input": 0, "output": 0, "time": 0}
            agent_stats[a]["calls"] += 1
            agent_stats[a]["input"] += step["input_tokens"]
            agent_stats[a]["output"] += step["output_tokens"]
            agent_stats[a]["time"] += step["elapsed_ms"]
    for agent, st in sorted(agent_stats.items(), key=lambda x: -x[1]["input"] + x[1]["output"]):
        print(f"    {agent:<12} {st['calls']:>2} 次调用 | 输入:{st['input']:>5} + 输出:{st['output']:>5} = {st['input'] + st['output']:>6} tokens | {st['time']/1000:.1f}s")

    print(f"\n  查看日志: python show_logs.py --stats")
    print(f"  查看明细: python show_logs.py --tail 50")
    print(f"  按 Agent: python show_logs.py --tool llm_chat")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    simulate()