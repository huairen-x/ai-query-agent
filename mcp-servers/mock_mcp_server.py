"""
Mock MCP Server - 智能问数模拟服务
协议：MCP over stdio JSON-RPC
功能：使用 SQLite 内存数据库模拟 Hive 查询，无需真实 Hive 环境
"""
from __future__ import annotations
import sys
import json
import os
import re
import sqlite3
import logging
import threading
import time

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
logger = logging.getLogger("mock-mcp")

# ============================================================
# 锁（SQLite 连接非线程安全）
# ============================================================
_db_lock = threading.Lock()

# ============================================================
# 模拟数据
# ============================================================
MOCK_TABLES = [
    {
        "name": "t_dw_nev_sac_sale_clue",
        "comment": "线索数据表 - 门店线索记录",
        "columns": [
            ("clue_id", "STRING", "线索ID"),
            ("dlr_code", "STRING", "门店编码"),
            ("dlr_name", "STRING", "门店名称"),
            ("customer_name", "STRING", "客户姓名"),
            ("customer_phone", "STRING", "客户电话"),
            ("car_series", "STRING", "意向车系"),
            ("channel", "STRING", "渠道来源"),
            ("status", "STRING", "线索状态"),
            ("create_date", "STRING", "创建日期"),
            ("arrive_date", "STRING", "到店日期"),
        ],
        "primary_key": "clue_id",
        "foreign_keys": {"dlr_code": "t_dim_dlr_info"}
    },
    {
        "name": "t_dw_nev_sac_sale_achievement",
        "comment": "业绩数据表 - 大定/锁单/交车记录",
        "columns": [
            ("ach_id", "STRING", "业绩ID"),
            ("dlr_code", "STRING", "门店编码"),
            ("ach_type_code", "INT", "业绩类型编码（1001大定/1002大定退订/1003锁单/1004锁单作废/1005交车/1006交车销退）"),
            ("ach_type", "STRING", "业绩类型名称"),
            ("amount", "INT", "数量"),
            ("car_series", "STRING", "车系"),
            ("ach_date", "STRING", "业绩日期"),
            ("clue_id", "STRING", "关联线索ID"),
        ],
        "primary_key": "ach_id",
        "foreign_keys": {"dlr_code": "t_dim_dlr_info", "clue_id": "t_dw_nev_sac_sale_clue"}
    },
    {
        "name": "t_dim_dlr_info",
        "comment": "经销商维度表",
        "columns": [
            ("dlr_code", "STRING", "门店编码"),
            ("dlr_short_name", "STRING", "门店简称"),
            ("city", "STRING", "城市"),
            ("province", "STRING", "省份"),
            ("brand", "STRING", "品牌"),
            ("level", "STRING", "门店级别"),
        ],
        "primary_key": "dlr_code",
        "foreign_keys": {}
    },
    {
        "name": "t_dim_car_config",
        "comment": "车系配置维度表",
        "columns": [
            ("series_id", "STRING", "车系ID"),
            ("series_code", "STRING", "车系编码"),
            ("series_name", "STRING", "车系名称"),
            ("brand_code", "STRING", "品牌编码"),
            ("brand_name", "STRING", "品牌名称"),
        ],
        "primary_key": "series_id",
        "foreign_keys": {}
    },
]

