"""
知识库模块 - 运维经验沉淀与检索
支持向量检索 + 关键词检索，用于相似故障匹配
企业级特性: 线程安全 SQLite 连接、WAL 模式、索引优化
"""
from __future__ import annotations
import json
import time
import hashlib
import logging
import os
import sqlite3
from typing import Optional

from .models import KnowledgeEntry, AnomalyType, AnomalyContext, ActionPlan, ExecutionResult
from .utils import ThreadSafeDb

logger = logging.getLogger("ops-agent.knowledge")


class KnowledgeBase:
    """
    运维知识库

    使用 SQLite 存储，支持向量检索(通过 embedding 函数)
    线程安全: 使用 ThreadSafeDb 包装，写操作加锁

    用法:
        kb = KnowledgeBase()
        kb.add_entry(entry)
        results = kb.search_similar(anomaly_type=TIMEOUT, keyword="超时")
        stats = kb.get_statistics()
    """

    # SQL 语句常量
    CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            entry_id TEXT PRIMARY KEY,
            anomaly_type TEXT NOT NULL,
            root_cause TEXT NOT NULL,
            action_taken TEXT NOT NULL,
            success INTEGER NOT NULL,
            context_summary TEXT,
            tags TEXT,
            created_at REAL NOT NULL,
            embedding BLOB
        )
    """
    CREATE_INDEX_TYPE_SQL = """
        CREATE INDEX IF NOT EXISTS idx_knowledge_type
        ON knowledge_entries(anomaly_type)
    """
    CREATE_INDEX_SUCCESS_SQL = """
        CREATE INDEX IF NOT EXISTS idx_knowledge_success
        ON knowledge_entries(success)
    """
    INSERT_SQL = """
        INSERT OR REPLACE INTO knowledge_entries
        (entry_id, anomaly_type, root_cause, action_taken, success,
         context_summary, tags, created_at, embedding)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    SELECT_BY_TYPE_SQL = """
        SELECT * FROM knowledge_entries
        WHERE anomaly_type = ?
        ORDER BY success DESC, created_at DESC
        LIMIT ?
    """
    SELECT_BY_KEYWORD_SQL = """
        SELECT * FROM knowledge_entries
        WHERE (root_cause LIKE ? OR context_summary LIKE ? OR action_taken LIKE ?)
        ORDER BY success DESC, created_at DESC
        LIMIT ?
    """
    SELECT_ALL_SQL = """
        SELECT * FROM knowledge_entries
        WHERE 1=1
        ORDER BY success DESC, created_at DESC
        LIMIT ?
    """
    SELECT_COUNT_SQL = "SELECT COUNT(*) FROM knowledge_entries"
    SELECT_BY_TYPE_STATS_SQL = """
        SELECT anomaly_type, COUNT(*) as cnt
        FROM knowledge_entries
        GROUP BY anomaly_type
    """
    SELECT_SUCCESS_RATE_SQL = """
        SELECT
            SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as rate
        FROM knowledge_entries
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(__file__), "..", "data", "knowledge.db"
            )
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db = ThreadSafeDb(db_path)
        self._init_db()
        logger.info(f"知识库初始化完成: {db_path}")

    def _init_db(self):
        """初始化数据库表结构"""
        self._db.execute(self.CREATE_TABLE_SQL)
        self._db.execute(self.CREATE_INDEX_TYPE_SQL)
        self._db.execute(self.CREATE_INDEX_SUCCESS_SQL)

    # ============================================================
    # 核心操作
    # ============================================================

    def add_entry(self, entry: KnowledgeEntry) -> str:
        """添加知识条目"""
        self._db.execute(self.INSERT_SQL, (
            entry.entry_id,
            entry.anomaly_type.value,
            entry.root_cause,
            entry.action_taken,
            1 if entry.success else 0,
            entry.context_summary,
            json.dumps(entry.tags, ensure_ascii=False),
            entry.created_at,
            json.dumps(entry.embedding) if entry.embedding else None,
        ))
        logger.info(f"知识条目已添加: {entry.entry_id}")
        return entry.entry_id

    def record_action_result(
        self,
        context: AnomalyContext,
        plan: ActionPlan,
        result: ExecutionResult,
        success: bool,
    ) -> str:
        """
        记录处理结果到知识库
        这是主要的写入入口
        """
        raw = f"{context.anomaly_id}_{plan.plan_id}"
        entry_id = f"kb_{hashlib.md5(raw.encode()).hexdigest()[:12]}"

        entry = KnowledgeEntry(
            entry_id=entry_id,
            anomaly_type=context.anomaly_type,
            root_cause=plan.root_cause,
            action_taken=json.dumps({
                "actions": [{"type": a.action_type.value, "target": a.target}
                            for a in plan.actions],
                "reasoning": plan.reasoning,
            }, ensure_ascii=False),
            success=success,
            context_summary=(
                f"工作流: {context.event.workflow_name}, "
                f"任务: {context.event.task_name}, "
                f"类型: {context.anomaly_type.value}"
            ),
            tags=[
                context.anomaly_type.value,
                context.severity.value,
                "success" if success else "failed",
                context.event.tenant,
            ],
            created_at=time.time(),
        )
        return self.add_entry(entry)

    def search_similar(
        self,
        anomaly_type: Optional[AnomalyType] = None,
        keyword: Optional[str] = None,
        limit: int = 5,
    ) -> list[KnowledgeEntry]:
        """
        检索相似故障案例
        支持按类型过滤 + 关键词搜索
        """
        if anomaly_type and keyword:
            conditions = ["anomaly_type = ?", "(root_cause LIKE ? OR context_summary LIKE ? OR action_taken LIKE ?)"]
            params = [anomaly_type.value, f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit]
            sql = f"""
                SELECT * FROM knowledge_entries
                WHERE {' AND '.join(conditions)}
                ORDER BY success DESC, created_at DESC
                LIMIT ?
            """
        elif anomaly_type:
            rows = self._db.query(self.SELECT_BY_TYPE_SQL, (anomaly_type.value, limit))
            return [self._row_to_entry(row) for row in rows]
        elif keyword:
            kw = f"%{keyword}%"
            rows = self._db.query(self.SELECT_BY_KEYWORD_SQL, (kw, kw, kw, limit))
            return [self._row_to_entry(row) for row in rows]
        else:
            rows = self._db.query(self.SELECT_ALL_SQL, (limit,))
            return [self._row_to_entry(row) for row in rows]

        rows = self._db.query(sql, params)
        return [self._row_to_entry(row) for row in rows]

    def get_statistics(self) -> dict:
        """获取知识库统计"""
        total = self._db.query_one(self.SELECT_COUNT_SQL)[0]
        by_type_rows = self._db.query(self.SELECT_BY_TYPE_STATS_SQL)
        rate_row = self._db.query_one(self.SELECT_SUCCESS_RATE_SQL)
        success_rate = rate_row[0] if rate_row else 0

        return {
            "total_entries": total,
            "by_type": {row["anomaly_type"]: row["cnt"] for row in by_type_rows},
            "success_rate": round(success_rate * 100, 1) if success_rate else 0,
        }

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> KnowledgeEntry:
        return KnowledgeEntry(
            entry_id=row["entry_id"],
            anomaly_type=AnomalyType(row["anomaly_type"]),
            root_cause=row["root_cause"],
            action_taken=row["action_taken"],
            success=bool(row["success"]),
            context_summary=row["context_summary"] or "",
            tags=json.loads(row["tags"]) if row["tags"] else [],
            created_at=row["created_at"],
        )

    def close(self):
        """关闭所有数据库连接"""
        self._db.close_all()
        logger.info("知识库连接已关闭")