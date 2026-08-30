from __future__ import annotations

import sqlite3

from ghostroot.server import db
from ghostroot.server.models import CompleteRequest
from ghostroot.server.routers.export import _export_yaml
from ghostroot.server.routers.projects import complete_project
from ghostroot.server.services import build_project_metrics


def test_configure_adds_bootstrap_enabled_to_legacy_projects_table(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                reason_worker TEXT,
                reason_trigger TEXT,
                reason_started_at TEXT,
                reason_last_heartbeat_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO projects (id, title, created_at) VALUES ('proj_001', 'legacy', '2026-01-01T00:00:00Z')"
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        row = conn.execute("SELECT bootstrap_enabled FROM projects WHERE id = 'proj_001'").fetchone()
    assert row["bootstrap_enabled"] == 1


def test_configure_maps_disabled_bootstrap_mode_to_false(tmp_path, monkeypatch) -> None:
    path = tmp_path / "intermediate.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                bootstrap_mode TEXT NOT NULL DEFAULT 'auto',
                created_at TEXT NOT NULL,
                reason_worker TEXT,
                reason_trigger TEXT,
                reason_started_at TEXT,
                reason_last_heartbeat_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO projects (id, title, bootstrap_mode, created_at) VALUES ('proj_001', 'disabled', 'disabled', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO projects (id, title, bootstrap_mode, created_at) VALUES ('proj_002', 'enabled', 'enabled', '2026-01-01T00:00:00Z')"
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, bootstrap_enabled FROM projects ORDER BY id").fetchall()
    assert [(row["id"], row["bootstrap_enabled"]) for row in rows] == [
        ("proj_001", 0),
        ("proj_002", 1),
    ]


def test_configure_adds_report_timeout_to_legacy_settings_table(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-settings.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE settings (
                intent_timeout INTEGER NOT NULL DEFAULT 15,
                reason_timeout INTEGER NOT NULL DEFAULT 15
            )
            """
        )
        conn.execute("INSERT INTO settings (rowid, intent_timeout, reason_timeout) VALUES (1, 20, 30)")

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        row = conn.execute("SELECT intent_timeout, reason_timeout, report_timeout FROM settings WHERE rowid = 1").fetchone()

    assert dict(row) == {"intent_timeout": 20, "reason_timeout": 30, "report_timeout": 60}


def test_configure_creates_tool_events_and_supports_idempotent_inserts(tmp_path, monkeypatch) -> None:
    path = tmp_path / "tool-events.db"
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (id, title, status, bootstrap_enabled, created_at) VALUES ('proj_001', 'p', 'active', 1, '2026-01-01T00:00:00Z')"
        )
        for _ in range(2):
            conn.execute(
                """
                INSERT OR IGNORE INTO tool_events (
                    event_id, project_id, task_type, phase, intent_id, worker,
                    task_run_id, tool, command, cwd, source, occurred_at, recorded_at
                )
                VALUES (
                    'evt-001', 'proj_001', 'explore', 'explore_execute', 'i001',
                    'worker-a', 'tr_001', 'curl', 'curl http://target', '/work',
                    'path-wrapper', '2026-01-01T00:00:01Z', '2026-01-01T00:00:02Z'
                )
                """
            )
        rows = conn.execute("SELECT * FROM tool_events WHERE project_id = 'proj_001'").fetchall()

    assert len(rows) == 1
    assert rows[0]["task_run_id"] == "tr_001"
    assert rows[0]["tool"] == "curl"


def test_configure_adds_structured_graph_columns_to_legacy_tables(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-graph.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                bootstrap_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                reason_worker TEXT,
                reason_trigger TEXT,
                reason_started_at TEXT,
                reason_last_heartbeat_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE facts (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                description TEXT NOT NULL,
                PRIMARY KEY (id, project_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE intents (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                to_fact_id TEXT,
                description TEXT NOT NULL,
                creator TEXT NOT NULL,
                worker TEXT,
                last_heartbeat_at TEXT,
                created_at TEXT NOT NULL,
                concluded_at TEXT,
                PRIMARY KEY (id, project_id)
            )
            """
        )
        conn.execute(
            "INSERT INTO projects (id, title, created_at) VALUES ('proj_001', 'legacy', '2026-01-01T00:00:00Z')"
        )
        conn.execute("INSERT INTO facts (id, project_id, description) VALUES ('origin', 'proj_001', 'start')")
        conn.execute(
            "INSERT INTO intents (id, project_id, description, creator, created_at) VALUES ('i001', 'proj_001', 'work', 'reasoner', '2026-01-01T00:00:00Z')"
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        fact_columns = {row["name"] for row in conn.execute("PRAGMA table_info(facts)")}
        intent_columns = {row["name"] for row in conn.execute("PRAGMA table_info(intents)")}
        fact = conn.execute("SELECT * FROM facts WHERE id = 'origin'").fetchone()
        intent = conn.execute("SELECT * FROM intents WHERE id = 'i001'").fetchone()

    assert {"kind", "outcome", "goal_relevance", "next_policy", "tags_json", "atoms_json"} <= fact_columns
    assert {"kind", "stop_condition"} <= intent_columns
    assert fact["kind"] is None
    assert fact["tags_json"] is None
    assert intent["kind"] is None


def test_project_metrics_counts_effective_action_steps(tmp_path, monkeypatch) -> None:
    path = tmp_path / "metrics.db"
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (id, title, status, bootstrap_enabled, created_at) VALUES ('proj_001', 'p', 'active', 1, '2026-01-01T00:00:00Z')"
        )
        conn.execute("INSERT INTO facts (id, project_id, description) VALUES ('origin', 'proj_001', 'start')")
        conn.execute("INSERT INTO facts (id, project_id, description) VALUES ('goal', 'proj_001', 'finish')")
        conn.execute(
            "INSERT INTO intents (id, project_id, description, creator, created_at) VALUES ('i001', 'proj_001', 'work', 'reasoner', '2026-01-01T00:00:00Z')"
        )
        for event_id, task_type, task_run_id, tool, command in (
            ("evt-001", "bootstrap", "tr_bootstrap", "curl", "curl http://target/"),
            ("evt-002", "explore", "tr_explore", "nmap", "nmap -sV target"),
            ("evt-003", "reason", "tr_reason", "curl", "curl http://ignored-in-reason/"),
            ("evt-004", "explore", "tr_explore", "grep", "grep --help"),
            ("evt-005", "explore", "tr_explore", "nmap", "nmap --version"),
            ("evt-006", "explore", "tr_explore", "ruby", "ruby /usr/bin/msfconsole -q -x run"),
            ("evt-007", "explore", "tr_explore", "curl", "curl http://target/"),
        ):
            conn.execute(
                """
                INSERT INTO tool_events (
                    event_id, project_id, task_type, phase, task_run_id, tool, command,
                    source, occurred_at, recorded_at
                )
                VALUES (?, 'proj_001', ?, ?, ?, ?, ?, 'path-wrapper', '2026-01-01T00:00:01Z', '2026-01-01T00:00:02Z')
                """,
                (event_id, task_type, f"{task_type}_execute", task_run_id, tool, command),
            )
        metrics = build_project_metrics(conn, "proj_001")

    assert metrics.fact_count == 2
    assert metrics.intent_count == 1
    assert metrics.tool_event_count == 7
    assert metrics.action_step_count == 2
    assert metrics.execution_episode_count == 2
    assert metrics.tool_event_counts_by_task_type == {"bootstrap": 1, "explore": 5, "reason": 1}
    assert metrics.tool_event_counts_by_tool == {"curl": 3, "nmap": 2, "grep": 1, "ruby": 1}


def test_complete_project_does_not_auto_queue_report(tmp_path, monkeypatch) -> None:
    path = tmp_path / "complete-no-report.db"
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (id, title, status, bootstrap_enabled, created_at) VALUES ('proj_001', 'p', 'active', 1, '2026-01-01T00:00:00Z')"
        )
        conn.execute("INSERT INTO facts (id, project_id, description) VALUES ('origin', 'proj_001', 'start')")
        conn.execute("INSERT INTO facts (id, project_id, description) VALUES ('goal', 'proj_001', 'finish')")
        conn.execute("INSERT INTO facts (id, project_id, description) VALUES ('f001', 'proj_001', 'root proof')")

    intent = complete_project(
        "proj_001",
        CompleteRequest.model_validate({"from": ["f001"], "description": "goal reached", "worker": "reasoner"}),
    )

    with db.get_conn() as conn:
        project = conn.execute("SELECT status FROM projects WHERE id = 'proj_001'").fetchone()
        report_count = conn.execute("SELECT COUNT(*) AS count FROM project_reports WHERE project_id = 'proj_001'").fetchone()[
            "count"
        ]

    assert intent.to == "goal"
    assert project["status"] == "completed"
    assert report_count == 0


def test_agent_yaml_replaces_structured_fact_description_with_atoms(tmp_path, monkeypatch) -> None:
    path = tmp_path / "export.db"
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (id, title, status, bootstrap_enabled, created_at) VALUES ('proj_001', 'p', 'active', 1, '2026-01-01T00:00:00Z')"
        )
        conn.execute("INSERT INTO facts (id, project_id, description) VALUES ('origin', 'proj_001', 'start')")
        conn.execute("INSERT INTO facts (id, project_id, description) VALUES ('goal', 'proj_001', 'finish')")
        conn.execute(
            """
            INSERT INTO facts (id, project_id, description, kind, outcome, goal_relevance, next_policy, tags_json, atoms_json)
            VALUES (
                'f001',
                'proj_001',
                'this is a long natural language paragraph that should not be fed back to the reason agent',
                'exploration_result',
                'positive',
                'advances',
                'branch',
                '["web", "sqli"]',
                '[{"subject":"endpoint.uid","predicate":"vulnerable_to","object":"sqli","polarity":"positive"}]'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO intents (id, project_id, to_fact_id, description, kind, stop_condition, creator, created_at)
            VALUES ('i001', 'proj_001', 'f001', 'validate SQL injection route and avoid already ruled out reverse shell route', 'validate', 'enough', 'reasoner', '2026-01-01T00:00:00Z')
            """
        )
        conn.execute(
            "INSERT INTO intent_sources (intent_id, project_id, fact_id) VALUES ('i001', 'proj_001', 'origin')"
        )
        exported = _export_yaml(conn, "proj_001")

    assert "description: this is a long natural language paragraph" not in exported
    assert "description: validate SQL injection route and avoid already ruled out reverse shell" in exported
    assert "kind: exploration_result" in exported
    assert "kind: validate" in exported
    assert "goal_relevance: advances" in exported
    assert "next_policy: branch" in exported
    assert "status: concluded" in exported
    assert "stop_condition: enough" in exported
    assert "predicate: vulnerable_to" in exported