# 模拟数据行
MOCK_DATA = {
    "t_dim_dlr_info": [
        ("DLR001", "北京朝阳店", "北京", "北京市", "比亚迪", "A级"),
        ("DLR002", "上海浦东店", "上海", "上海市", "比亚迪", "A级"),
        ("DLR003", "广州天河店", "广州", "广东省", "比亚迪", "A级"),
        ("DLR004", "深圳南山店", "深圳", "广东省", "比亚迪", "B级"),
        ("DLR005", "成都锦江店", "成都", "四川省", "比亚迪", "B级"),
        ("DLR006", "杭州西湖店", "杭州", "浙江省", "比亚迪", "A级"),
        ("DLR007", "武汉光谷店", "武汉", "湖北省", "比亚迪", "B级"),
        ("DLR008", "南京鼓楼店", "南京", "江苏省", "比亚迪", "B级"),
        ("DLR009", "重庆渝北店", "重庆", "重庆市", "比亚迪", "C级"),
        ("DLR010", "西安雁塔店", "西安", "陕西省", "比亚迪", "C级"),
    ],
    "t_dim_car_config": [
        ("S001", "SERIES_001", "汉", "BYD", "比亚迪"),
        ("S002", "SERIES_002", "唐", "BYD", "比亚迪"),
        ("S003", "SERIES_003", "宋PLUS", "BYD", "比亚迪"),
        ("S004", "SERIES_004", "秦PLUS", "BYD", "比亚迪"),
        ("S005", "SERIES_005", "海豹", "BYD", "比亚迪"),
        ("S006", "SERIES_006", "海豚", "BYD", "比亚迪"),
        ("S007", "SERIES_007", "元PLUS", "BYD", "比亚迪"),
    ],
    "t_dw_nev_sac_sale_clue": [
        ("CL001", "DLR001", "北京朝阳店", "张三", "13800001001", "汉", "线上", "已到店", "2026-07-01", "2026-07-03"),
        ("CL002", "DLR001", "北京朝阳店", "李四", "13800001002", "唐", "线下", "已成交", "2026-07-02", "2026-07-04"),
        ("CL003", "DLR002", "上海浦东店", "王五", "13800002001", "宋PLUS", "线上", "已到店", "2026-07-01", "2026-07-05"),
        ("CL004", "DLR002", "上海浦东店", "赵六", "13800002002", "汉", "转介绍", "已成交", "2026-07-03", "2026-07-06"),
        ("CL005", "DLR003", "广州天河店", "钱七", "13800003001", "海豹", "线上", "已到店", "2026-07-02", "2026-07-04"),
        ("CL006", "DLR003", "广州天河店", "孙八", "13800003002", "秦PLUS", "线下", "新客", "2026-07-05", None),
        ("CL007", "DLR004", "深圳南山店", "周九", "13800004001", "汉", "线上", "已成交", "2026-07-01", "2026-07-02"),
        ("CL008", "DLR004", "深圳南山店", "吴十", "13800004002", "唐", "转介绍", "已到店", "2026-07-04", "2026-07-06"),
        ("CL009", "DLR005", "成都锦江店", "郑一", "13800005001", "海豚", "线上", "新客", "2026-07-03", None),
        ("CL010", "DLR005", "成都锦江店", "冯二", "13800005002", "元PLUS", "线下", "已成交", "2026-07-01", "2026-07-02"),
        ("CL011", "DLR006", "杭州西湖店", "陈三", "13800006001", "宋PLUS", "线上", "已到店", "2026-07-02", "2026-07-05"),
        ("CL012", "DLR006", "杭州西湖店", "褚四", "13800006002", "汉", "转介绍", "已成交", "2026-07-06", "2026-07-08"),
        ("CL013", "DLR007", "武汉光谷店", "卫五", "13800007001", "海豹", "线上", "新客", "2026-07-05", None),
        ("CL014", "DLR007", "武汉光谷店", "蒋六", "13800007002", "秦PLUS", "线下", "已到店", "2026-07-03", "2026-07-06"),
        ("CL015", "DLR008", "南京鼓楼店", "沈七", "13800008001", "唐", "线上", "已成交", "2026-07-04", "2026-07-05"),
        ("CL016", "DLR008", "南京鼓楼店", "韩八", "13800008002", "汉", "转介绍", "已到店", "2026-07-07", "2026-07-09"),
        ("CL017", "DLR009", "重庆渝北店", "杨九", "13800009001", "海豚", "线上", "新客", "2026-07-06", None),
        ("CL018", "DLR009", "重庆渝北店", "朱十", "13800009002", "元PLUS", "线下", "已到店", "2026-07-02", "2026-07-04"),
        ("CL019", "DLR010", "西安雁塔店", "秦一", "13800010001", "宋PLUS", "线上", "已成交", "2026-07-03", "2026-07-05"),
        ("CL020", "DLR010", "西安雁塔店", "许二", "13800010002", "汉", "转介绍", "已到店", "2026-07-05", "2026-07-07"),
    ],
    "t_dw_nev_sac_sale_achievement": [
        # 北京朝阳店 - 大定3单，退订1单，锁单2单，交车2单
        ("ACH001", "DLR001", 1001, "大定", 1, "汉", "2026-07-05", "CL001"),
        ("ACH002", "DLR001", 1001, "大定", 1, "唐", "2026-07-06", "CL002"),
        ("ACH003", "DLR001", 1002, "大定退订", 1, "汉", "2026-07-08", "CL001"),
        ("ACH004", "DLR001", 1001, "大定", 1, "宋PLUS", "2026-07-10", "CL003"),
        ("ACH005", "DLR001", 1003, "锁单", 1, "唐", "2026-07-12", "CL002"),
        ("ACH006", "DLR001", 1003, "锁单", 1, "宋PLUS", "2026-07-15", "CL003"),
        ("ACH007", "DLR001", 1005, "交车", 1, "唐", "2026-07-20", "CL002"),
        ("ACH008", "DLR001", 1005, "交车", 1, "宋PLUS", "2026-07-25", "CL003"),
        # 上海浦东店 - 大定2单，锁单1单，交车1单
        ("ACH009", "DLR002", 1001, "大定", 1, "宋PLUS", "2026-07-06", "CL003"),
        ("ACH010", "DLR002", 1001, "大定", 1, "汉", "2026-07-08", "CL004"),
        ("ACH011", "DLR002", 1003, "锁单", 1, "汉", "2026-07-15", "CL004"),
        ("ACH012", "DLR002", 1005, "交车", 1, "汉", "2026-07-22", "CL004"),
        # 广州天河店 - 大定1单，退订1单，锁单1单
        ("ACH013", "DLR003", 1001, "大定", 1, "海豹", "2026-07-06", "CL005"),
        ("ACH014", "DLR003", 1002, "大定退订", 1, "海豹", "2026-07-10", "CL005"),
        ("ACH015", "DLR003", 1003, "锁单", 1, "海豹", "2026-07-18", "CL005"),
        # 深圳南山店 - 大定2单，锁单2单，交车2单
        ("ACH016", "DLR004", 1001, "大定", 1, "汉", "2026-07-03", "CL007"),
        ("ACH017", "DLR004", 1001, "大定", 1, "唐", "2026-07-08", "CL008"),
        ("ACH018", "DLR004", 1003, "锁单", 1, "汉", "2026-07-10", "CL007"),
        ("ACH019", "DLR004", 1003, "锁单", 1, "唐", "2026-07-12", "CL008"),
        ("ACH020", "DLR004", 1005, "交车", 1, "汉", "2026-07-18", "CL007"),
        ("ACH021", "DLR004", 1005, "交车", 1, "唐", "2026-07-22", "CL008"),
        # 成都锦江店 - 大定1单，锁单1单，交车1单
        ("ACH022", "DLR005", 1001, "大定", 1, "元PLUS", "2026-07-05", "CL010"),
        ("ACH023", "DLR005", 1003, "锁单", 1, "元PLUS", "2026-07-12", "CL010"),
        ("ACH024", "DLR005", 1005, "交车", 1, "元PLUS", "2026-07-20", "CL010"),
        # 杭州西湖店 - 大定2单，锁单1单，交车1单
        ("ACH025", "DLR006", 1001, "大定", 1, "宋PLUS", "2026-07-06", "CL011"),
        ("ACH026", "DLR006", 1001, "大定", 1, "汉", "2026-07-10", "CL012"),
        ("ACH027", "DLR006", 1003, "锁单", 1, "汉", "2026-07-15", "CL012"),
        ("ACH028", "DLR006", 1005, "交车", 1, "汉", "2026-07-25", "CL012"),
        # 武汉光谷店 - 大定1单
        ("ACH029", "DLR007", 1001, "大定", 1, "秦PLUS", "2026-07-08", "CL014"),
        # 南京鼓楼店 - 大定2单，锁单2单，交车1单
        ("ACH030", "DLR008", 1001, "大定", 1, "唐", "2026-07-06", "CL015"),
        ("ACH031", "DLR008", 1001, "大定", 1, "汉", "2026-07-10", "CL016"),
        ("ACH032", "DLR008", 1003, "锁单", 1, "唐", "2026-07-12", "CL015"),
        ("ACH033", "DLR008", 1003, "锁单", 1, "汉", "2026-07-15", "CL016"),
        ("ACH034", "DLR008", 1005, "交车", 1, "唐", "2026-07-22", "CL015"),
        # 重庆渝北店 - 大定1单
        ("ACH035", "DLR009", 1001, "大定", 1, "元PLUS", "2026-07-06", "CL018"),
        # 西安雁塔店 - 大定2单，锁单1单，交车1单
        ("ACH036", "DLR010", 1001, "大定", 1, "宋PLUS", "2026-07-06", "CL019"),
        ("ACH037", "DLR010", 1001, "大定", 1, "汉", "2026-07-08", "CL020"),
        ("ACH038", "DLR010", 1003, "锁单", 1, "宋PLUS", "2026-07-12", "CL019"),
        ("ACH039", "DLR010", 1005, "交车", 1, "宋PLUS", "2026-07-22", "CL019"),
    ],
}


