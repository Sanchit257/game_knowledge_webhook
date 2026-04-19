"""SQLite persistence for audits, knowledge nodes, and scrape history."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Matches ``config.BASE_DIR / "data"`` when this package lives under the project root.
_DATA_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = _DATA_ROOT / "data"
_DB_PATH: Path = DATA_DIR / "marvellore.db"


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""

    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> Path:
    """Return the filesystem path to the SQLite database file."""

    return _DB_PATH


def get_connection() -> sqlite3.Connection:
    """Open a SQLite connection with row factory set to sqlite3.Row."""

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create database tables if they do not already exist."""

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL,
                pr_number INTEGER NOT NULL,
                commit_sha TEXT NOT NULL,
                triggered_at TEXT NOT NULL,
                status TEXT NOT NULL,
                issues_found INTEGER NOT NULL DEFAULT 0,
                report_json TEXT,
                duration_seconds REAL
            );

            CREATE TABLE IF NOT EXISTS knowledge_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_name TEXT NOT NULL,
                node_type TEXT NOT NULL,
                content TEXT NOT NULL,
                source_pdf TEXT NOT NULL,
                version_tag TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scrape_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scraped_at TEXT NOT NULL,
                pdfs_processed INTEGER NOT NULL DEFAULT 0,
                nodes_created INTEGER NOT NULL DEFAULT 0,
                success INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()


def set_system_state(key: str, value: str) -> None:
    """Set a system state value (string) by key."""

    init_db()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO system_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, _utc_now_iso()),
        )
        conn.commit()


def get_system_state(key: str) -> str | None:
    """Get a system state value by key, or None if not present."""

    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM system_state WHERE key = ?",
            (key,),
        ).fetchone()
        return None if row is None else str(row["value"])


def insert_audit(
    repo: str,
    pr_number: int,
    commit_sha: str,
    triggered_at: str,
    status: str,
    issues_found: int,
    report_json: str | dict[str, Any] | None,
    duration_seconds: float | None,
) -> int:
    """Insert one audit row and return its primary key."""

    payload = (
        report_json
        if isinstance(report_json, str) or report_json is None
        else json.dumps(report_json)
    )
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO audits (
                repo, pr_number, commit_sha, triggered_at, status,
                issues_found, report_json, duration_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repo,
                pr_number,
                commit_sha,
                triggered_at,
                status,
                issues_found,
                payload,
                duration_seconds,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_all_audits() -> list[dict[str, Any]]:
    """Return all audit rows as dictionaries, newest first by id."""

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audits ORDER BY id DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_audit_by_id(audit_id: int) -> dict[str, Any] | None:
    """Return a single audit as a dictionary, or None if not found."""

    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM audits WHERE id = ?",
            (audit_id,),
        ).fetchone()
        return None if row is None else _row_to_dict(row)


def insert_knowledge_node(
    character_name: str,
    node_type: str,
    content: str,
    source_pdf: str,
    version_tag: str | None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> int:
    """Insert one knowledge node and return its primary key."""

    now = _utc_now_iso()
    ca = created_at if created_at is not None else now
    ua = updated_at if updated_at is not None else now
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO knowledge_nodes (
                character_name, node_type, content, source_pdf,
                version_tag, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                character_name,
                node_type,
                content,
                source_pdf,
                version_tag,
                ca,
                ua,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def log_scrape(
    scraped_at: str,
    pdfs_processed: int,
    nodes_created: int,
    success: bool,
) -> int:
    """Append one scrape log row and return its primary key."""

    flag = 1 if success else 0
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO scrape_log (
                scraped_at, pdfs_processed, nodes_created, success
            ) VALUES (?, ?, ?, ?)
            """,
            (scraped_at, pdfs_processed, nodes_created, flag),
        )
        conn.commit()
        return int(cur.lastrowid)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row into a plain dict."""

    return {k: row[k] for k in row.keys()}
