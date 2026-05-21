"""SQLite persistence for session records."""

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".cctracker" / "sessions.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                session_uuid      TEXT    UNIQUE NOT NULL,
                date              TEXT,
                start_time        TEXT,
                end_time          TEXT,
                duration_minutes  INTEGER,
                input_tokens      INTEGER DEFAULT 0,
                output_tokens     INTEGER DEFAULT 0,
                total_tokens      INTEGER DEFAULT 0,
                project           TEXT,
                achievement       TEXT,
                model             TEXT,
                account           TEXT,
                cwd               TEXT,
                transcript_path   TEXT,
                created_at        TEXT    DEFAULT (datetime('now')),
                updated_at        TEXT    DEFAULT (datetime('now'))
            )
        """)


def upsert_session(data: dict) -> None:
    """Insert a new session or update token counts / times on subsequent hook calls."""
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions (
                session_uuid, date, start_time, end_time, duration_minutes,
                input_tokens, output_tokens, total_tokens,
                project, model, account, cwd, transcript_path
            ) VALUES (
                :session_uuid, :date, :start_time, :end_time, :duration_minutes,
                :input_tokens, :output_tokens, :total_tokens,
                :project, :model, :account, :cwd, :transcript_path
            )
            ON CONFLICT(session_uuid) DO UPDATE SET
                end_time          = excluded.end_time,
                duration_minutes  = excluded.duration_minutes,
                input_tokens      = excluded.input_tokens,
                output_tokens     = excluded.output_tokens,
                total_tokens      = excluded.total_tokens,
                project           = excluded.project,
                model             = excluded.model,
                account           = excluded.account,
                cwd               = excluded.cwd,
                transcript_path   = excluded.transcript_path,
                updated_at        = datetime('now')
            """,
            data,
        )


def update_achievement(session_id: int, text: str) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE sessions SET achievement = ?, updated_at = datetime('now') WHERE id = ?",
            (text, session_id),
        )
        return cur.rowcount > 0


def get_sessions(
    limit: int | None = 20,
    days: int | None = None,
    account: str | None = None,
) -> list[sqlite3.Row]:
    init_db()
    conditions: list[str] = []
    params: list = []

    if days:
        conditions.append("date >= date('now', ?)")
        params.append(f"-{days} days")
    if account:
        conditions.append("account = ?")
        params.append(account)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    limit_clause = f"LIMIT {int(limit)}" if limit else ""

    sql = f"""
        SELECT * FROM sessions
        {where}
        ORDER BY date DESC, start_time DESC
        {limit_clause}
    """
    with _connect() as conn:
        return conn.execute(sql, params).fetchall()


def get_session_by_id(session_id: int) -> sqlite3.Row | None:
    init_db()
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()


def get_last_session() -> sqlite3.Row | None:
    init_db()
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM sessions ORDER BY date DESC, start_time DESC LIMIT 1"
        ).fetchone()