# ============================================================
# SQLite 内存数据库
# ============================================================
def _create_mock_db() -> sqlite3.Connection:
    """创建内存 SQLite 数据库并加载模拟数据"""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row

    for table in MOCK_TABLES:
        col_defs = ", ".join(f"{c[0]} {c[1]}" for c in table["columns"])
        conn.execute(f"CREATE TABLE {table['name']} ({col_defs})")

    for table_name, rows in MOCK_DATA.items():
        if not rows:
            continue
        placeholders = ", ".join(["?" for _ in rows[0]])
        conn.executemany(f"INSERT INTO {table_name} VALUES ({placeholders})", rows)

    conn.commit()
    logger.info(f"模拟数据库已加载: {len(MOCK_TABLES)} 张表, {sum(len(v) for v in MOCK_DATA.values())} 条记录")
    return conn


_db: sqlite3.Connection | None = None


def get_db() -> sqlite3.Connection:
    global _db
    if _db is None:
        _db = _create_mock_db()
    return _db


# ============================================================
# SQL 白名单验证（与正式版一致）
# ============================================================
ALLOWED_STATEMENTS = {"SELECT", "SHOW", "DESCRIBE", "EXPLAIN", "WITH", "VALUES"}
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
    if not sql or not sql.strip():
        return False, "SQL 语句不能为空"
    sql_stripped = sql.strip().rstrip(";")
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(sql_stripped):
            return False, f"禁止执行高危操作: {pattern.pattern}"
    first_word = sql_stripped.split(None, 1)[0].upper()
    if first_word not in ALLOWED_STATEMENTS:
        return False, f"不允许的语句类型: {first_word}"
    return True, ""


