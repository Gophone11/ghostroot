from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import logging
import threading

from pydantic import TypeAdapter
import requests
from requests.adapters import HTTPAdapter

from ghostroot.server.models import Intent, ProjectDetail, ProjectReport, ProjectSummary, ReportContext, Settings

LOG = logging.getLogger(__name__)


class ProtocolError(RuntimeError):
    def __init__(self, message: str, status_code: int, response_text: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


@dataclass(slots=True)
class ApiResult:
    status_code: int
    data: Any | None = None
    text: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class GhostrootClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._summary_adapter = TypeAdapter(list[ProjectSummary])
        self._report_adapter = TypeAdapter(list[ProjectReport])
        self._local = threading.local()
        self._sessions: dict[int, requests.Session] = {}
        self._sessions_lock = threading.Lock()

    def close(self) -> None:
        with self._sessions_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()

    def list_projects(self) -> list[ProjectSummary]:
        response = self._session().get(self._url("/projects"), timeout=self._timeout)
        response.raise_for_status()
        return self._summary_adapter.validate_python(response.json())

    def get_project(self, project_id: str) -> ProjectDetail:
        response = self._session().get(self._url(f"/projects/{project_id}"), timeout=self._timeout)
        response.raise_for_status()
        return ProjectDetail.model_validate(response.json())

    def get_settings(self) -> Settings:
        response = self._session().get(self._url("/settings"), timeout=self._timeout)
        response.raise_for_status()
        return Settings.model_validate(response.json())

    def export_project_for_model_context(self, project_id: str) -> str:
        response = self._session().get(
            self._url(f"/projects/{project_id}/export"),
            params={"format": "yaml", "include_tool_events": "false"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.text

    def export_project(self, project_id: str) -> str:
        return self.export_project_for_model_context(project_id)

    def list_reports(self, project_id: str) -> list[ProjectReport]:
        response = self._session().get(self._url(f"/projects/{project_id}/reports"), timeout=self._timeout)
        response.raise_for_status()
        return self._report_adapter.validate_python(response.json())

    def create_report(self, project_id: str) -> ProjectReport:
        response = self._session().post(self._url(f"/projects/{project_id}/reports"), timeout=self._timeout)
        response.raise_for_status()
        return ProjectReport.model_validate(response.json())

    def get_report_context(self, project_id: str, report_id: str) -> ReportContext:
        response = self._session().get(
            self._url(f"/projects/{project_id}/reports/{report_id}/context"),
            timeout=self._timeout,
        )
        response.raise_for_status()
        return ReportContext.model_validate(response.json())

    def heartbeat(self, project_id: str, intent_id: str, worker: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/intents/{intent_id}/heartbeat",
            json={"worker": worker},
        )

    def claim_reason(self, project_id: str, worker: str, trigger: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/reason/claim",
            json={"worker": worker, "trigger": trigger},
        )

    def reason_heartbeat(self, project_id: str, worker: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/reason/heartbeat",
            json={"worker": worker},
        )

    def release_reason(self, project_id: str, worker: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/reason/release",
            json={"worker": worker},
        )

    def update_project_status(self, project_id: str, status: str) -> ApiResult:
        return self._request_json(
            "PUT",
            f"/projects/{project_id}/status",
            json={"status": status},
        )

    def claim_report(self, project_id: str, report_id: str, worker: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/reports/{report_id}/claim",
            json={"worker": worker},
        )

    def report_heartbeat(self, project_id: str, report_id: str, worker: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/reports/{report_id}/heartbeat",
            json={"worker": worker},
        )

    def complete_report(
        self,
        project_id: str,
        report_id: str,
        worker: str,
        markdown: str,
        attack_path_summary: list[dict[str, Any]],
        confidence: str,
        gaps: list[str],
    ) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/reports/{report_id}/complete",
            json={
                "worker": worker,
                "markdown": markdown,
                "attack_path_summary": attack_path_summary,
                "confidence": confidence,
                "gaps": gaps,
            },
        )

    def fail_report(self, project_id: str, report_id: str, worker: str, error: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/reports/{report_id}/fail",
            json={"worker": worker, "error": error},
        )

    def release(self, project_id: str, intent_id: str, worker: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/intents/{intent_id}/release",
            json={"worker": worker},
        )

    def conclude(
        self,
        project_id: str,
        intent_id: str,
        worker: str,
        description: str,
        *,
        kind: str | None = None,
        outcome: str | None = None,
        goal_relevance: str | None = None,
        next_policy: str | None = None,
        stop_condition: str | None = None,
        tags: list[str] | None = None,
        atoms: list[dict[str, Any]] | None = None,
    ) -> ApiResult:
        payload: dict[str, Any] = {"worker": worker, "description": description}
        if kind is not None:
            payload["kind"] = kind
        if outcome is not None:
            payload["outcome"] = outcome
        if goal_relevance is not None:
            payload["goal_relevance"] = goal_relevance
        if next_policy is not None:
            payload["next_policy"] = next_policy
        if stop_condition is not None:
            payload["stop_condition"] = stop_condition
        if tags is not None:
            payload["tags"] = tags
        if atoms is not None:
            payload["atoms"] = atoms
        return self._request_json(
            "POST",
            f"/projects/{project_id}/intents/{intent_id}/conclude",
            json=payload,
        )

    def complete(self, project_id: str, from_ids: list[str], description: str, worker: str) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/complete",
            json={"from": from_ids, "description": description, "worker": worker},
        )

    def create_intent(
        self,
        project_id: str,
        from_ids: list[str],
        description: str,
        creator: str,
        *,
        kind: str | None = None,
    ) -> ApiResult:
        payload: dict[str, Any] = {
            "from": from_ids,
            "description": description,
            "creator": creator,
            "worker": None,
        }
        if kind is not None:
            payload["kind"] = kind
        return self._request_json(
            "POST",
            f"/projects/{project_id}/intents",
            json=payload,
        )

    def record_tool_events(self, project_id: str, events: list[dict[str, Any]]) -> ApiResult:
        return self._request_json(
            "POST",
            f"/projects/{project_id}/tool-events",
            json={"events": events},
        )

    def _request_json(self, method: str, path: str, json: dict[str, Any]) -> ApiResult:
        try:
            response = self._session().request(
                method,
                self._url(path),
                json=json,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            LOG.warning("request failed method=%s path=%s error=%s", method, path, exc)
            return ApiResult(status_code=0, text=str(exc))
        data: Any | None = None
        if response.headers.get("content-type", "").startswith("application/json"):
            data = response.json()
        return ApiResult(status_code=response.status_code, data=data, text=response.text)

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is not None:
            return session

        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=64, pool_maxsize=64, pool_block=False)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        self._local.session = session
        with self._sessions_lock:
            self._sessions[threading.get_ident()] = session
        return session
