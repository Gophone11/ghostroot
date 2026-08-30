from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from datetime import datetime
import json
import yaml

from ghostroot.server.db import get_conn
from ghostroot.server.services import build_project_metrics, check_project_completed, expire_reason_leases, expire_workers, get_project_or_404

router = APIRouter(tags=["export"])


def format_export_timestamp(value: str | None) -> str | None:
    if not value:
        return value
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _load_project_data(conn, project_id: str):
    expire_workers(conn, project_id)
    expire_reason_leases(conn, project_id)
    proj = get_project_or_404(conn, project_id)

    facts = conn.execute(
        "SELECT id, description, kind, outcome, goal_relevance, next_policy, tags_json, atoms_json FROM facts WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    hints = conn.execute(
        "SELECT content, creator, created_at FROM hints WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    intents = conn.execute(
        "SELECT * FROM intents WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()

    sources_by_intent = {}
    for i in intents:
        rows = conn.execute(
            "SELECT fact_id FROM intent_sources WHERE intent_id = ? AND project_id = ? ORDER BY rowid",
            (i["id"], project_id),
        ).fetchall()
        sources_by_intent[i["id"]] = [r["fact_id"] for r in rows]

    return proj, facts, hints, intents, sources_by_intent


def _loads_json_list(value: str | None) -> list:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _fact_export_entry(row) -> dict:
    tags = _loads_json_list(row["tags_json"])
    atoms = _loads_json_list(row["atoms_json"])
    has_structured_data = bool(row["kind"] or row["outcome"] or row["goal_relevance"] or row["next_policy"] or tags or atoms)
    entry = {"id": row["id"]}
    if not has_structured_data:
        entry["description"] = row["description"]
    if row["kind"]:
        entry["kind"] = row["kind"]
    if row["outcome"]:
        entry["outcome"] = row["outcome"]
    if row["goal_relevance"]:
        entry["goal_relevance"] = row["goal_relevance"]
    if row["next_policy"]:
        entry["next_policy"] = row["next_policy"]
    if tags:
        entry["tags"] = tags
    if atoms:
        entry["atoms"] = atoms
    return entry


def _load_tool_events(conn, project_id: str):
    return conn.execute(
        """
        SELECT event_id, project_id, task_type, phase, intent_id, worker, task_run_id, tool,
               command, cwd, source, occurred_at, recorded_at
        FROM tool_events
        WHERE project_id = ?
        ORDER BY occurred_at, rowid
        """,
        (project_id,),
    ).fetchall()


def _export_yaml(conn, project_id: str, *, include_tool_events: bool = False) -> str:
    proj, facts, hints, intents, sources_by_intent = _load_project_data(conn, project_id)

    origin_desc = ""
    goal_desc = ""
    for f in facts:
        if f["id"] == "origin":
            origin_desc = f["description"]
        elif f["id"] == "goal":
            goal_desc = f["description"]

    data: dict = {
        "project": {
            "title": proj["title"],
            "origin": origin_desc,
            "goal": goal_desc,
            "bootstrap_enabled": bool(proj["bootstrap_enabled"]),
        }
    }

    if hints:
        data["hints"] = [
            {
                "content": h["content"],
                "creator": h["creator"],
                "created_at": format_export_timestamp(h["created_at"]),
            }
            for h in hints
        ]

    data["facts"] = [_fact_export_entry(f) for f in facts]

    intent_list = []
    for i in intents:
        status = "concluded" if i["to_fact_id"] else "running" if i["worker"] else "open"
        entry: dict = {
            "from": sources_by_intent.get(i["id"], []),
            "to": i["to_fact_id"],
            "status": status,
            "creator": i["creator"],
            "worker": i["worker"],
            "created_at": format_export_timestamp(i["created_at"]),
            "concluded_at": format_export_timestamp(i["concluded_at"]),
        }
        if i["kind"]:
            entry["kind"] = i["kind"]
        entry["description"] = i["description"]
        if i["stop_condition"]:
            entry["stop_condition"] = i["stop_condition"]
        intent_list.append(entry)

    if intent_list:
        data["intents"] = intent_list

    tool_events = _load_tool_events(conn, project_id) if include_tool_events else []
    if tool_events:
        data["tool_events"] = [
            {
                "event_id": event["event_id"],
                "task_type": event["task_type"],
                "phase": event["phase"],
                "intent_id": event["intent_id"],
                "worker": event["worker"],
                "task_run_id": event["task_run_id"],
                "tool": event["tool"],
                "command": event["command"],
                "cwd": event["cwd"],
                "source": event["source"],
                "occurred_at": format_export_timestamp(event["occurred_at"]),
            }
            for event in tool_events
        ]

    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _export_timeline(conn, project_id: str) -> str:
    proj, facts, hints, intents, sources_by_intent = _load_project_data(conn, project_id)

    facts_by_id = {f["id"]: f["description"] for f in facts}

    events: list[tuple[str, int, str]] = []  # (timestamp, order, text)
    order = 0

    origin_desc = facts_by_id.get("origin", "")
    goal_desc = facts_by_id.get("goal", "")
    ts = format_export_timestamp(proj["created_at"]) or ""
    block = f"[{ts}] PROJECT CREATED\n  origin: {origin_desc}\n  goal: {goal_desc}"
    events.append((proj["created_at"] or "", order, block))
    order += 1

    for h in hints:
        ts = format_export_timestamp(h["created_at"]) or ""
        block = f"[{ts}] HINT by {h['creator']}\n  {h['content']}"
        events.append((h["created_at"] or "", order, block))
        order += 1

    for i in intents:
        src = sources_by_intent.get(i["id"], [])
        from_str = ", ".join(src)

        ts = format_export_timestamp(i["created_at"]) or ""
        meta = f"  from: {from_str}"
        if i["worker"] and not i["concluded_at"]:
            meta += f"\n  worker: {i['worker']} (in progress)"
        block = f"[{ts}] INTENT DECLARED {i['id']} by {i['creator']}\n{meta}\n  {i['description']}"
        events.append((i["created_at"] or "", order, block))
        order += 1

        if not i["concluded_at"] or not i["to_fact_id"]:
            continue

        ts = format_export_timestamp(i["concluded_at"]) or ""
        actor = i["worker"] or i["creator"]

        if i["to_fact_id"] == "goal":
            block = f"[{ts}] PROJECT COMPLETED by {actor}\n  via: {i['id']} from {from_str}"
        else:
            fact_desc = facts_by_id.get(i["to_fact_id"], "")
            block = f"[{ts}] INTENT CONCLUDED {i['id']} by {actor}\n  from: {from_str}\n  produced: {i['to_fact_id']}\n  {fact_desc}"

        events.append((i["concluded_at"] or "", order, block))
        order += 1

    for event in _load_tool_events(conn, project_id):
        ts = format_export_timestamp(event["occurred_at"]) or ""
        intent_part = f" intent={event['intent_id']}" if event["intent_id"] else ""
        worker_part = f" worker={event['worker']}" if event["worker"] else ""
        cwd_part = f"\n  cwd: {event['cwd']}" if event["cwd"] else ""
        block = (
            f"[{ts}] TOOL {event['tool']} phase={event['phase']} task={event['task_type']}"
            f"{intent_part}{worker_part}\n  {event['command']}{cwd_part}"
        )
        events.append((event["occurred_at"] or "", order, block))
        order += 1

    events.sort(key=lambda e: (e[0], e[1]))

    return "\n\n".join(e[2] for e in events) + "\n"


def _export_report(conn, project_id: str) -> str:
    check_project_completed(conn, project_id)
    row = conn.execute(
        """
        SELECT markdown FROM project_reports
        WHERE project_id = ? AND status = 'ready'
        ORDER BY generated_at DESC, created_at DESC, id DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row is None or not row["markdown"]:
        latest = conn.execute(
            """
            SELECT status, error FROM project_reports
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        if latest is None:
            raise HTTPException(404, "Report has not been queued")
        if latest["status"] == "failed":
            raise HTTPException(409, f"Report generation failed: {latest['error'] or 'unknown error'}")
        raise HTTPException(409, f"Report is {latest['status']}")
    return row["markdown"]


def _export_tool_events(conn, project_id: str) -> list[dict]:
    get_project_or_404(conn, project_id)
    return [dict(row) for row in _load_tool_events(conn, project_id)]


@router.get("/projects/{project_id}/export")
def export_project(project_id: str, format: str = "yaml", include_tool_events: bool = False):
    if format not in ("yaml", "timeline", "report", "tool-events", "metrics"):
        raise HTTPException(400, "Supported formats: yaml, timeline, report, tool-events, metrics")

    with get_conn() as conn:
        if format == "tool-events":
            return JSONResponse(content=_export_tool_events(conn, project_id))
        if format == "metrics":
            return JSONResponse(content=build_project_metrics(conn, project_id).model_dump())
        if format == "report":
            text = _export_report(conn, project_id)
            media_type = "text/markdown"
        elif format == "timeline":
            text = _export_timeline(conn, project_id)
            media_type = "text/plain"
        else:
            text = _export_yaml(conn, project_id, include_tool_events=include_tool_events)
            media_type = "text/plain"

        return Response(content=text, media_type=media_type)