def validate_identifier(name: str, label: str = "标识符") -> str:
    if not name or not name.strip():
        raise ValueError(f"{label} 不能为空")
    name = name.strip()
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
        raise ValueError(f"{label} 包含非法字符: {name}")
    return name


# ============================================================
# 虚拟列功能（模拟 Hive 复杂查询返回）
# ============================================================
VIRTUAL_COLUMNS = {
    "ach_type": {
        1001: "大定", 1002: "大定退订", 1003: "锁单",
        1004: "锁单作废", 1005: "交车", 1006: "交车销退",
    }
}


# ============================================================
# 工具函数
# ============================================================
def query_hive(sql: str, max_rows: int = 200) -> dict:
    """执行 SQL 查询（基于 SQLite 模拟）"""
    is_safe, err_msg = validate_sql_safe(sql)
    if not is_safe:
        raise ValueError(f"SQL 安全验证失败: {err_msg}")

    conn = get_db()
    with _db_lock:
        cursor = conn.execute(sql)
        rows = cursor.fetchmany(max_rows)
        columns = [desc[0] for desc in cursor.description]
        has_more = len(rows) == max_rows

        # 格式化为 Markdown 表格
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
            "text": result_text,
        }


def validate_sql(sql: str) -> dict:
    is_safe, err_msg = validate_sql_safe(sql)
    if not is_safe:
        return {"valid": False, "message": f"SQL 安全验证失败: {err_msg}"}
    try:
        conn = get_db()
        with _db_lock:
            conn.execute(f"EXPLAIN {sql}").close()
        return {"valid": True, "message": "SQL 语法验证通过"}
    except Exception as e:
        return {"valid": False, "message": f"SQL 语法错误: {e}"}


# ============================================================
# 元数据函数（返回模拟数据）
# ============================================================
def list_tables(database: str = None) -> dict:
    tables = [t["name"] for t in MOCK_TABLES]
    result = {"tables": tables, "database": database or "mock_db", "count": len(tables)}
    return result


def describe_table(table_name: str, database: str = None) -> dict:
    for table in MOCK_TABLES:
        if table["name"] == table_name or table_name.endswith("." + table["name"]):
            columns = [
                {"name": c[0], "type": c[1], "comment": c[2]}
                for c in table["columns"]
            ]
            result = {
                "table": table_name,
                "columns": columns,
                "column_count": len(columns),
                "partitions": [],
                "properties": {
                    "primary.key": table["primary_key"],
                    **{f"foreign.key.{k}": v for k, v in table["foreign_keys"].items()},
                },
            }
            return result
    raise ValueError(f"表不存在: {table_name}")


