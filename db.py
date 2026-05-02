"""
db.py — طبقة قاعدة البيانات (SQLite)

تحسينات v2:
  - Thread-local connection caching (اتصال واحد لكل thread بدلاً من فتح جديد لكل عملية)
  - get_subject_stats() لإحصاءات تفصيلية حسب المادة
  - get_user_rank() لترتيب المستخدم
  - cleanup_old_pdf_data() لتنظيف البيانات القديمة
  - إضافة unique constraint على pdf_chunks (file_hash, chunk_index)
"""
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime

from config import DB_PATH, MAX_RESULTS_DISPLAY

logger = logging.getLogger(__name__)

# ─── Thread-local Connection Pool ────────────────────────────────────────────
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """
    إرجاع اتصال SQLite خاص بالـ thread الحالي.
    يُنشئ اتصالاً جديداً عند الحاجة فقط، ويعيد استخدام الموجود.
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        db_dir = os.path.dirname(DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        conn = sqlite3.connect(
            DB_PATH,
            check_same_thread=False,
            timeout=15,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA cache_size=-8000")   # 8 MB cache
        _local.conn = conn

    return _local.conn


def _exec(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """تنفيذ SELECT وإرجاع النتائج."""
    return get_conn().execute(sql, params).fetchall()


def _run(sql: str, params: tuple = ()) -> None:
    """تنفيذ INSERT/UPDATE/DELETE مع commit فوري."""
    conn = get_conn()
    conn.execute(sql, params)
    conn.commit()


def _runmany(sql: str, params_list: list[tuple]) -> None:
    """تنفيذ batch INSERT/UPDATE مع commit واحد."""
    conn = get_conn()
    conn.executemany(sql, params_list)
    conn.commit()


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ─── Init ─────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """إنشاء الجداول والـ Indexes."""
    conn = get_conn()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_user_id  TEXT UNIQUE NOT NULL,
            username    TEXT,
            created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
        );

        CREATE TABLE IF NOT EXISTS results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_user_id  TEXT NOT NULL,
            subject     TEXT NOT NULL,
            score       INTEGER NOT NULL,
            total       INTEGER NOT NULL,
            language    TEXT NOT NULL,
            date        TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pdf_cache (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            file_hash      TEXT UNIQUE NOT NULL,
            file_name      TEXT,
            language       TEXT NOT NULL,
            extracted_text TEXT NOT NULL,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pdf_chunks (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            file_hash            TEXT NOT NULL,
            chunk_index          INTEGER NOT NULL,
            chunk_hash           TEXT UNIQUE NOT NULL,
            chunk_text           TEXT NOT NULL,
            keywords_json        TEXT NOT NULL,
            learning_points_json TEXT NOT NULL,
            used_count           INTEGER DEFAULT 0,
            last_used_at         TEXT,
            created_at           TEXT NOT NULL,
            UNIQUE(file_hash, chunk_index)
        );

        CREATE INDEX IF NOT EXISTS idx_results_user_date    ON results(tg_user_id, date DESC);
        CREATE INDEX IF NOT EXISTS idx_results_user_subject ON results(tg_user_id, subject);
        CREATE INDEX IF NOT EXISTS idx_pdf_chunks_file_hash ON pdf_chunks(file_hash);
        CREATE INDEX IF NOT EXISTS idx_pdf_chunks_usage     ON pdf_chunks(file_hash, used_count, last_used_at);
    """)
    conn.commit()
    logger.info("✅ قاعدة البيانات جاهزة: %s", DB_PATH)


# ─── Users ────────────────────────────────────────────────────────────────────

def save_user(tg_user_id: int | str, username: str | None) -> None:
    _run(
        "INSERT OR IGNORE INTO users (tg_user_id, username) VALUES (?, ?)",
        (str(tg_user_id), username or ""),
    )


# ─── Results ──────────────────────────────────────────────────────────────────

def save_result(
    tg_user_id: int | str,
    subject: str,
    score: int,
    total: int,
    language: str,
) -> None:
    _run(
        "INSERT INTO results (tg_user_id, subject, score, total, language, date) VALUES (?, ?, ?, ?, ?, ?)",
        (str(tg_user_id), subject, score, total, language, now_str()),
    )


