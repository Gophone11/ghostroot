from __future__ import annotations

from contextlib import suppress
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from typing import Any

import requests

from ghostroot.dispatcher.workers.adapters.claudecode import ANTHROPIC_VERSION
from ghostroot.dispatcher.config import DispatchConfig, WorkerConfig
from ghostroot.dispatcher.contracts import parse_json_output, validate_report_payload
from ghostroot.dispatcher.prompting import format_json_block, load_prompt, render_prompt
from ghostroot.dispatcher.protocol.client import GhostrootClient
from ghostroot.dispatcher.runtime.cancellation import TaskCancellation
from ghostroot.dispatcher.runtime.heartbeat import HeartbeatLease
from ghostroot.dispatcher.runtime.process import ProcessResult
from ghostroot.dispatcher.tasks.common import cancel_reason, did_timeout, preview
from ghostroot.dispatcher.workers.base import DriverResult, WorkerDriver
from ghostroot.dispatcher.workers.registry import get_driver
from ghostroot.server.models import ReportContext

LOG = logging.getLogger(__name__)
REPORT_PROMPT_INLINE_LIMIT_BYTES = 16_000
CLAUDECODE_REPORT_MAX_TOKENS = 8192


class LocalManagedProcess:
    def __init__(self, argv: list[str], env: dict[str, str], *, timeout_seconds: int):
        self._argv = argv
        self._env = env
        self._timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._cancel_reason: str | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._process = subprocess.Popen(
            self._argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, **self._env},
        )

    def communicate(self) -> ProcessResult:
        assert self._process is not None
        try:
            stdout, stderr = self._process.communicate(timeout=self._timeout_seconds)
            return ProcessResult(
                returncode=self._process.returncode,
                stdout=stdout,
                stderr=stderr,
                cancelled=self._cancel_reason is not None,
                cancel_reason=self._cancel_reason,
            )
        except subprocess.TimeoutExpired:
            self.kill()
            stdout, stderr = self._process.communicate()
            return ProcessResult(
                returncode=self._process.returncode if self._process.returncode is not None else 124,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                cancelled=self._cancel_reason is not None,
                cancel_reason=self._cancel_reason,
            )

    def kill(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.kill()

    def cancel(self, reason: str) -> None:
        with self._lock:
            if self._cancel_reason is None:
                self._cancel_reason = reason
        self.kill()


def run_report_task(
    config: DispatchConfig,
    client: GhostrootClient,
    project_id: str,
    report_id: str,
    worker: WorkerConfig,
    cancellation: TaskCancellation,
) -> str:
    driver = get_driver(worker.type)
    task_started = time.perf_counter()
    lease = HeartbeatLease.for_report(client, project_id, report_id, worker.name, config.runtime.interval)
    prompt_file: Path | None = None
    lease.start()
    try:
        context = client.get_report_context(project_id, report_id)
        prompt = render_prompt(
            load_prompt(config.runtime.prompt_group, "report.md"),
            {"report_context": format_report_context(context)},
        )
        execute_started = time.perf_counter()
        if worker.type == "claudecode":
            result = run_claudecode_report_api(worker, prompt, timeout_seconds=config.tasks.report.timeout)
        else:
            command, prompt_file = build_report_command(driver, worker, prompt)
            process = LocalManagedProcess(
                command.argv,
                dict(worker.env),
                timeout_seconds=config.tasks.report.timeout,
            )
            try:
                process.start()
            except OSError as exc:
                LOG.warning(
                    "report process start failed project=%s report=%s worker=%s error=%s",
                    project_id,
                    report_id,
                    worker.name,
                    exc,
                )
                _best_effort_fail_report(client, project_id, report_id, worker.name, f"report process start failed: {exc}")
                return "failed"
            lease.attach_process(process)  # type: ignore[arg-type]
            cancellation.attach_process(process)  # type: ignore[arg-type]
            try:
                result = process.communicate()
            finally:
                lease.attach_process(None)
                cancellation.attach_process(None)
        execute_ms = int((time.perf_counter() - execute_started) * 1000)
        total_ms = int((time.perf_counter() - task_started) * 1000)

        cancelled = cancel_reason(result, cancellation)
        if cancelled is not None:
            LOG.info(
                "report cancelled project=%s report=%s worker=%s reason=%s execute_ms=%s",
                project_id,
                report_id,
                worker.name,
                cancelled,
                execute_ms,
            )
            _best_effort_fail_report(client, project_id, report_id, worker.name, f"cancelled: {cancelled}")
            return "cancelled"
        if lease.failure is not None:
            LOG.warning(
                "heartbeat lost during report project=%s report=%s worker=%s status=%s execute_ms=%s",
                project_id,
                report_id,
                worker.name,
                lease.failure.status_code,
                execute_ms,
            )
            return "failed"
        if did_timeout(result):
            message = "report timed out"
            LOG.warning(
                "report timed out project=%s report=%s worker=%s execute_ms=%s total_ms=%s stdout_preview=%s stderr_preview=%s",
                project_id,
                report_id,
                worker.name,
                execute_ms,
                total_ms,
                preview(result.stdout),
                preview(result.stderr),
            )
            _best_effort_fail_report(client, project_id, report_id, worker.name, message)
            return "failed"
        if result.returncode != 0:
            message = f"report worker failed with exit code {result.returncode}: {preview(result.stderr, 500)}"
            LOG.warning(
                "report command failed project=%s report=%s worker=%s code=%s execute_ms=%s total_ms=%s stderr_preview=%s",
                project_id,
                report_id,
                worker.name,
                result.returncode,
                execute_ms,
                total_ms,
                preview(result.stderr),
            )
            _best_effort_fail_report(client, project_id, report_id, worker.name, message)
            return "failed"

        try:
            model_output = driver.extract_response_text(result.stdout, result.stderr)
            payload = parse_json_output(model_output)
            kind, data = validate_report_payload(
                payload,
                fact_ids={fact.id for fact in context.facts},
                intent_ids={intent.id for intent in context.intents},
            )
        except Exception as exc:
            LOG.warning(
                "report parse failed project=%s report=%s worker=%s error=%s stdout_preview=%s stderr_preview=%s",
                project_id,
                report_id,
                worker.name,
                exc,
                preview(result.stdout),
                preview(result.stderr),
            )
            _best_effort_fail_report(client, project_id, report_id, worker.name, f"invalid report output: {exc}")
            return "failed"

        if kind == "rejected":
            LOG.warning("report rejected project=%s report=%s worker=%s", project_id, report_id, worker.name)
            _best_effort_fail_report(client, project_id, report_id, worker.name, "reporter rejected the task")
            return "rejected"
        assert data is not None
        response = client.complete_report(
            project_id,
            report_id,
            worker.name,
            data["markdown"],
            data["attack_path_summary"],
            data["confidence"],
            data["gaps"],
        )
        if not response.ok:
            LOG.warning(
                "report complete write failed project=%s report=%s worker=%s status=%s body=%s",
                project_id,
                report_id,
                worker.name,
                response.status_code,
                response.text,
            )
            return "failed"
        LOG.info("report completed project=%s report=%s worker=%s total_ms=%s", project_id, report_id, worker.name, total_ms)
        return "success"
    finally:
        lease.stop()
        if prompt_file is not None:
            with suppress(OSError):
                prompt_file.unlink()


def build_report_command(
    driver: WorkerDriver,
    worker: WorkerConfig,
    prompt: str,
    *,
    prompt_dir: Path | None = None,
) -> tuple[DriverResult, Path | None]:
    session = driver.prepare_session()
    if len(prompt.encode("utf-8")) <= REPORT_PROMPT_INLINE_LIMIT_BYTES:
        return driver.build_execute(worker, prompt, session), None

    prompt_file = _write_report_prompt_file(prompt, prompt_dir)
    reference_prompt = (
        "The full Ghostroot report-generation prompt and evidence context are stored in this local file:\n\n"
        f"{prompt_file}\n\n"
        "Read the entire file before answering. Treat that file as the authoritative prompt and context. "
        "Return only the JSON object required by the file."
    )
    return driver.build_execute(worker, reference_prompt, session), prompt_file


def run_claudecode_report_api(worker: WorkerConfig, prompt: str, *, timeout_seconds: int) -> ProcessResult:
    env = worker.env
    try:
        response = requests.post(
            f"{env['ANTHROPIC_BASE_URL']}/v1/messages",
            headers={
                "Authorization": f"Bearer {env['ANTHROPIC_AUTH_TOKEN']}",
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": env["ANTHROPIC_MODEL"],
                "max_tokens": int(env.get("GHOSTROOT_REPORT_MAX_TOKENS", str(CLAUDECODE_REPORT_MAX_TOKENS))),
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout_seconds,
        )
    except requests.Timeout:
        return ProcessResult(returncode=124, stdout="", stderr="claudecode report API timed out", timed_out=True)
    except requests.RequestException as exc:
        return ProcessResult(returncode=1, stdout="", stderr=f"claudecode report API request failed: {exc}")

    if not response.ok:
        return ProcessResult(
            returncode=1,
            stdout="",
            stderr=f"claudecode report API HTTP {response.status_code}: {response.text}",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        return ProcessResult(returncode=1, stdout=response.text, stderr=f"claudecode report API returned non-JSON: {exc}")

    text = _extract_anthropic_text(payload)
    if not text:
        return ProcessResult(returncode=1, stdout=response.text, stderr="claudecode report API returned no text content")
    return ProcessResult(returncode=0, stdout=text, stderr="")


def _extract_anthropic_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts).strip()


def _write_report_prompt_file(prompt: str, prompt_dir: Path | None) -> Path:
    if prompt_dir is not None:
        prompt_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix="ghostroot-report-prompt-",
        suffix=".md",
        dir=prompt_dir,
        delete=False,
    ) as file:
        file.write(prompt)
        return Path(file.name)


def format_report_context(context: ReportContext) -> str:
    intent_by_id = {intent.id: intent for intent in context.intents}
    main_path = [
        intent_by_id[intent_id].model_dump(by_alias=True)
        for intent_id in context.main_path_intent_ids
        if intent_id in intent_by_id
    ]
    payload = {
        "project": context.project.model_dump(),
        "reconstructed_main_path": main_path,
        "evidence_pool": {
            "facts": [fact.model_dump() for fact in context.facts],
            "intents": [intent.model_dump(by_alias=True) for intent in context.intents],
            "hints": [hint.model_dump() for hint in context.hints],
            "timeline": context.timeline,
        },
        "output_language": "zh-CN",
        "technical_detail_policy": "preserve recorded technical strings exactly",
    }
    return format_json_block(payload)


def _best_effort_fail_report(client: GhostrootClient, project_id: str, report_id: str, worker_name: str, error: str) -> None:
    response = client.fail_report(project_id, report_id, worker_name, error)
    if not response.ok and response.status_code not in (403, 409):
        LOG.warning(
            "report fail write failed project=%s report=%s worker=%s status=%s body=%s",
            project_id,
            report_id,
            worker_name,
            response.status_code,
            response.text,
        )
