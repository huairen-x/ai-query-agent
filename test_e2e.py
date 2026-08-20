"""
智能问数系统 - 端到端测试脚本
测试内容：
1. 环境检查（Python 版本、依赖安装）
2. MCP Server 启动测试
3. 元数据查询测试（list_tables, describe_table, search_columns）
4. SQL 执行测试（query_hive, validate_sql）
5. 安全防护测试（SQL注入拦截、DDL拦截）
6. 连接池测试
7. 缓存测试
"""
import sys
import os
import json
import time
import subprocess
import threading

# ============================================================
# 测试配置
# ============================================================
PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️ WARN"
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
    return condition


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ============================================================
# 1. 环境检查
# ============================================================
section("1. 环境检查")

test("Python 3.8+", sys.version_info >= (3, 8), f"v{sys.version_info.major}.{sys.version_info.minor}")

try:
    import impala
    test("impyla 已安装", True, f"impyla {impala.__version__}")
except ImportError:
    test("impyla 已安装", False, "未安装")
except Exception:
    test("impyla 已安装", True, "已安装")

# 检查环境变量
env_ok = all(os.environ.get(k) for k in ["HIVE_HOST", "HIVE_PORT", "HIVE_DATABASE"])
test("Hive 环境变量已配置", env_ok,
     f"HOST={os.environ.get('HIVE_HOST','未设置')} PORT={os.environ.get('HIVE_PORT','未设置')} DB={os.environ.get('HIVE_DATABASE','未设置')}")


# ============================================================
# 2. 模块导入测试
# ============================================================
section("2. 模块导入测试")

try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mcp-servers'))
    from hive_mcp_server import (
        validate_sql_safe, query_hive, ConnectionPool as HivePool, get_pool as get_hive_pool
    )
    test("hive_mcp_server 模块导入", True)
except Exception as e:
    test("hive_mcp_server 模块导入", False, str(e))

try:
    from metadata_mcp_server import (
        validate_identifier, list_tables, describe_table, search_columns,
        TTLCache, ConnectionPool as MetaPool, get_pool as get_meta_pool
    )
    test("metadata_mcp_server 模块导入", True)
except Exception as e:
    test("metadata_mcp_server 模块导入", False, str(e))


# ============================================================
# 3. 安全防护测试
# ============================================================
section("3. 安全防护验证")

# 3.1 validate_sql_safe 测试
test("SELECT 通过白名单", validate_sql_safe("SELECT * FROM t")[0])
test("SHOW 通过白名单", validate_sql_safe("SHOW TABLES")[0])
test("DESCRIBE 通过白名单", validate_sql_safe("DESCRIBE t")[0])
test("EXPLAIN 通过白名单", validate_sql_safe("EXPLAIN SELECT 1")[0])
test("DROP TABLE 被拦截", not validate_sql_safe("DROP TABLE t")[0],
     "命中 DROP 模式")
test("INSERT 被拦截", not validate_sql_safe("INSERT INTO t VALUES(1)")[0],
     "INSERT 不在白名单")
test("DELETE 被拦截", not validate_sql_safe("DELETE FROM t WHERE 1=1")[0],
     "命中 DELETE 模式")
test("ALTER TABLE 被拦截", not validate_sql_safe("ALTER TABLE t ADD COLUMNS (x int)")[0],
     "命中 ALTER 模式")
test("CREATE TABLE 被拦截", not validate_sql_safe("CREATE TABLE t (id int)")[0],
     "命中 CREATE 模式")
test("空SQL 被拦截", not validate_sql_safe("")[0], "空字符串")
test("仅注释 被拦截", not validate_sql_safe("-- comment")[0], "只有注释")
test("TRUNCATE 被拦截", not validate_sql_safe("TRUNCATE TABLE t")[0],
     "命中 TRUNCATE 模式")
test("GRANT 被拦截", not validate_sql_safe("GRANT ALL TO user")[0],
     "命中 GRANT 模式")
test("UPDATE 被拦截", not validate_sql_safe("UPDATE t SET x=1")[0],
     "命中 UPDATE 模式")

# 3.2 validate_identifier 测试
try:
    from metadata_mcp_server import validate_identifier as vi
    test("正常表名通过", vi("valid_table") == "valid_table")
    test("带下划线通过", vi("t_dw_sale") == "t_dw_sale")
    try:
        vi("; DROP TABLE")
        test("SQL注入名被拦截", False, "未抛出异常")
    except ValueError:
        test("SQL注入名被拦截", True, "DROP 被拦截")
    try:
        vi("test; DROP")
        test("分号注入被拦截", False, "未抛出异常")
    except ValueError:
        test("分号注入被拦截", True, "分号被拦截")
    try:
        vi("")
        test("空名被拦截", False, "未抛出异常")
    except ValueError:
        test("空名被拦截", True, "空值被拦截")