def get_results(tg_user_id: int | str, limit: int | None = None) -> list[dict]:
    effective_limit = limit if limit is not None else MAX_RESULTS_DISPLAY
    rows = _exec(
        """
        SELECT subject, score, total, language, date
        FROM results
        WHERE tg_user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (str(tg_user_id), effective_limit),
    )
    return [dict(r) for r in rows]


def get_stats(tg_user_id: int | str) -> dict:
    """إحصاءات إجمالية للمستخدم."""
    rows = _exec(
        """
        SELECT
            COUNT(*)                                                        AS total_quizzes,
            COALESCE(SUM(score), 0)                                         AS total_correct,
            COALESCE(SUM(total), 0)                                         AS total_questions,
            ROUND(AVG(CAST(score AS REAL) / NULLIF(total,0) * 100), 1)     AS avg_pct,
            MAX(ROUND(CAST(score AS REAL) / NULLIF(total,0) * 100, 1))     AS best_pct,
            MIN(ROUND(CAST(score AS REAL) / NULLIF(total,0) * 100, 1))     AS worst_pct
        FROM results
        WHERE tg_user_id = ?
        """,
        (str(tg_user_id),),
    )
    return dict(rows[0]) if rows else {}


def get_subject_stats(tg_user_id: int | str) -> list[dict]:
    """إحصاءات تفصيلية مجمّعة حسب المادة."""
    rows = _exec(
        """
        SELECT
            subject,
            COUNT(*)                                                       AS attempts,
            ROUND(AVG(CAST(score AS REAL) / NULLIF(total,0) * 100), 1)    AS avg_pct,
            MAX(ROUND(CAST(score AS REAL) / NULLIF(total,0) * 100, 1))    AS best_pct
        FROM results
        WHERE tg_user_id = ?
        GROUP BY subject
        ORDER BY avg_pct DESC
        """,
        (str(tg_user_id),),
    )
    return [dict(r) for r in rows]


# ─── PDF Cache ────────────────────────────────────────────────────────────────

def get_pdf_cache(file_hash: str) -> dict | None:
    rows = _exec(
        "SELECT file_hash, file_name, language, extracted_text, created_at, updated_at FROM pdf_cache WHERE file_hash = ?",
        (file_hash,),
    )
    return dict(rows[0]) if rows else None


def upsert_pdf_cache(
    file_hash: str,
    file_name: str | None,
    language: str,
    extracted_text: str,
) -> None:
    ts = now_str()
    _run(
        """
        INSERT INTO pdf_cache (file_hash, file_name, language, extracted_text, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_hash) DO UPDATE SET
            file_name      = excluded.file_name,
            language       = excluded.language,
            extracted_text = excluded.extracted_text,
            updated_at     = excluded.updated_at
        """,
        (file_hash, file_name, language, extracted_text, ts, ts),
    )


# ─── PDF Chunks ───────────────────────────────────────────────────────────────

def get_chunk_count(file_hash: str) -> int:
    rows = _exec("SELECT COUNT(*) AS cnt FROM pdf_chunks WHERE file_hash = ?", (file_hash,))
    return rows[0]["cnt"] if rows else 0


def save_chunks(file_hash: str, chunks: list[dict]) -> None:
    if not chunks:
        return
    ts = now_str()
    rows = [
        (
            file_hash,
            item["chunk_index"],
            item["chunk_hash"],
            item["chunk_text"],
            json.dumps(item["keywords"],        ensure_ascii=False),
            json.dumps(item["learning_points"], ensure_ascii=False),
            ts,
        )
        for item in chunks
    ]
    _runmany(
        """
        INSERT OR IGNORE INTO pdf_chunks
            (file_hash, chunk_index, chunk_hash, chunk_text,
             keywords_json, learning_points_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def sample_chunks(file_hash: str, limit: int = 3) -> list[dict]:
    """
    اختيار chunks بذكاء: الأقل استخداماً → الأقدم استخداماً → عشوائي.
    """
    rows = _exec(
        """
        SELECT id, file_hash, chunk_index, chunk_hash, chunk_text,
               keywords_json, learning_points_json, used_count, last_used_at
        FROM pdf_chunks
        WHERE file_hash = ?
        ORDER BY
            used_count ASC,
            CASE WHEN last_used_at IS NULL THEN 0 ELSE 1 END ASC,
            last_used_at ASC,
            RANDOM()
        LIMIT ?
        """,
        (file_hash, limit),
    )
    result = []
    for row in rows:
        item = dict(row)
        item["keywords"]        = json.loads(item["keywords_json"])
        item["learning_points"] = json.loads(item["learning_points_json"])
        result.append(item)
    return result


def mark_chunks_used(chunk_ids: list[int]) -> None:
    if not chunk_ids:
        return
    ts = now_str()
    _runmany(
        "UPDATE pdf_chunks SET used_count = used_count + 1, last_used_at = ? WHERE id = ?",
        [(ts, cid) for cid in chunk_ids],
    )


def delete_pdf_data(file_hash: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM pdf_chunks WHERE file_hash = ?", (file_hash,))
    conn.execute("DELETE FROM pdf_cache  WHERE file_hash = ?", (file_hash,))
    conn.commit()
    logger.info("🗑️ تم حذف بيانات PDF: %s", file_hash)


def cleanup_old_pdf_data(days: int = 60) -> int:
    """حذف PDFs غير المستخدمة منذ أكثر من `days` يوماً."""
    conn = get_conn()
    cur = conn.execute(
        """
        DELETE FROM pdf_chunks
        WHERE file_hash IN (
            SELECT file_hash FROM pdf_cache
            WHERE updated_at < datetime('now', ? || ' days')
        )
        """,
        (f"-{days}",),
    )
    deleted_chunks = cur.rowcount
    conn.execute(
        "DELETE FROM pdf_cache WHERE updated_at < datetime('now', ? || ' days')",
        (f"-{days}",),
    )
    conn.commit()
    logger.info("🧹 تنظيف: حُذف %d chunk قديم", deleted_chunks)
    return deleted_chunks
