from __future__ import annotations

import sqlite3
import json
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from ghostroot.server.models import Fact, Intent, ProjectMeta, ProjectMetrics, ProjectReason, ProjectReport, ReportPathStep, ToolEvent

def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_project_id(conn: sqlite3.Connection) -> str:
    conn.execute("UPDATE counters SET value = value + 1 WHERE name = 'project'")
    row = conn.execute("SELECT value FROM counters WHERE name = 'project'").fetchone()
    return f"proj_{row['value']:03d}"


def _next_scoped_id(
    conn: sqlite3.Connection, kind: str, prefix: str, project_id: str
) -> str:
    conn.execute(
        "INSERT OR IGNORE INTO scoped_counters (project_id, kind, value) VALUES (?, ?, 0)",
        (project_id, kind),
    )
    conn.execute(
        "UPDATE scoped_counters SET value = value + 1 WHERE project_id = ? AND kind = ?",
        (project_id, kind),
    )
    row = conn.execute(
        "SELECT value FROM scoped_counters WHERE project_id = ? AND kind = ?",
        (project_id, kind),
    ).fetchone()
    assert row is not None
    return f"{prefix}{row['value']:03d}"


def next_fact_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "fact", "f", project_id)


def structured_fact_description(
    fallback: str,
    *,
    kind: str | None,
    outcome: str | None,
    tags: list[str],
    atoms: list[dict[str, Any]],
    goal_relevance: str | None = None,
    next_policy: str | None = None,
) -> str:
    if not (kind or outcome or tags or atoms):
        return fallback
    parts: list[str] = []
    if kind:
        parts.append(kind)
    if outcome:
        parts.append(outcome)
    if goal_relevance:
        parts.append(goal_relevance)
    if next_policy:
        parts.append(next_policy)
    if atoms:
        atom = atoms[0]
        subject = str(atom.get("subject") or "?").strip() or "?"
        predicate = str(atom.get("predicate") or "?").strip() or "?"
        object_ = str(atom.get("object") or "?").strip() or "?"
        parts.append(f"{subject} {predicate} {object_}")
    elif tags:
        parts.append(",".join(tags[:3]))
    text = " | ".join(parts).strip() or fallback
    chars = list(text)
    return text if len(chars) <= 120 else "".join(chars[:117]) + "..."


def next_intent_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "intent", "i", project_id)


def next_hint_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "hint", "h", project_id)


def next_report_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "report", "r", project_id)