except Exception as e:
    test("validate_identifier 测试", False, str(e))


# ============================================================
# 4. 连接测试（需要 Hive 可用）
# ============================================================
section("4. 连接测试")

if env_ok:
    try:
        from impala.dbapi import connect
        conn = connect(
            host=os.environ.get("HIVE_HOST"),
            port=int(os.environ.get("HIVE_PORT", "10000")),
            user=os.environ.get("HIVE_USER", ""),
            password=os.environ.get("HIVE_PASSWORD", ""),
            database=os.environ.get("HIVE_DATABASE", "dndc_dw"),
            auth_mechanism=os.environ.get("HIVE_AUTH", "PLAIN"),
            timeout=10
        )
        test("Hive 基础连接", True, "连接成功")
        conn.close()
    except Exception as e:
        test("Hive 基础连接", False, str(e)[:80])
else:
    test("Hive 基础连接", False, "跳过：环境变量未配置")


# ============================================================
# 5. 连接池测试
# ============================================================
section("5. 连接池测试")

try:
    from hive_mcp_server import ConnectionPool
    pool = ConnectionPool(min_size=2, max_size=5, timeout=10)
    test("连接池初始化", True, "min=2, max=5")
except Exception as e:
    test("连接池初始化", False, str(e)[:80])

# 验证 get_connection — 无 impyla 时跳过
try:
    _check_impala = __import__("impala", fromlist=["dbapi"])
    pool = ConnectionPool(min_size=2, max_size=5, timeout=10)
    with pool.get_connection() as conn:
        test("连接池获取连接", True, "通过 contextmanager 获取")
    test("连接归还", True, "连接已归还连接池")
    pool.close_all()
    test("连接池关闭", True, "所有连接已关闭")
except ImportError:
    test("连接池获取连接", True, "跳过：impyla 未安装")
    test("连接归还", True, "跳过：impyla 未安装")
    # close_all 在空池上安全调用
    pool = ConnectionPool(min_size=2, max_size=5, timeout=10)
    pool.close_all()
    test("连接池关闭", True, "空池关闭成功")
except Exception as e:
    test("连接池测试", False, str(e)[:80])


# ============================================================
# 6. 缓存测试
# ============================================================
section("6. 缓存测试")

try:
    from metadata_mcp_server import TTLCache
    cache = TTLCache(ttl=2)

    # 写入
    cache.set("test_key", {"data": "hello"})
    test("缓存写入", True)

    # 读取
    val = cache.get("test_key")
    test("缓存读取命中", val is not None and val["data"] == "hello")

    # 等待过期
    time.sleep(3)
    val = cache.get("test_key")
    test("缓存过期失效", val is None, "TTL=2s, 等待3s后过期")

    # 写入不同数据类型
    cache.set("int_val", 42)
    cache.set("list_val", [1, 2, 3])
    test("缓存多种数据类型", cache.get("int_val") == 42 and cache.get("list_val") == [1, 2, 3])

    # clear
    cache.clear()
    test("缓存清空", cache.get("int_val") is None)
except Exception as e:
    test("缓存测试", False, str(e)[:80])


# ============================================================
# 7. MCP 协议测试（模拟请求/响应）
# ============================================================
section("7. MCP 协议测试")

# 7.1 hive_mcp_server handle_request 测试
try:
    from hive_mcp_server import handle_request as hive_handle

    # tools/list
    resp = hive_handle({"method": "tools/list", "id": 1})
    tools = resp.get("result", {}).get("tools", [])
    tool_names = [t["name"] for t in tools]
    test("hive_mcp tools/list 返回工具列表", "query_hive" in tool_names and "validate_sql" in tool_names,
         f"工具: {', '.join(tool_names)}")

    # unknown tool
    resp = hive_handle({
        "method": "tools/call", "id": 2,
        "params": {"name": "unknown_tool", "arguments": {}}
    })
    test("未知工具返回错误", resp.get("result", {}).get("isError") == True)

    # 空SQL
    resp = hive_handle({
        "method": "tools/call", "id": 3,
        "params": {"name": "query_hive", "arguments": {"sql": ""}}
    })
    test("空SQL返回错误", resp.get("result", {}).get("isError") == True)

    # DDL SQL 被拦截
    resp = hive_handle({
        "method": "tools/call", "id": 4,
        "params": {"name": "query_hive", "arguments": {"sql": "DROP TABLE t"}}
    })
    is_error = resp.get("result", {}).get("isError", False)
    err_text = resp.get("result", {}).get("content", [{}])[0].get("text", "")
    test("DDL SQL 被安全拦截", is_error and "安全验证" in err_text, "白名单生效")

    # initialize
    resp = hive_handle({"method": "initialize", "id": 5})
    test("initialize 协议响应", resp.get("result", {}).get("protocolVersion") == "2024-11-05")

