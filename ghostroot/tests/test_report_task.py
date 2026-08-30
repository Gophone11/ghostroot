from __future__ import annotations

import json

from ghostroot.dispatcher.config import WorkerConfig
from ghostroot.dispatcher.tasks.report import build_report_command, format_report_context, run_claudecode_report_api
from ghostroot.dispatcher.workers.adapters.claudecode import ClaudeCodeDriver
from ghostroot.server.models import Fact, Hint, Intent, ProjectMeta, ReportContext


def test_format_report_context_layers_main_path_before_evidence_pool() -> None:
    context = ReportContext(
        project=ProjectMeta(
            id="proj_001",
            title="demo",
            status="completed",
            bootstrap_enabled=True,
            created_at="2026-01-01T00:00:00Z",
        ),
        facts=[
            Fact(id="origin", description="Target is https://example.test."),
            Fact(id="goal", description="Obtain shell."),
            Fact(id="f001", description="Admin panel at /admin."),
        ],
        intents=[
            Intent(
                id="i001",
                **{"from": ["origin"]},
                to="f001",
                description="Enumerate admin panel.",
                creator="reasoner",
                worker="agent",
                created_at="2026-01-01T00:00:01Z",
                concluded_at="2026-01-01T00:00:02Z",
            )
        ],
        hints=[
            Hint(
                id="h001",
                content="Authorized lab only.",
                creator="human",
                created_at="2026-01-01T00:00:00Z",
            )
        ],
        main_path_intent_ids=["i001"],
        timeline="timeline text",
    )

    rendered = format_report_context(context)
    payload = json.loads(rendered)

    assert list(payload)[:2] == ["project", "reconstructed_main_path"]
    assert payload["reconstructed_main_path"][0]["id"] == "i001"
    assert payload["evidence_pool"]["facts"][2]["description"] == "Admin panel at /admin."
    assert payload["output_language"] == "zh-CN"


def test_build_report_command_spills_large_prompt_to_file(tmp_path) -> None:
    worker = WorkerConfig(
        name="reporter",
        type="claudecode",
        task_types=["report"],
        max_running=1,
        priority=0,
        env={
            "ANTHROPIC_MODEL": "model",
            "ANTHROPIC_BASE_URL": "https://example.test/anthropic",
            "ANTHROPIC_AUTH_TOKEN": "token",
        },
    )
    prompt = "report prompt\n" + ("x" * 200_000)

    command, prompt_file = build_report_command(
        ClaudeCodeDriver(),
        worker,
        prompt,
        prompt_dir=tmp_path,
    )

    assert prompt_file is not None
    assert prompt_file.read_text(encoding="utf-8") == prompt
    assert max(len(arg.encode("utf-8")) for arg in command.argv) < 20_000
    assert str(prompt_file) in " ".join(command.argv)


def test_claudecode_report_uses_messages_api(monkeypatch) -> None:
    worker = WorkerConfig(
        name="reporter",
        type="claudecode",
        task_types=["report"],
        max_running=1,
        priority=0,
        env={
            "ANTHROPIC_MODEL": "model",
            "ANTHROPIC_BASE_URL": "https://example.test/anthropic",
            "ANTHROPIC_AUTH_TOKEN": "token",
        },
    )
    calls = []

    class Response:
        ok = True
        status_code = 200
        text = "{}"

        def json(self):
            return {"content": [{"type": "text", "text": '{"accepted":true,"data":{}}'}]}

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr("ghostroot.dispatcher.tasks.report.requests.post", fake_post)

    result = run_claudecode_report_api(worker, "report prompt", timeout_seconds=12)

    assert result.returncode == 0
    assert result.stdout == '{"accepted":true,"data":{}}'
    assert calls[0]["url"] == "https://example.test/anthropic/v1/messages"
    assert calls[0]["headers"]["Authorization"] == "Bearer token"
    assert calls[0]["json"]["model"] == "model"
    assert calls[0]["json"]["messages"] == [{"role": "user", "content": "report prompt"}]
    assert calls[0]["timeout"] == 12
