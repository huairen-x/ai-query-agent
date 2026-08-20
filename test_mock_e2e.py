"""
智能问数系统 - Mock 端到端测试
测试内容：使用 mock_mcp_server 模拟真实查询流程
无需 Hive 环境，直接验证 SQLite 模拟数据 + 查询功能
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mcp-servers'))
from mock_mcp_server import (
    validate_sql_safe, query_hive, validate_sql,
    list_tables, describe_table, search_columns,
    handle_request
)

PASS = "✅ PASS"
FAIL = "❌ FAIL"
results = []
test_count = 0
pass_count = 0


def test(name, condition, detail=""):
    global test_count, pass_count
    test_count += 1
    status = PASS if condition else FAIL
    if condition:
        pass_count += 1
    msg = f"  {status} | {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    results.append({"name": name, "status": "PASS" if condition else "FAIL", "detail": detail})


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ============================================================
# 1. 元数据查询
# ============================================================
section("1. 元数据查询测试")

# 1.1 list_tables
result = list_tables()
test("list_tables 返回表列表", result["count"] == 4, f"共 {result['count']} 张表")
test("包含线索表", "t_dw_nev_sac_sale_clue" in result["tables"], "")
test("包含业绩表", "t_dw_nev_sac_sale_achievement" in result["tables"], "")
test("包含经销商表", "t_dim_dlr_info" in result["tables"], "")
test("包含车系表", "t_dim_car_config" in result["tables"], "")

# 1.2 describe_table
desc = describe_table("t_dim_dlr_info")
test("describe_table 返回字段", desc["column_count"] == 6, f"共 {desc['column_count']} 个字段")
col_names = [c["name"] for c in desc["columns"]]
test("包含 dlr_code 字段", "dlr_code" in col_names, "")
test("包含 dlr_short_name 字段", "dlr_short_name" in col_names, "")
test("包含 city 字段", "city" in col_names, "")
test("包含 province 字段", "province" in col_names, "")
test("主键正确", desc["properties"].get("primary.key") == "dlr_code", "")

# 1.3 describe_table - 线索表
desc_clue = describe_table("t_dw_nev_sac_sale_clue")
test("线索表含 10 个字段", desc_clue["column_count"] == 10, "")
test("线索表主键", desc_clue["properties"].get("primary.key") == "clue_id", "")
test("线索表外键", "foreign.key.dlr_code" in desc_clue["properties"], "t_dim_dlr_info")

# 1.4 search_columns
search = search_columns("clue_id")
test("search_columns('clue_id') 找到匹配", search["count"] >= 1, f"找到 {search['count']} 个")
search2 = search_columns("门店")
test("search_columns('门店') 找到匹配", search2["count"] >= 1, f"找到 {search2['count']} 个")


# ============================================================
# 2. SQL 查询测试
# ============================================================
section("2. SQL 查询测试")

# 2.1 简单查询
qr = query_hive("SELECT COUNT(*) AS cnt FROM t_dim_dlr_info")
test("查询门店总数", qr["row_count"] == 1 and qr["rows"][0]["cnt"] == "10", "10 家门店")

# 2.2 带 WHERE 条件
qr = query_hive("SELECT COUNT(*) AS cnt FROM t_dim_dlr_info WHERE province = '广东省'")
test("查询广东省门店", qr["row_count"] == 1 and qr["rows"][0]["cnt"] == "2", "2 家")

# 2.3 GROUP BY 聚合
qr = query_hive(
    "SELECT province, COUNT(*) AS cnt FROM t_dim_dlr_info GROUP BY province ORDER BY cnt DESC"
)
test("GROUP BY 省份聚合", qr["row_count"] >= 5, f"{qr['row_count']} 个省份")
test("广东省排第一", qr["rows"][0]["province"] == "广东省", f"cnt={qr['rows'][0]['cnt']}")

# 2.4 JOIN 查询
qr = query_hive(
    "SELECT c.dlr_name, c.car_series, c.customer_name "
    "FROM t_dw_nev_sac_sale_clue c "
    "WHERE c.status = '已成交' "
    "ORDER BY c.dlr_code"
)
test("JOIN 查询已成交线索", qr["row_count"] >= 5, f"{qr['row_count']} 条")

# 2.5 业绩统计 - 大定净量
qr = query_hive(
    "SELECT a.dlr_code, "
    "  SUM(CASE WHEN a.ach_type_code = 1001 THEN a.amount ELSE 0 END) - "
    "  SUM(CASE WHEN a.ach_type_code = 1002 THEN a.amount ELSE 0 END) AS net_dading "
    "FROM t_dw_nev_sac_sale_achievement a "
    "GROUP BY a.dlr_code "
    "ORDER BY net_dading DESC"
)
test("大定净量查询", qr["row_count"] >= 5, f"{qr['row_count']} 个门店")
# 北京朝阳店：大定3-退订1=2
beijing = [r for r in qr["rows"] if r["dlr_code"] == "DLR001"]
test("北京朝阳店大定净量=2", len(beijing) > 0 and beijing[0]["net_dading"] == "2", "")

# 2.6 车系销量排名
qr = query_hive(
    "SELECT a.car_series, SUM(a.amount) AS total "
    "FROM t_dw_nev_sac_sale_achievement a "
    "WHERE a.ach_type_code = 1001 "
    "GROUP BY a.car_series "
    "ORDER BY total DESC"
)
test("车系大定排名", qr["row_count"] >= 5, f"{qr['row_count']} 个车系")

# 2.7 线索量排名
qr = query_hive(
    "SELECT dlr_name, COUNT(*) AS cnt "
    "FROM t_dw_nev_sac_sale_clue "
    "GROUP BY dlr_name "
    "ORDER BY cnt DESC"
)
test("门店线索量排名", qr["row_count"] == 10, "10 家门店")

# 2.8 带 LIMIT
qr = query_hive("SELECT * FROM t_dim_dlr_info LIMIT 3")
test("LIMIT 3 返回 3 行", qr["row_count"] == 3, "")

# 2.9 带 ORDER BY 和 LIMIT
qr = query_hive(
    "SELECT dlr_code, dlr_short_name FROM t_dim_dlr_info ORDER BY dlr_code LIMIT 5"
)
test("ORDER BY + LIMIT 查询", qr["row_count"] == 5, "DLR001-DLR005")


# ============================================================
# 3. 安全防护验证（与主测试一致）
# ============================================================
section("3. 安全防护验证")

test("SELECT 通过白名单", validate_sql_safe("SELECT * FROM t")[0])
test("SHOW 通过白名单", validate_sql_safe("SHOW TABLES")[0])
test("DROP TABLE 被拦截", not validate_sql_safe("DROP TABLE t")[0])
test("INSERT 被拦截", not validate_sql_safe("INSERT INTO t VALUES(1)")[0])
test("DELETE 被拦截", not validate_sql_safe("DELETE FROM t WHERE 1=1")[0])
test("ALTER TABLE 被拦截", not validate_sql_safe("ALTER TABLE t ADD COLUMNS (x int)")[0])
test("CREATE TABLE 被拦截", not validate_sql_safe("CREATE TABLE t (id int)")[0])
test("空SQL 被拦截", not validate_sql_safe("")[0])
test("TRUNCATE 被拦截", not validate_sql_safe("TRUNCATE TABLE t")[0])
test("GRANT 被拦截", not validate_sql_safe("GRANT ALL TO user")[0])
test("UPDATE 被拦截", not validate_sql_safe("UPDATE t SET x=1")[0])


# ============================================================
# 4. SQL 语法验证
# ============================================================
section("4. SQL 语法验证")

vr = validate_sql("SELECT * FROM t_dim_dlr_info")
test("正确 SQL 语法通过", vr["valid"], "")

vr = validate_sql("SELECTT typo FROM t")
test("错误 SQL 语法被拦截", not vr["valid"], "SQLite 语法错误")

vr = validate_sql("DROP TABLE t")
test("高危 SQL 被拦截", not vr["valid"], "安全验证失败")


# ============================================================
# 5. MCP 协议测试
# ============================================================
section("5. MCP 协议测试")

# 5.1 tools/list
resp = handle_request({"method": "tools/list", "id": 1})
tools = resp.get("result", {}).get("tools", [])
tool_names = [t["name"] for t in tools]
test("tools/list 返回 5 个工具", len(tools) == 5, f"工具: {', '.join(tool_names)}")
test("包含 query_hive", "query_hive" in tool_names, "")
test("包含 validate_sql", "validate_sql" in tool_names, "")
test("包含 list_tables", "list_tables" in tool_names, "")
test("包含 describe_table", "describe_table" in tool_names, "")
test("包含 search_columns", "search_columns" in tool_names, "")

# 5.2 tools/call - query_hive
resp = handle_request({
    "method": "tools/call", "id": 2,
    "params": {"name": "query_hive", "arguments": {"sql": "SELECT COUNT(*) AS cnt FROM t_dim_dlr_info"}}
})
test("query_hive 返回结果", not resp.get("result", {}).get("isError", True), "")

# 5.3 tools/call - 空SQL
resp = handle_request({
    "method": "tools/call", "id": 3,
    "params": {"name": "query_hive", "arguments": {"sql": ""}}
})
test("空SQL 返回错误", resp.get("result", {}).get("isError", False), "")

# 5.4 tools/call - DDL 拦截
resp = handle_request({
    "method": "tools/call", "id": 4,
    "params": {"name": "query_hive", "arguments": {"sql": "DROP TABLE t"}}
})
test("DDL SQL 被拦截", resp.get("result", {}).get("isError", False), "安全验证生效")

# 5.5 tools/call - list_tables
resp = handle_request({
    "method": "tools/call", "id": 5,
    "params": {"name": "list_tables", "arguments": {}}
})
test("list_tables 返回结果", not resp.get("result", {}).get("isError", True), "")

# 5.6 tools/call - describe_table
resp = handle_request({
    "method": "tools/call", "id": 6,
    "params": {"name": "describe_table", "arguments": {"table_name": "t_dim_dlr_info"}}
})
test("describe_table 返回结果", not resp.get("result", {}).get("isError", True), "")

# 5.7 tools/call - 非法表名
resp = handle_request({
    "method": "tools/call", "id": 7,
    "params": {"name": "describe_table", "arguments": {"table_name": "; DROP TABLE"}}
})
test("非法表名被拦截", resp.get("result", {}).get("isError", False), "标识符校验生效")

# 5.8 tools/call - search_columns
resp = handle_request({
    "method": "tools/call", "id": 8,
    "params": {"name": "search_columns", "arguments": {"keyword": "dlr_code"}}
})
test("search_columns 返回结果", not resp.get("result", {}).get("isError", True), "")

# 5.9 initialize
resp = handle_request({"method": "initialize", "id": 9})
test("initialize 协议响应", resp.get("result", {}).get("protocolVersion") == "2024-11-05", "")

# 5.10 未知工具
resp = handle_request({
    "method": "tools/call", "id": 10,
    "params": {"name": "unknown_tool", "arguments": {}}
})
test("未知工具返回错误", resp.get("result", {}).get("isError", True), "")


# ============================================================
# 测试报告
# ============================================================
section("测试报告")
print(f"\n  总用例: {test_count}")
print(f"  通过:   {pass_count} {PASS}")
print(f"  失败:   {test_count - pass_count} {FAIL if test_count - pass_count > 0 else ''}")
print(f"  通过率: {pass_count/test_count*100:.1f}%")
print()

if test_count == pass_count:
    print("  🎉 所有测试通过！Mock 系统已就绪。")
    print()
    print("  📌 模拟数据概要：")
    print("     - 4 张表（线索/业绩/经销商/车系）")
    print("     - 10 家门店（北京/上海/广州/深圳/成都/杭州/武汉/南京/重庆/西安）")
    print("     - 20 条线索记录")
    print("     - 39 条业绩记录（大定/退订/锁单/交车）")
    print("     - 7 个车系（汉/唐/宋PLUS/秦PLUS/海豹/海豚/元PLUS）")
    print()
    print("  📌 可测试的查询场景：")
    print("     - \"各门店线索量排名\"")
    print("     - \"本月大定净量前十的门店\"")
    print("     - \"各品牌/车系销量排名\"")
    print("     - \"各区域/省份门店分布\"")
    print("     - \"各门店交车完成率\"")
    print()
    print("  📌 切换到 Mock 模式：")
    print("     将 opencode.mock.json 重命名为 opencode.json 即可")
    print("     或在 OpenCode 中直接加载 opencode.mock.json")
else:
    print(f"  ⚠️  {test_count - pass_count} 个测试失败，需要检查。")
    for r in results:
        if r["status"] == "FAIL":
            print(f"    - {r['name']}: {r['detail']}")

print(f"\n{'='*60}")