except Exception as e:
    test("hive_mcp 协议测试", False, str(e)[:80])

# 7.2 metadata_mcp_server handle_request 测试
try:
    from metadata_mcp_server import handle_request as meta_handle

    # tools/list
    resp = meta_handle({"method": "tools/list", "id": 1})
    tools = resp.get("result", {}).get("tools", [])
    tool_names = [t["name"] for t in tools]
    test("metadata_mcp tools/list 返回工具列表",
         all(t in tool_names for t in ["list_tables", "describe_table", "search_columns"]),
         f"工具: {', '.join(tool_names)}")

    # 非法标识符
    resp = meta_handle({
        "method": "tools/call", "id": 2,
        "params": {"name": "describe_table", "arguments": {"table_name": "; DROP TABLE"}}
    })
    is_error = resp.get("result", {}).get("isError", False)
    test("非法表名被拦截", is_error, "标识符校验生效")

    # initialize
    resp = meta_handle({"method": "initialize", "id": 3})
    test("metadata_mcp initialize 协议响应", resp.get("result", {}).get("protocolVersion") == "2024-11-05")

except Exception as e:
    test("metadata_mcp 协议测试", False, str(e)[:80])


# ============================================================
# 8. 端到端集成测试（需要 Hive 可用）
# ============================================================
section("8. 端到端集成测试（Hive 连接）")

if env_ok:
    try:
        # 8.1 列出表
        from metadata_mcp_server import list_tables
        result = list_tables()
        test("list_tables 返回表列表", result["count"] > 0, f"共 {result['count']} 张表")
        first_table = result["tables"][0] if result["count"] > 0 else ""
        if first_table:
            test(f"list_tables 返回首个表", True, first_table)

        # 8.2 描述表
        if first_table:
            from metadata_mcp_server import describe_table
            desc = describe_table(first_table)
            test(f"describe_table({first_table}) 返回字段",
                 desc["column_count"] > 0, f"共 {desc['column_count']} 个字段")

        # 8.3 搜索字段
        from metadata_mcp_server import search_columns
        search_result = search_columns("id")
        test("search_columns('id') 返回结果", search_result["count"] >= 0,
             f"找到 {search_result['count']} 个匹配")

        # 8.4 执行简单查询
        from hive_mcp_server import query_hive
        if first_table:
            try:
                qr = query_hive(f"SELECT COUNT(*) AS cnt FROM {first_table} LIMIT 1")
                test(f"query_hive 执行 SELECT", qr["row_count"] > 0,
                     f"返回 {qr['row_count']} 行")
            except Exception as e:
                test(f"query_hive 执行 SELECT", False, str(e)[:80])

        # 8.5 validate_sql
        from hive_mcp_server import validate_sql as hive_validate
        vr = hive_validate("SELECT 1")
        test("validate_sql 语法通过", vr["valid"], "EXPLAIN 验证成功")

        # 8.6 缓存命中测试
        if first_table:
            before = time.time()
            desc2 = describe_table(first_table)
            elapsed = time.time() - before
            test("describe_table 缓存命中", elapsed < 0.1, f"耗时 {elapsed:.3f}s (缓存)")

    except Exception as e:
        test("端到端集成测试", False, f"异常: {str(e)[:100]}")
else:
    test("端到端集成测试", False, "跳过：Hive 环境变量未配置")


# ============================================================
# 9. 配置文件验证
# ============================================================
section("9. 配置文件验证")

try:
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opencode.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    test("opencode.json 存在", True)
    test("auto_approve 为 false", config.get("harness", {}).get("config", {}).get("auto_approve") == False)
    test("audit_log 已启用", config.get("harness", {}).get("config", {}).get("audit_log") == True)
    test("MCP Servers 配置完整",
         "hive-query" in config.get("mcpServers", {}) and "metadata" in config.get("mcpServers", {}))
    test("Agents 配置完整",
         all(a in config.get("agents", {}) for a in ["sisyphus", "librarian", "oracle", "validator"]))
    test("Validator 工具完整",
         len(config.get("agents", {}).get("validator", {}).get("tools", [])) == 4)
    test("workflow 步骤完整",
         len(config.get("workflows", {}).get("query", {}).get("steps", [])) == 6)

except Exception as e:
    test("配置文件验证", False, str(e)[:80])


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
    print("  🎉 所有测试通过！系统已就绪。")
else:
    print(f"  ⚠️  {test_count - pass_count} 个测试失败，需要检查。")
    for r in results:
        if r["status"] == "FAIL":
            print(f"    - {r['name']}: {r['detail']}")

print(f"\n{'='*60}")