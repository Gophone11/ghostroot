from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ghostroot.dispatcher.config import DispatchConfig, WorkerConfig
from ghostroot.dispatcher.contracts import FactPayload
from ghostroot.dispatcher.protocol.client import GhostrootClient
from ghostroot.dispatcher.runtime.cancellation import TaskCancellation
from ghostroot.dispatcher.runtime.containers import TELEMETRY_BIN, ContainerManager
from ghostroot.dispatcher.runtime.heartbeat import HeartbeatLease
from ghostroot.dispatcher.runtime.process import ProcessResult

HEALTHCHECK_COMMUNICATE_GRACE_SECONDS = 10
PROCESS_COMMUNICATE_GRACE_SECONDS = 15
LOG_PREVIEW_LIMIT = 1200
GRAPH_SNAPSHOT_ROOT = "/tmp/ghostroot-prompts"
LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class HealthcheckRun:
    result: ProcessResult
    duration_ms: int


@dataclass(slots=True)
class ConcludeWriteResult:
    status: str
    fact_id: str | None = None


def preview(text: str, limit: int = LOG_PREVIEW_LIMIT) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def did_timeout(result: ProcessResult) -> bool:
    return not result.cancelled and (result.timed_out or result.returncode in (124, 137))


def cancel_reason(result: ProcessResult, cancellation: TaskCancellation | None = None) -> str | None:
    if result.cancelled:
        return result.cancel_reason or "cancelled"
    if cancellation is not None:
        return cancellation.reason
    return None


def communicate_timeout(timeout_seconds: int, grace_seconds: int = PROCESS_COMMUNICATE_GRACE_SECONDS) -> int:
    return timeout_seconds + grace_seconds


def task_healthcheck_enabled(config: DispatchConfig) -> bool:
    return config.runtime.worker_healthcheck == "startup_and_task"


def write_graph_snapshot_reference(
    container_manager: ContainerManager,
    container_name: str,
    graph_yaml: str,
    *,
    phase: str,
) -> str:
    path = f"{GRAPH_SNAPSHOT_ROOT}/{phase}-{uuid.uuid4().hex[:12]}/graph.yaml"
    container_manager.write_text_file(container_name, path, graph_yaml)
    return (
        "The graph YAML snapshot is stored in this file inside the current container:\n\n"
        f"{path}\n\n"
        "Before using the graph, read the entire file and treat its contents as the YAML snapshot "
        "for this Graph section."
    )


def run_healthcheck(
    container_manager: ContainerManager,
    container_name: str,
    worker: WorkerConfig,
    command: list[str],
    *,
    timeout_seconds: int,
    lease: HeartbeatLease | None = None,
    cancellation: TaskCancellation | None = None,
) -> HealthcheckRun:
    process = container_manager.build_exec_process(
        container_name,
        dict(worker.env),
        command,
        timeout_seconds=timeout_seconds,
    )
    process.start()
    if lease is not None:
        lease.attach_process(process)
    if cancellation is not None:
        cancellation.attach_process(process)
    started = time.perf_counter()
    try:
        result = process.communicate(timeout=communicate_timeout(timeout_seconds, HEALTHCHECK_COMMUNICATE_GRACE_SECONDS))
    finally:
        if lease is not None:
            lease.attach_process(None)
        if cancellation is not None:
            cancellation.attach_process(None)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return HealthcheckRun(result=result, duration_ms=duration_ms)


def run_worker_process(
    container_manager: ContainerManager,
    container_name: str,
    worker: WorkerConfig,
    argv: list[str],
    *,
    phase: str,
    timeout_seconds: int,
    lease: HeartbeatLease | None = None,
    cancellation: TaskCancellation | None = None,
    telemetry_project_id: str | None = None,
    telemetry_task_type: str | None = None,
    telemetry_intent_id: str | None = None,
) -> ProcessResult:
    LOG.info(
        "starting container exec container=%s worker=%s phase=%s timeout=%ss",
        container_name,
        worker.name,
        phase,
        timeout_seconds,
    )
    env = dict(worker.env)
    telemetry_run_id: str | None = None
    if telemetry_project_id and telemetry_task_type:
        telemetry_run_id = f"tr_{uuid.uuid4().hex[:16]}"
        try:
            container_manager.ensure_tool_telemetry(container_name)
            env.update(
                {
                    "GHOSTROOT_PROJECT_ID": telemetry_project_id,
                    "GHOSTROOT_TASK_TYPE": telemetry_task_type,
                    "GHOSTROOT_PHASE": phase,
                    "GHOSTROOT_INTENT_ID": telemetry_intent_id or "",
                    "GHOSTROOT_WORKER": worker.name,
                    "GHOSTROOT_TASK_RUN_ID": telemetry_run_id,
                    "PATH": _telemetry_path(env.get("PATH")),
                }
            )
        except Exception as exc:
            LOG.warning(
                "tool telemetry unavailable container=%s worker=%s phase=%s error=%s",
                container_name,
                worker.name,
                phase,
                exc,
            )
            telemetry_run_id = None

    process = container_manager.build_exec_process(
        container_name,
        env,
        argv,
        timeout_seconds=timeout_seconds,
    )
    process.start()
    if lease is not None:
        lease.attach_process(process)
    if cancellation is not None:
        cancellation.attach_process(process)
    try:
        result = process.communicate(timeout=communicate_timeout(timeout_seconds))
    finally:
        if lease is not None:
            lease.attach_process(None)
        if cancellation is not None:
            cancellation.attach_process(None)
    if telemetry_run_id is not None:
        result.tool_events = _collect_current_tool_events(container_manager, container_name, telemetry_run_id)
        if result.tool_events:
            LOG.info(
                "collected tool telemetry container=%s worker=%s phase=%s events=%s",
                container_name,
                worker.name,
                phase,
                len(result.tool_events),
            )
    return result


