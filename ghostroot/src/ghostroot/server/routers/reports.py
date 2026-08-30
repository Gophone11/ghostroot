from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from ghostroot.server.db import get_conn
from ghostroot.server.models import (
    ReportClaimRequest,
    ReportCompleteRequest,
    ReportContext,
    ReportFailRequest,
    ProjectReport,
)
from ghostroot.server.report import reconstruct_attack_path
from ghostroot.server.routers.export import _export_timeline
from ghostroot.server.services import (
    build_intents,
    check_project_completed,
    create_pending_report,
    get_completion_intent_or_409,
    get_project_or_404,
    get_report_timeout,
    fact_to_model,
    project_meta_from_row,
    report_to_model,
    utcnow,
)
from ghostroot.server.models import Hint

router = APIRouter(tags=["reports"])


def _expire_report_leases(conn, project_id: str | None = None) -> None:
    timeout = get_report_timeout(conn)
    now = utcnow()
    query = """
        UPDATE project_reports
        SET status = 'pending',
            generator = NULL,
            started_at = NULL,
            last_heartbeat_at = NULL,
            error = 'report generation lease expired'
        WHERE status = 'generating'
          AND last_heartbeat_at IS NOT NULL
          AND (julianday(?) - julianday(last_heartbeat_at)) * 86400 > ?
    """
    params: tuple = (now, timeout)
    if project_id is not None:
        query = query.replace("WHERE ", "WHERE project_id = ? AND ", 1)
        params = (project_id, now, timeout)
    conn.execute(query, params)


