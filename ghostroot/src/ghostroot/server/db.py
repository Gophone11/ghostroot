from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

DEFAULT_DB = Path.home() / ".local" / "share" / "ghostroot" / "ghostroot.db"

_db_path: Path | None = None

SCHEMA = """\
CREATE TABLE IF NOT EXISTS settings (
    intent_timeout INTEGER NOT NULL DEFAULT 15,
    reason_timeout INTEGER NOT NULL DEFAULT 15,
    report_timeout INTEGER NOT NULL DEFAULT 60
);

INSERT OR IGNORE INTO settings (rowid, intent_timeout, reason_timeout) VALUES (1, 15, 15);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    bootstrap_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    reason_worker TEXT,
    reason_trigger TEXT,
    reason_started_at TEXT,
    reason_last_heartbeat_at TEXT
);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    kind TEXT,
    outcome TEXT,
    goal_relevance TEXT,
    next_policy TEXT,
    tags_json TEXT,
    atoms_json TEXT,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS intents (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    to_fact_id TEXT,
    description TEXT NOT NULL,
    kind TEXT,
    stop_condition TEXT,
    creator TEXT NOT NULL,
    worker TEXT,
    last_heartbeat_at TEXT,
    created_at TEXT NOT NULL,
    concluded_at TEXT,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS intent_sources (
    intent_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    PRIMARY KEY (intent_id, project_id, fact_id),
    FOREIGN KEY (intent_id, project_id) REFERENCES intents(id, project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hints (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    creator TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO counters (name, value) VALUES ('project', 0);

CREATE TABLE IF NOT EXISTS scoped_counters (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, kind)
);

CREATE TABLE IF NOT EXISTS project_reports (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    markdown TEXT,
    attack_path_summary_json TEXT,
    confidence TEXT,
    gaps_json TEXT,
    error TEXT,
    generator TEXT,
    source_completed_intent_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    last_heartbeat_at TEXT,
    generated_at TEXT,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS tool_events (
    event_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_type TEXT NOT NULL,
    phase TEXT NOT NULL,
    intent_id TEXT,
    worker TEXT,
    task_run_id TEXT,
    tool TEXT NOT NULL,
    command TEXT NOT NULL,
    cwd TEXT,
    source TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (project_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_events_project_time
ON tool_events(project_id, occurred_at);
"""


def configure(path: Path) -> None:
    global _db_path
    if _db_path is not None:
        return
    _db_path = path
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _ensure_settings_columns(conn)
        _ensure_project_columns(conn)
        _ensure_fact_columns(conn)
        _ensure_intent_columns(conn)
        _ensure_tool_event_columns(conn)


def _ensure_settings_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(settings)")}
    if "report_timeout" not in columns:
        conn.execute("ALTER TABLE settings ADD COLUMN report_timeout INTEGER NOT NULL DEFAULT 60")


def _ensure_project_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
    if "bootstrap_enabled" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN bootstrap_enabled INTEGER NOT NULL DEFAULT 1")
        if "bootstrap_mode" in columns:
            conn.execute(
                "UPDATE projects SET bootstrap_enabled = CASE WHEN bootstrap_mode = 'disabled' THEN 0 ELSE 1 END"
            )


def _ensure_fact_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(facts)")}
    if not columns:
        return
    for name, ddl in (
        ("kind", "ALTER TABLE facts ADD COLUMN kind TEXT"),
        ("outcome", "ALTER TABLE facts ADD COLUMN outcome TEXT"),
        ("goal_relevance", "ALTER TABLE facts ADD COLUMN goal_relevance TEXT"),
        ("next_policy", "ALTER TABLE facts ADD COLUMN next_policy TEXT"),
        ("tags_json", "ALTER TABLE facts ADD COLUMN tags_json TEXT"),
        ("atoms_json", "ALTER TABLE facts ADD COLUMN atoms_json TEXT"),
    ):
        if name not in columns:
            conn.execute(ddl)


def _ensure_intent_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(intents)")}
    for name, ddl in (
        ("kind", "ALTER TABLE intents ADD COLUMN kind TEXT"),
        ("stop_condition", "ALTER TABLE intents ADD COLUMN stop_condition TEXT"),
    ):
        if columns and name not in columns:
            conn.execute(ddl)


def _ensure_tool_event_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tool_events)")}
    if columns and "task_run_id" not in columns:
        conn.execute("ALTER TABLE tool_events ADD COLUMN task_run_id TEXT")


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    assert _db_path is not None
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
