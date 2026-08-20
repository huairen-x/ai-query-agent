"""
Hive MCP Server - 智能问数查询执行器
协议：MCP over stdio JSON-RPC
功能：执行 Hive/Impala SQL 查询，返回格式化结果
"""
from __future__ import annotations
import sys
import json
import os
import re
import logging
import queue
import threading
import time
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
logger = logging.getLogger("hive-mcp")

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
    def __init__(self, min_size=2, max_size=POOL_SIZE, timeout=120):
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
                        raise RuntimeError("连接池已满，无法获取连接")
            yield conn
        finally:
            if conn:
                try:
                    self._pool.put(conn, block=False)
                except queue.Full:
                    conn.close()
                    with self._lock:
                        self._created -= 1

    def close_all(self):
        while not self._pool.empty():
            try:
                conn = self._pool.get(block=False)
                conn.close()
            except (queue.Empty, Exception):
                break


# 全局连接池（延迟初始化）
_pool = None


def get_pool():
    global _pool
    if _pool is None:
        _pool = ConnectionPool()
    return _pool


# ============================================================
# SQL 白名单验证
# ============================================================
ALLOWED_STATEMENTS = {
    "SELECT", "SHOW", "DESCRIBE", "EXPLAIN", "WITH", "VALUES"
}

# 高危关键字匹配
DANGEROUS_PATTERNS = [
    re.compile(r'\bDROP\s+(TABLE|DATABASE|VIEW|FUNCTION)\b', re.IGNORECASE),
    re.compile(r'\bALTER\s+(TABLE|DATABASE|VIEW)\b', re.IGNORECASE),
    re.compile(r'\bTRUNCATE\s+TABLE\b', re.IGNORECASE),
    re.compile(r'\bDELETE\s+FROM\b', re.IGNORECASE),
    re.compile(r'\bINSERT\s+(INTO|OVERWRITE)\b', re.IGNORECASE),
    re.compile(r'\bUPDATE\s+\w+\s+SET\b', re.IGNORECASE),
    re.compile(r'\bCREATE\s+(TABLE|DATABASE|VIEW|FUNCTION)\b', re.IGNORECASE),
    re.compile(r'\bGRANT\b|\bREVOKE\b', re.IGNORECASE),
]


def validate_sql_safe(sql: str) -> tuple[bool, str]:
    """验证 SQL 是否安全可执行，返回 (is_safe, error_message)"""
    if not sql or not sql.strip():
        return False, "SQL 语句不能为空"

    sql_stripped = sql.strip().rstrip(";")

    # 检查高危操作
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(sql_stripped):
            return False, f"禁止执行高危操作: {pattern.pattern}"

    # 检查语句类型
    first_word = sql_stripped.split(None, 1)[0].upper()
    if first_word not in ALLOWED_STATEMENTS:
        return False, f"不允许的语句类型: {first_word}，仅允许 {', '.join(sorted(ALLOWED_STATEMENTS))}"

    return True, ""


# ============================================================
# 查询执行
# ============================================================
def query_hive(sql: str, max_rows: int = 200) -> dict:
    """执行 Hive SQL，返回结构化结果"""
    is_safe, err_msg = validate_sql_safe(sql)
    if not is_safe:
        raise ValueError(f"SQL 安全验证失败: {err_msg}")

    pool = get_pool()
    with pool.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchmany(max_rows)
        has_more = len(rows) == max_rows

        # 格式化输出
        lines = []
        header = "| " + " | ".join(columns) + " |"
        sep = "|" + "|".join(["---"] * len(columns)) + "|"
        lines.append(header)
        lines.append(sep)
        for row in rows:
            vals = [str(v) if v is not None else "NULL" for v in row]
            lines.append("| " + " | ".join(vals) + " |")

        result_text = "\n".join(lines)
        if has_more:
            result_text += f"\n... (仅展示前 {max_rows} 行，超出部分已截断)"

        cursor.close()
        return {
            "columns": columns,
            "rows": [dict(zip(columns, [str(v) if v is not None else None for v in row])) for row in rows],
            "row_count": len(rows),
            "has_more": has_more,
            "text": result_text
        }


def validate_sql(sql: str) -> dict:
    """验证 SQL 语法正确性"""
    is_safe, err_msg = validate_sql_safe(sql)
    if not is_safe:
        return {"valid": False, "message": f"SQL 安全验证失败: {err_msg}"}

    pool = get_pool()
    with pool.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"EXPLAIN {sql}")
        cursor.close()
        return {"valid": True, "message": "SQL 语法验证通过"}


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
                        "name": "query_hive",
                        "description": "安全执行 Hive SQL 查询（仅允许 SELECT/SHOW/DESCRIBE/EXPLAIN），返回 Markdown 表格结果",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sql": {
                                    "type": "string",
                                    "description": "要执行的 Hive SQL 语句（仅允许 SELECT/SHOW/DESCRIBE/EXPLAIN）"
                                },
                                "max_rows": {
                                    "type": "integer",
                                    "description": "最大返回行数，默认200",
                                    "default": 200
                                }
                            },
                            "required": ["sql"]
                        }
                    },
                    {
                        "name": "validate_sql",
                        "description": "验证 SQL 语法正确性和安全性，不执行查询",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sql": {
                                    "type": "string",
                                    "description": "要验证的 Hive SQL 语句"
                                }
                            },
                            "required": ["sql"]
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
            if tool_name == "query_hive":
                sql = tool_args.get("sql", "")
                if not sql:
                    raise ValueError("参数 sql 不能为空")
                max_rows = int(tool_args.get("max_rows", 200))
                logger.info(f"执行查询: {sql[:200]}...")
                result = query_hive(sql, max_rows)
                logger.info(f"查询完成: {result['row_count']} 行")
                _resp = {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": result["text"]}], "isError": False}
                }

            elif tool_name == "validate_sql":
                sql = tool_args.get("sql", "")
                result = validate_sql(sql)
                _resp = {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": result["message"]}],
                        "isError": not result["valid"]
                    }
                }

            else:
                _resp = {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": f"未知工具: {tool_name}"}],
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
    logger.info("Hive MCP Server 已启动")
    try:
        # 初始化连接池
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