def record_tool_events(client: GhostrootClient, project_id: str, result: ProcessResult) -> None:
    if not result.tool_events:
        return
    response = client.record_tool_events(project_id, result.tool_events)
    if response.ok:
        LOG.info("recorded tool telemetry project=%s events=%s", project_id, len(result.tool_events))
        return
    LOG.warning(
        "tool telemetry write failed project=%s status=%s body=%s",
        project_id,
        response.status_code,
        response.text,
    )


def _telemetry_path(existing: str | None) -> str:
    if existing:
        return f"{TELEMETRY_BIN}:{existing}"
    return f"{TELEMETRY_BIN}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _collect_current_tool_events(
    container_manager: ContainerManager,
    container_name: str,
    task_run_id: str,
) -> list[dict[str, Any]]:
    raw = container_manager.collect_tool_events(container_name)
    events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("task_run_id") != task_run_id:
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id or event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        events.append(
            {
                "event_id": event_id,
                "timestamp": _string_field(event.get("timestamp")),
                "task_type": _string_field(event.get("task_type")),
                "phase": _string_field(event.get("phase")),
                "intent_id": _optional_string_field(event.get("intent_id")),
                "worker": _optional_string_field(event.get("worker")),
                "task_run_id": task_run_id,
                "tool": _string_field(event.get("tool")),
                "command": _string_field(event.get("command")),
                "cwd": _optional_string_field(event.get("cwd")),
                "source": _string_field(event.get("source") or "path-wrapper"),
            }
        )
    return events


def _string_field(value: Any) -> str:
    if isinstance(value, str):
        return value
    return "" if value is None else str(value)


def _optional_string_field(value: Any) -> str | None:
    text = _string_field(value).strip()
    return text or None


def project_allows_conclude_fallback(client: GhostrootClient, project_id: str, *, worker_name: str, intent_id: str) -> bool:
    project = client.get_project(project_id)
    if project.project.status == "active":
        return True
    LOG.info(
        "skip conclude fallback because project is no longer active project=%s intent=%s worker=%s status=%s",
        project_id,
        intent_id,
        worker_name,
        project.project.status,
    )
    return False


def best_effort_release_reason(client: GhostrootClient, project_id: str, worker_name: str) -> None:
    response = client.release_reason(project_id, worker_name)
    if not response.ok and response.status_code not in (403, 409):
        LOG.warning(
            "reason release failed project=%s worker=%s status=%s",
            project_id,
            worker_name,
            response.status_code,
        )
    elif response.ok:
        LOG.info("released reason project=%s worker=%s", project_id, worker_name)
    else:
        LOG.info(
            "reason release skipped project=%s worker=%s status=%s",
            project_id,
            worker_name,
            response.status_code,
        )


def write_conclude_result(
    client: GhostrootClient,
    project_id: str,
    intent_id: str,
    worker_name: str,
    fact: str | FactPayload,
    *,
    source: str,
    phase_ms: int,
    total_ms: int | None = None,
) -> str:
    return write_conclude_result_with_fact_id(
        client,
        project_id,
        intent_id,
        worker_name,
        fact,
        source=source,
        phase_ms=phase_ms,
        total_ms=total_ms,
    ).status


def write_conclude_result_with_fact_id(
    client: GhostrootClient,
    project_id: str,
    intent_id: str,
    worker_name: str,
    fact: str | FactPayload,
    *,
    source: str,
    phase_ms: int,
    total_ms: int | None = None,
) -> ConcludeWriteResult:
    if isinstance(fact, FactPayload):
        description = fact.description
        response = client.conclude(project_id, intent_id, worker_name, description, **fact.client_kwargs())
    else:
        description = fact
        response = client.conclude(project_id, intent_id, worker_name, description)
    if response.ok:
        fact_id: str | None = None
        if isinstance(response.data, dict):
            fact = response.data.get("fact")
            if isinstance(fact, dict):
                candidate = fact.get("id")
                if isinstance(candidate, str) and candidate:
                    fact_id = candidate
        if total_ms is None:
            LOG.info(
                "intent concluded project=%s intent=%s worker=%s source=%s phase_ms=%s",
                project_id,
                intent_id,
                worker_name,
                source,
                phase_ms,
            )
        else:
            LOG.info(
                "intent concluded project=%s intent=%s worker=%s source=%s phase_ms=%s total_ms=%s",
                project_id,
                intent_id,
                worker_name,
                source,
                phase_ms,
                total_ms,
            )
        return ConcludeWriteResult(status="success", fact_id=fact_id)
    if response.status_code == 403:
        LOG.info(
            "project became inactive during conclude project=%s intent=%s worker=%s",
            project_id,
            intent_id,
            worker_name,
        )
    else:
        LOG.warning(
            "conclude write failed project=%s intent=%s worker=%s status=%s body=%s",
            project_id,
            intent_id,
            worker_name,
            response.status_code,
            response.text,
        )
    best_effort_release(client, project_id, intent_id, worker_name)
    return ConcludeWriteResult(status="failed", fact_id=None)


def best_effort_release(client: GhostrootClient, project_id: str, intent_id: str, worker_name: str) -> None:
    response = client.release(project_id, intent_id, worker_name)
    if not response.ok and response.status_code not in (403, 409):
        LOG.warning(
            "release failed project=%s intent=%s worker=%s status=%s",
            project_id,
            intent_id,
            worker_name,
            response.status_code,
        )
    elif response.ok:
        LOG.info("released intent project=%s intent=%s worker=%s", project_id, intent_id, worker_name)
    else:
        LOG.info(
            "release skipped project=%s intent=%s worker=%s status=%s",
            project_id,
            intent_id,
            worker_name,
            response.status_code,
        )