def _get_report_or_404(conn, project_id: str, report_id: str):
    row = conn.execute(
        "SELECT * FROM project_reports WHERE id = ? AND project_id = ?",
        (report_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Report not found")
    return row


@router.get("/projects/{project_id}/reports", response_model=list[ProjectReport])
def list_project_reports(project_id: str):
    with get_conn() as conn:
        _expire_report_leases(conn, project_id)
        get_project_or_404(conn, project_id)
        rows = conn.execute(
            "SELECT * FROM project_reports WHERE project_id = ? ORDER BY created_at DESC, id DESC",
            (project_id,),
        ).fetchall()
        return [report_to_model(row) for row in rows]


@router.get("/projects/{project_id}/reports/latest", response_model=ProjectReport)
def latest_project_report(project_id: str):
    with get_conn() as conn:
        _expire_report_leases(conn, project_id)
        get_project_or_404(conn, project_id)
        row = conn.execute(
            """
            SELECT * FROM project_reports
            WHERE project_id = ? AND status = 'ready'
            ORDER BY generated_at DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT * FROM project_reports
                WHERE project_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Report not found")
        return report_to_model(row)


@router.post("/projects/{project_id}/reports", response_model=ProjectReport, status_code=201)
def create_project_report(project_id: str):
    with get_conn() as conn:
        check_project_completed(conn, project_id)
        completion = get_completion_intent_or_409(conn, project_id)
        return create_pending_report(conn, project_id, source_completed_intent_id=completion["id"])


@router.get("/projects/{project_id}/reports/{report_id}", response_model=ProjectReport)
def get_project_report(project_id: str, report_id: str):
    with get_conn() as conn:
        _expire_report_leases(conn, project_id)
        row = _get_report_or_404(conn, project_id, report_id)
        return report_to_model(row)


@router.get(
    "/projects/{project_id}/reports/{report_id}/context",
    response_model=ReportContext,
    response_model_exclude_defaults=True,
)
def get_report_context(project_id: str, report_id: str):
    with get_conn() as conn:
        _expire_report_leases(conn, project_id)
        report = _get_report_or_404(conn, project_id, report_id)
        if report["status"] not in ("pending", "generating"):
            raise HTTPException(409, f"Report is {report['status']}")
        project = get_project_or_404(conn, project_id)
        fact_rows = conn.execute(
            """
            SELECT id, description, kind, outcome, goal_relevance, next_policy, tags_json, atoms_json
            FROM facts
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchall()
        hint_rows = conn.execute(
            "SELECT id, content, creator, created_at FROM hints WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
        intent_rows = conn.execute(
            "SELECT * FROM intents WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
        sources_by_intent = {
            intent["id"]: [
                row["fact_id"]
                for row in conn.execute(
                    "SELECT fact_id FROM intent_sources WHERE intent_id = ? AND project_id = ? ORDER BY rowid",
                    (intent["id"], project_id),
                ).fetchall()
            ]
            for intent in intent_rows
        }
        main_path = reconstruct_attack_path(intent_rows, sources_by_intent)
        return ReportContext(
            project=project_meta_from_row(project),
            facts=[fact_to_model(row) for row in fact_rows],
            intents=build_intents(conn, project_id),
            hints=[Hint(**dict(row)) for row in hint_rows],
            main_path_intent_ids=[intent["id"] for intent in main_path],
            timeline=_export_timeline(conn, project_id),
        )


@router.post("/projects/{project_id}/reports/{report_id}/claim", response_model=ProjectReport)
def claim_project_report(project_id: str, report_id: str, body: ReportClaimRequest):
    with get_conn() as conn:
        _expire_report_leases(conn, project_id)
        check_project_completed(conn, project_id)
        row = _get_report_or_404(conn, project_id, report_id)
        if row["status"] != "pending":
            raise HTTPException(409, f"Report is {row['status']}")
        now = utcnow()
        conn.execute(
            """
            UPDATE project_reports
            SET status = 'generating',
                generator = ?,
                started_at = ?,
                last_heartbeat_at = ?,
                error = NULL
            WHERE id = ? AND project_id = ?
            """,
            (body.worker, now, now, report_id, project_id),
        )
        updated = _get_report_or_404(conn, project_id, report_id)
        return report_to_model(updated)


@router.post("/projects/{project_id}/reports/{report_id}/heartbeat", response_model=ProjectReport)
def heartbeat_project_report(project_id: str, report_id: str, body: ReportClaimRequest):
    with get_conn() as conn:
        _expire_report_leases(conn, project_id)
        row = _get_report_or_404(conn, project_id, report_id)
        if row["status"] != "generating":
            raise HTTPException(409, f"Report is {row['status']}")
        if row["generator"] != body.worker:
            raise HTTPException(409, f"Report is currently claimed by {row['generator']}")
        now = utcnow()
        conn.execute(
            "UPDATE project_reports SET last_heartbeat_at = ? WHERE id = ? AND project_id = ?",
            (now, report_id, project_id),
        )
        updated = _get_report_or_404(conn, project_id, report_id)
        return report_to_model(updated)


@router.post("/projects/{project_id}/reports/{report_id}/complete", response_model=ProjectReport)
def complete_project_report(project_id: str, report_id: str, body: ReportCompleteRequest):
    with get_conn() as conn:
        row = _get_report_or_404(conn, project_id, report_id)
        if row["status"] != "generating":
            raise HTTPException(409, f"Report is {row['status']}")
        if row["generator"] != body.worker:
            raise HTTPException(409, f"Report is currently claimed by {row['generator']}")
        now = utcnow()
        conn.execute(
            """
            UPDATE project_reports
            SET status = 'ready',
                markdown = ?,
                attack_path_summary_json = ?,
                confidence = ?,
                gaps_json = ?,
                error = NULL,
                generated_at = ?,
                last_heartbeat_at = ?
            WHERE id = ? AND project_id = ?
            """,
            (
                body.markdown,
                json.dumps([item.model_dump() for item in body.attack_path_summary], ensure_ascii=False),
                body.confidence,
                json.dumps(body.gaps, ensure_ascii=False),
                now,
                now,
                report_id,
                project_id,
            ),
        )
        updated = _get_report_or_404(conn, project_id, report_id)
        return report_to_model(updated)


@router.post("/projects/{project_id}/reports/{report_id}/fail", response_model=ProjectReport)
def fail_project_report(project_id: str, report_id: str, body: ReportFailRequest):
    with get_conn() as conn:
        row = _get_report_or_404(conn, project_id, report_id)
        if row["status"] != "generating":
            raise HTTPException(409, f"Report is {row['status']}")
        if row["generator"] != body.worker:
            raise HTTPException(409, f"Report is currently claimed by {row['generator']}")
        now = utcnow()
        conn.execute(
            """
            UPDATE project_reports
            SET status = 'failed',
                error = ?,
                generated_at = ?,
                last_heartbeat_at = ?
            WHERE id = ? AND project_id = ?
            """,
            (body.error, now, now, report_id, project_id),
        )
        updated = _get_report_or_404(conn, project_id, report_id)
        return report_to_model(updated)
