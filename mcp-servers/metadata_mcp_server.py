"""
元数据 MCP Server - 智能问数表结构查询
协议：MCP over stdio JSON-RPC
功能：查询 Hive 表结构、字段信息、分区信息（带缓存）
"""
from __future__ import annotations
import sys
import json
import os
import re
import logging
import time
import queue
import threading
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit_logger import log_query

# ============================================================
# 日志配置
# ============================================================
LOG_FORMAT = '{"time":"%(asctime)s","level":"%(levelname)s","name":"%(name)s","message":"%(message)s"}'
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stderr
)
logger = logging.getLogger("metadata-mcp")

# ============================================================
# 连接池
# ============================================================
POOL_SIZE = 5


def get_env_or_die(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(f"缺少环境变量: {key}")
    return val


# 延迟导入：仅当真正需要连接 Hive 时才加载 impyla
_impala_connect = None
def _get_connect():
    global _impala_connect
    if _impala_connect is None:
        from impala.dbapi import connect as _c
        _impala_connect = _c
    return _impala_connect


class ConnectionPool:
    """Impala 连接池"""
    def __init__(self, min_size=2, max_size=POOL_SIZE, timeout=30):
        self._pool = queue.Queue(maxsize=max_size)
        self._max_size = max_size
        self._timeout = timeout
        self._lock = threading.Lock()
        self._created = 0
        # 预创建最小连接数（容错：impyla 未安装时跳过）
        for _ in range(min_size):
            try:
                conn = self._create_connection()
                self._pool.put(conn)
                self._created += 1
            except ImportError:
                logger.warning("impyla 未安装，跳过连接预创建")
                break
            except Exception as e:
                logger.warning(f"连接预创建失败: {e}")
                break

    def _create_connection(self):
        return _get_connect()(
            host=get_env_or_die("HIVE_HOST"),
            port=int(os.environ.get("HIVE_PORT", "10000")),
            user=os.environ.get("HIVE_USER", ""),
            password=os.environ.get("HIVE_PASSWORD", ""),
            database=os.environ.get("HIVE_DATABASE", "dndc_dw"),
            auth_mechanism=os.environ.get("HIVE_AUTH", "PLAIN"),
            timeout=self._timeout
        )

    @contextmanager
    def get_connection(self):
        conn = None
        try:
            try:
                conn = self._pool.get(block=True, timeout=5)
            except queue.Empty:
                with self._lock:
                    if self._created < self._max_size:
                        conn = self._create_connection()
                        self._created += 1
                    else:
                        raise RuntimeError("连接池已满")
            yield conn
        finally:
            if conn:
                try:
                    self._pool.put(conn, block=False)
                except queue.Full:
                    conn.close()
                    with self._lock:
                        self._created -= 1


_pool = None


def get_pool():
    global _pool
    if _pool is None:
        _pool = ConnectionPool()
    return _pool


# ============================================================
# 元数据缓存（TTL 60秒）
# ============================================================
class TTLCache:
    """带 TTL 的简单内存缓存"""
    def __init__(self, ttl=60):
        self._cache = {}
        self._ttl = ttl

    def get(self, key):
        entry = self._cache.get(key)
        if entry and time.time() - entry["time"] < self._ttl:
            return entry["value"]
        if entry:
            del self._cache[key]
        return None

    def set(self, key, value):
        self._cache[key] = {"value": value, "time": time.time()}

    def clear(self):
        self._cache.clear()


_cache = TTLCache(ttl=60)

# ============================================================
# SQL 注入防护 - 表名/库名校验
# ============================================================
# 只允许字母、数字、下划线
VALID_IDENTIFIER = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def validate_identifier(name: str, label: str = "标识符") -> str:
    """验证标识符（表名、库名）是否合法，防止 SQL 注入"""
    if not name or not name.strip():
        raise ValueError(f"{label} 不能为空")
    name = name.strip()
    if not VALID_IDENTIFIER.match(name):
        raise ValueError(f"{label} 包含非法字符: {name}")
    return name


# ============================================================
# 元数据查询函数
# ============================================================
def list_tables(database: str = None) -> dict:
    """列出指定数据库的所有表（带缓存）"""
    db = database or os.environ.get("HIVE_DATABASE", "dndc_dw")
    db = validate_identifier(db, "数据库名")

    cache_key = f"list_tables:{db}"
    cached = _cache.get(cache_key)
    if cached:
        return cached

    pool = get_pool()
    with pool.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SHOW TABLES IN {db}")
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()

    result = {"tables": tables, "database": db, "count": len(tables)}
    _cache.set(cache_key, result)
    return result


def describe_table(table_name: str, database: str = None) -> dict:
    """描述表结构：字段名、类型、注释（带缓存）"""
    db = database or os.environ.get("HIVE_DATABASE", "dndc_dw")

    # 解析 full_name
    if "." in table_name:
        parts = table_name.split(".", 1)
        db = validate_identifier(parts[0], "数据库名")
        tbl = validate_identifier(parts[1], "表名")
        full_name = f"{db}.{tbl}"
    else:
        tbl = validate_identifier(table_name, "表名")
        db = validate_identifier(db, "数据库名")
        full_name = f"{db}.{tbl}"

    cache_key = f"describe:{full_name}"
    cached = _cache.get(cache_key)
    if cached:
        return cached

    pool = get_pool()
    with pool.get_connection() as conn:
        cursor = conn.cursor()

        # 字段信息
        cursor.execute(f"DESCRIBE {full_name}")
        raw_cols = cursor.fetchall()
        columns = []
        for row in raw_cols:
            col_name = row[0].strip() if row[0] else ""
            if not col_name or col_name == "" or col_name.startswith("#"):
                continue
            col_type = row[1] if len(row) > 1 else ""
            col_comment = row[2] if len(row) > 2 else ""
            if col_name and col_type and not col_name.startswith("Partition"):
                columns.append({
                    "name": col_name,
                    "type": col_type,
                    "comment": col_comment
                })

        # 分区信息
        partitions = []
        try:
            cursor.execute(f"SHOW PARTITIONS {full_name}")
            raw = cursor.fetchall()
            partitions = [row[0] if isinstance(row, tuple) else row for row in raw[:20]]
        except Exception as e:
            logger.warning(f"获取分区信息失败: {e}")

        # 表属性
        props = {}
        try:
            cursor.execute(f"SHOW TBLPROPERTIES {full_name}")
            props = {row[0]: row[1] for row in cursor.fetchall()}
        except Exception as e:
            logger.warning(f"获取表属性失败: {e}")

        cursor.close()

    result = {
        "table": full_name,
        "columns": columns,
        "column_count": len(columns),
        "partitions": partitions,
        "properties": props
    }
    _cache.set(cache_key, result)
    return result


def search_columns(keyword: str, database: str = None) -> dict:
    """跨表搜索包含指定关键字的字段（带缓存）"""
    db = database or os.environ.get("HIVE_DATABASE", "dndc_dw")
    db = validate_identifier(db, "数据库名")

    cache_key = f"search:{db}:{keyword}"
    cached = _cache.get(cache_key)
    if cached:
        return cached

    pool = get_pool()
    with pool.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SHOW TABLES IN {db}")
        all_tables = [row[0] for row in cursor.fetchall()]

        results = []
        keyword_lower = keyword.lower()

        for tbl in all_tables:
            try:
                cursor.execute(f"DESCRIBE {db}.{tbl}")
                for row in cursor.fetchall():
                    col_name = row[0].strip() if row[0] else ""
                    col_type = row[1] if len(row) > 1 else ""
                    col_comment = row[2] if len(row) > 2 else ""
                    if keyword_lower in col_name.lower() or keyword_lower in col_comment.lower():
                        results.append({
                            "table": tbl,
                            "column": col_name,
                            "type": col_type,
                            "comment": col_comment
                        })
            except Exception:
                continue

        cursor.close()

    result = {
        "keyword": keyword,
        "database": db,
        "matches": results,
        "count": len(results)
    }
    _cache.set(cache_key, result)
    return result


# ============================================================
# MCP 请求处理器
# ============================================================
def handle_request(request: dict) -> dict:
    method = request.get("method", "")
    params = request.get("params", {})
    arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
    request_id = request.get("id")

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "list_tables",
                        "description": "列出指定数据库的所有表（带缓存，TTL 60s）",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "database": {
                                    "type": "string",
                                    "description": "数据库名称，默认 HIVE_DATABASE 环境变量"
                                }
                            }
                        }
                    },
                    {
                        "name": "describe_table",
                        "description": "查看表结构：字段名、类型、注释、分区信息、主外键（带缓存，TTL 60s）",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "table_name": {
                                    "type": "string",
                                    "description": "表名，如 t_dw_nev_sac_sale_clue_test"
                                },
                                "database": {
                                    "type": "string",
                                    "description": "数据库名称，默认 HIVE_DATABASE 环境变量"
                                }
                            },
                            "required": ["table_name"]
                        }
                    },
                    {
                        "name": "search_columns",
                        "description": "跨表搜索包含指定关键字的字段（带缓存，TTL 60s）",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "keyword": {
                                    "type": "string",
                                    "description": "搜索关键词，如 clue_id、dlr_code"
                                },
                                "database": {
                                    "type": "string",
                                    "description": "数据库名称，默认 HIVE_DATABASE 环境变量"
                                }
                            },
                            "required": ["keyword"]
                        }
                    }
                ]
            }
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = arguments if isinstance(arguments, dict) else {}
        _start = time.time()
        _resp = None

        try:
            if tool_name == "list_tables":
                result = list_tables(tool_args.get("database"))
                text = f"📋 数据库 `{result['database']}` 共有 {result['count']} 张表：\n\n"
                for t in result["tables"]:
                    text += f"- {t}\n"
                _resp = {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": text}], "isError": False}
                }

            elif tool_name == "describe_table":
                result = describe_table(tool_args.get("table_name", ""), tool_args.get("database"))
                text = f"## 表结构: {result['table']}\n\n"
                text += "### 字段列表\n"
                text += "| 字段名 | 类型 | 注释 |\n| --- | --- | --- |\n"
                for c in result["columns"]:
                    text += f"| {c['name']} | {c['type']} | {c['comment']} |\n"
                if result["partitions"]:
                    text += f"\n### 分区\n```\n{chr(10).join(result['partitions'][:5])}\n```\n"
                if result["properties"]:
                    pk = result["properties"].get("primary.key", "")
                    fk = {k: v for k, v in result["properties"].items() if k.startswith("foreign.key")}
                    if pk:
                        text += f"\n### 主键\n`{pk}`\n"
                    if fk:
                        text += "\n### 外键\n"
                        for k, v in fk.items():
                            text += f"- `{k}` = `{v}`\n"
                _resp = {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": text}], "isError": False}
                }

            elif tool_name == "search_columns":
                result = search_columns(tool_args.get("keyword", ""), tool_args.get("database"))
                text = f"🔍 搜索 `{result['keyword']}` 在 `{result['database']}` 中找到 {result['count']} 个匹配：\n\n"
                text += "| 表名 | 字段名 | 类型 | 注释 |\n| --- | --- | --- | --- |\n"
                for m in result["matches"]:
                    text += f"| {m['table']} | {m['column']} | {m['type']} | {m['comment']} |\n"
                _resp = {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": text}], "isError": False}
                }

            else:
                _resp = {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": f"未知工具: {tool_name}"}],
                        "isError": True
                    }
                }

        except ValueError as e:
            logger.warning(f"输入验证错误: {e}")
            _resp = {
                "jsonrpc": "2.0", "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"输入错误: {e}"}],
                    "isError": True
                }
            }
        except Exception as e:
            logger.error(f"执行错误: {e}", exc_info=True)
            _resp = {
                "jsonrpc": "2.0", "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"执行错误: {str(e)}"}],
                    "isError": True
                }
            }

        # 审计日志
        _elapsed = (time.time() - _start) * 1000
        log_query(tool_name, tool_args, _resp, _elapsed)
        return _resp

    elif method == "resources/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"resources": []}}

    elif method == "initialize":
        return {
            "jsonrpc": "2.0", "id": request_id,
            "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}
        }

    else:
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}


def main():
    logger.info("Metadata MCP Server 已启动")
    try:
        get_pool()
        logger.info("连接池初始化完成")
    except Exception as e:
        logger.error(f"连接池初始化失败: {e}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析错误: {e}")
        except Exception as e:
            logger.error(f"处理异常: {e}", exc_info=True)


if __name__ == "__main__":
    main()