def search_columns(keyword: str, database: str = None) -> dict:
    results = []
    keyword_lower = keyword.lower()
    for table in MOCK_TABLES:
        for col in table["columns"]:
            if keyword_lower in col[0].lower() or keyword_lower in col[2].lower():
                results.append({
                    "table": table["name"],
                    "column": col[0],
                    "type": col[1],
                    "comment": col[2],
                })
    return {"keyword": keyword, "database": database or "mock_db", "matches": results, "count": len(results)}


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
                        "description": "执行 SQL 查询（模拟模式），返回 Markdown 表格",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sql": {"type": "string", "description": "SQL 语句"},
                                "max_rows": {"type": "integer", "description": "最大返回行数", "default": 200},
                            },
                            "required": ["sql"],
                        },
                    },
                    {
                        "name": "validate_sql",
                        "description": "验证 SQL 语法正确性",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sql": {"type": "string", "description": "要验证的 SQL 语句"},
                            },
                            "required": ["sql"],
                        },
                    },
                    {
                        "name": "list_tables",
                        "description": "列出所有模拟表",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "database": {"type": "string", "description": "数据库名称（可选）"}
                            },
                        },
                    },
                    {
                        "name": "describe_table",
                        "description": "查看表结构",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "table_name": {"type": "string", "description": "表名"},
                                "database": {"type": "string", "description": "数据库名称（可选）"},
                            },
                            "required": ["table_name"],
                        },
                    },
                    {
                        "name": "search_columns",
                        "description": "跨表搜索字段",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "keyword": {"type": "string", "description": "搜索关键词"},
                                "database": {"type": "string", "description": "数据库名称（可选）"},
                            },
                            "required": ["keyword"],
                        },
                    },
                ]
            },
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
                    "result": {"content": [{"type": "text", "text": result["text"]}], "isError": False},
                }

            elif tool_name == "validate_sql":
                sql = tool_args.get("sql", "")
                result = validate_sql(sql)
                _resp = {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": result["message"]}],
                        "isError": not result["valid"],
                    },
                }

            elif tool_name == "list_tables":
                result = list_tables(tool_args.get("database"))
                text = f"📋 数据库 `mock` 共有 {result['count']} 张模拟表：\n\n"
                for t in result["tables"]:
                    text += f"- {t}\n"
                _resp = {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": text}], "isError": False},
                }

            elif tool_name == "describe_table":
                result = describe_table(tool_args.get("table_name", ""), tool_args.get("database"))
                text = f"## 表结构: {result['table']}\n\n"
                text += "### 字段列表\n"
                text += "| 字段名 | 类型 | 注释 |\n| --- | --- | --- |\n"
                for c in result["columns"]:
                    text += f"| {c['name']} | {c['type']} | {c['comment']} |\n"
                if result["properties"].get("primary.key"):
                    text += f"\n### 主键\n`{result['properties']['primary.key']}`\n"
                fk = {k: v for k, v in result["properties"].items() if k.startswith("foreign.key")}
                if fk:
                    text += "\n### 外键\n"
                    for k, v in fk.items():
                        text += f"- `{k}` = `{v}`\n"
                _resp = {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": text}], "isError": False},
                }

            elif tool_name == "search_columns":
                result = search_columns(tool_args.get("keyword", ""), tool_args.get("database"))
                text = f"🔍 搜索 `{result['keyword']}` 找到 {result['count']} 个匹配：\n\n"
                text += "| 表名 | 字段名 | 类型 | 注释 |\n| --- | --- | --- | --- |\n"
                for m in result["matches"]:
                    text += f"| {m['table']} | {m['column']} | {m['type']} | {m['comment']} |\n"
                _resp = {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": text}], "isError": False},
                }

            else:
                _resp = {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": f"未知工具: {tool_name}"}], "isError": True},
                }

        except ValueError as e:
            logger.warning(f"输入验证错误: {e}")
            _resp = {
                "jsonrpc": "2.0", "id": request_id,
                "result": {"content": [{"type": "text", "text": f"输入错误: {e}"}], "isError": True},
            }
        except Exception as e:
            logger.error(f"执行错误: {e}", exc_info=True)
            _resp = {
                "jsonrpc": "2.0", "id": request_id,
                "result": {"content": [{"type": "text", "text": f"执行错误: {str(e)}"}], "isError": True},
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
            "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}},
        }

    else:
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}


# ============================================================
# 主入口
# ============================================================
def main():
    logger.info("Mock MCP Server 已启动")
    logger.info("模拟数据已就绪: 4 张表, 40+ 条记录")

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