def get_project_or_404(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Project not found")
    return row


def check_project_active(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = get_project_or_404(conn, project_id)
    if row["status"] != "active":
        raise HTTPException(403, f"Project is {row['status']}")
    return row


def check_project_hint_writable(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = get_project_or_404(conn, project_id)
    if row["status"] not in ("active", "stopped", "completed"):
        raise HTTPException(403, f"Project is {row['status']}")
    return row


def check_project_completed(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = get_project_or_404(conn, project_id)
    if row["status"] != "completed":
        raise HTTPException(403, f"Project is {row['status']}")
    return row


def validate_facts_exist(
    conn: sqlite3.Connection, project_id: str, fact_ids: list[str]
) -> None:
    for fid in fact_ids:
        row = conn.execute(
            "SELECT 1 FROM facts WHERE id = ? AND project_id = ?", (fid, project_id)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"Fact {fid} not found")


def validate_goal_not_in_sources(fact_ids: list[str]) -> None:
    if "goal" in fact_ids:
        raise HTTPException(400, "goal cannot be used in from")


def validate_intent_creator_worker(creator: str, worker: str | None) -> None:
    if worker is not None and worker != creator:
        raise HTTPException(400, "worker must be null or equal to creator")


def get_intent_or_404(
    conn: sqlite3.Connection, project_id: str, intent_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM intents WHERE id = ? AND project_id = ?",
        (intent_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Intent not found")
    return row


def get_claimable_open_intent_or_404(
    conn: sqlite3.Connection, project_id: str, intent_id: str, worker: str
) -> sqlite3.Row:
    expire_workers(conn, project_id)
    row = get_intent_or_404(conn, project_id, intent_id)
    if row["to_fact_id"] is not None:
        raise HTTPException(409, "Intent already concluded")
    if row["worker"] is not None and row["worker"] != worker:
        raise HTTPException(409, f"Intent is currently claimed by {row['worker']}")
    return row


def get_releasable_open_intent_or_404(
    conn: sqlite3.Connection, project_id: str, intent_id: str, worker: str
) -> sqlite3.Row:
    expire_workers(conn, project_id)
    row = get_intent_or_404(conn, project_id, intent_id)
    if row["to_fact_id"] is not None:
        raise HTTPException(409, "Intent already concluded")
    if row["worker"] is None:
        return row
    if row["worker"] != worker:
        raise HTTPException(409, f"Intent is currently claimed by {row['worker']}")
    return row


def get_completion_intent_or_409(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    rows = conn.execute(
        "SELECT * FROM intents WHERE project_id = ? AND to_fact_id = 'goal'",
        (project_id,),
    ).fetchall()
    if not rows:
        raise HTTPException(409, "Completed project is missing its completion intent")
    if len(rows) != 1:
        raise HTTPException(409, "Completed project has multiple completion intents")
    return rows[0]


def intent_to_model(conn: sqlite3.Connection, row: sqlite3.Row, project_id: str) -> Intent:
    sources = conn.execute(
        "SELECT fact_id FROM intent_sources WHERE intent_id = ? AND project_id = ? ORDER BY rowid",
        (row["id"], project_id),
    ).fetchall()
    return Intent(
        id=row["id"],
        **{"from": [s["fact_id"] for s in sources]},
        to=row["to_fact_id"],
        description=row["description"],
        kind=row["kind"],
        stop_condition=row["stop_condition"],
        creator=row["creator"],
        worker=row["worker"],
        last_heartbeat_at=row["last_heartbeat_at"],
        created_at=row["created_at"],
        concluded_at=row["concluded_at"],
    )


def _loads_json_list(value: str | None) -> list:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return data


def fact_to_model(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        description=row["description"],
        kind=row["kind"],
        outcome=row["outcome"],
        goal_relevance=row["goal_relevance"],
        next_policy=row["next_policy"],
        tags=_loads_json_list(row["tags_json"]),
        atoms=_loads_json_list(row["atoms_json"]),
    )


def build_intents(conn: sqlite3.Connection, project_id: str) -> list[Intent]:
    rows = conn.execute(
        "SELECT * FROM intents WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    return [intent_to_model(conn, r, project_id) for r in rows]


def report_to_model(row: sqlite3.Row) -> ProjectReport:
    attack_path_summary = None
    if row["attack_path_summary_json"]:
        attack_path_summary = [
            ReportPathStep.model_validate(item)
            for item in json.loads(row["attack_path_summary_json"])
        ]
    gaps = None
    if row["gaps_json"]:
        gaps = json.loads(row["gaps_json"])
    return ProjectReport(
        id=row["id"],
        project_id=row["project_id"],
        status=row["status"],
        markdown=row["markdown"],
        attack_path_summary=attack_path_summary,
        confidence=row["confidence"],
        gaps=gaps,
        error=row["error"],
        generator=row["generator"],
        source_completed_intent_id=row["source_completed_intent_id"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        last_heartbeat_at=row["last_heartbeat_at"],
        generated_at=row["generated_at"],
    )


def tool_event_to_model(row: sqlite3.Row) -> ToolEvent:
    return ToolEvent(
        event_id=row["event_id"],
        project_id=row["project_id"],
        task_type=row["task_type"],
        phase=row["phase"],
        intent_id=row["intent_id"],
        worker=row["worker"],
        task_run_id=row["task_run_id"],
        tool=row["tool"],
        command=row["command"],
        cwd=row["cwd"],
        source=row["source"],
        occurred_at=row["occurred_at"],
        recorded_at=row["recorded_at"],
    )


_AUXILIARY_ACTION_TOOLS = {"grep", "find", "ls", "cat", "tar", "unzip", "7z"}
_TOOL_INTROSPECTION_RE = re.compile(r"(^|\s)(--help|-h|--version|-V)(\s|$)")


def _is_effective_action_event(row: sqlite3.Row) -> bool:
    if row["task_type"] not in ("bootstrap", "explore"):
        return False
    tool = (row["tool"] or "").strip()
    command = (row["command"] or "").strip()
    if not tool or not command:
        return False
    if tool in _AUXILIARY_ACTION_TOOLS:
        return False
    if _TOOL_INTROSPECTION_RE.search(command):
        return False
    if tool == "ruby" and command.startswith("ruby /usr/bin/msfconsole "):
        return False
    return True


def _effective_action_step_count(rows: list[sqlite3.Row]) -> int:
    commands = {
        " ".join((row["command"] or "").split())
        for row in rows
        if _is_effective_action_event(row)
    }
    return len(commands)


def build_project_metrics(conn: sqlite3.Connection, project_id: str) -> ProjectMetrics:
    get_project_or_404(conn, project_id)
    fact_count = conn.execute("SELECT COUNT(*) AS count FROM facts WHERE project_id = ?", (project_id,)).fetchone()["count"]
    intent_count = conn.execute("SELECT COUNT(*) AS count FROM intents WHERE project_id = ?", (project_id,)).fetchone()["count"]
    tool_event_count = conn.execute(
        "SELECT COUNT(*) AS count FROM tool_events WHERE project_id = ?",
        (project_id,),
    ).fetchone()["count"]
    action_rows = conn.execute(
        """
        SELECT task_type, tool, command
        FROM tool_events
        WHERE project_id = ? AND task_type IN ('bootstrap', 'explore')
        ORDER BY occurred_at, event_id
        """,
        (project_id,),
    ).fetchall()
    action_step_count = _effective_action_step_count(action_rows)
    execution_episode_count = conn.execute(
        """
        SELECT COUNT(DISTINCT task_run_id) AS count
        FROM tool_events
        WHERE project_id = ?
          AND task_type IN ('bootstrap', 'explore')
          AND task_run_id IS NOT NULL
          AND task_run_id != ''
        """,
        (project_id,),
    ).fetchone()["count"]
    by_task_type = {
        row["task_type"]: row["count"]
        for row in conn.execute(
            """
            SELECT task_type, COUNT(*) AS count
            FROM tool_events
            WHERE project_id = ?
            GROUP BY task_type
            ORDER BY task_type
            """,
            (project_id,),
        ).fetchall()
    }
    by_tool = {
        row["tool"]: row["count"]
        for row in conn.execute(
            """
            SELECT tool, COUNT(*) AS count
            FROM tool_events
            WHERE project_id = ?
            GROUP BY tool
            ORDER BY count DESC, tool
            """,
            (project_id,),
        ).fetchall()
    }
    return ProjectMetrics(
        project_id=project_id,
        fact_count=fact_count,
        intent_count=intent_count,
        tool_event_count=tool_event_count,
        action_step_count=action_step_count,
        execution_episode_count=execution_episode_count,
        tool_event_counts_by_task_type=by_task_type,
        tool_event_counts_by_tool=by_tool,
    )


def create_pending_report(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    source_completed_intent_id: str | None,
) -> ProjectReport:
    report_id = next_report_id(conn, project_id)
    now = utcnow()
    conn.execute(
        """
        INSERT INTO project_reports (
            id, project_id, status, markdown, attack_path_summary_json, confidence,
            gaps_json, error, generator, source_completed_intent_id, created_at,
            started_at, last_heartbeat_at, generated_at
        )
        VALUES (?, ?, 'pending', NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, NULL, NULL, NULL)
        """,
        (report_id, project_id, source_completed_intent_id, now),
    )
    row = conn.execute(
        "SELECT * FROM project_reports WHERE id = ? AND project_id = ?",
        (report_id, project_id),
    ).fetchone()
    assert row is not None
    return report_to_model(row)


def get_intent_timeout(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT intent_timeout FROM settings WHERE rowid = 1").fetchone()
    return row["intent_timeout"]


def get_reason_timeout(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT reason_timeout FROM settings WHERE rowid = 1").fetchone()
    return row["reason_timeout"]


def get_report_timeout(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT report_timeout FROM settings WHERE rowid = 1").fetchone()
    return row["report_timeout"]


def project_reason_from_row(row: sqlite3.Row) -> ProjectReason | None:
    if row["reason_worker"] is None:
        return None
    return ProjectReason(
        worker=row["reason_worker"],
        trigger=row["reason_trigger"],
        started_at=row["reason_started_at"],
        last_heartbeat_at=row["reason_last_heartbeat_at"],
    )


def project_meta_from_row(row: sqlite3.Row) -> ProjectMeta:
    return ProjectMeta(
        id=row["id"],
        title=row["title"],
        status=row["status"],
        bootstrap_enabled=bool(row["bootstrap_enabled"]),
        created_at=row["created_at"],
        reason=project_reason_from_row(row),
    )


def clear_project_reason(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute(
        """
        UPDATE projects
        SET reason_worker = NULL,
            reason_trigger = NULL,
            reason_started_at = NULL,
            reason_last_heartbeat_at = NULL
        WHERE id = ?
        """,
        (project_id,),
    )


def expire_workers(conn: sqlite3.Connection, project_id: str | None = None) -> None:
    timeout = get_intent_timeout(conn)
    now = utcnow()
    query = """
        UPDATE intents
        SET worker = NULL
        WHERE to_fact_id IS NULL
          AND worker IS NOT NULL
          AND last_heartbeat_at IS NOT NULL
          AND (julianday(?) - julianday(last_heartbeat_at)) * 86400 > ?
    """
    params: tuple = (now, timeout)
    if project_id is not None:
        query = query.replace("WHERE ", "WHERE project_id = ? AND ", 1)
        params = (project_id, now, timeout)
    conn.execute(query, params)


def expire_reason_leases(conn: sqlite3.Connection, project_id: str | None = None) -> None:
    timeout = get_reason_timeout(conn)
    now = utcnow()
    query = """
        UPDATE projects
        SET reason_worker = NULL,
            reason_trigger = NULL,
            reason_started_at = NULL,
            reason_last_heartbeat_at = NULL
        WHERE reason_worker IS NOT NULL
          AND reason_last_heartbeat_at IS NOT NULL
          AND (julianday(?) - julianday(reason_last_heartbeat_at)) * 86400 > ?
    """
    params: tuple = (now, timeout)
    if project_id is not None:
        query = query.replace("WHERE ", "WHERE id = ? AND ", 1)
        params = (project_id, now, timeout)
    conn.execute(query, params)
