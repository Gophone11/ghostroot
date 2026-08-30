from __future__ import annotations

import requests

from ghostroot.dispatcher.protocol.client import GhostrootClient
from ghostroot.dispatcher.runtime.startup_healthcheck import (
    StartupHealthcheckResult,
    _parse_stdout,
    format_failure_summary,
)


def test_client_request_failure_returns_status_zero() -> None:
    class Session:
        def request(self, *_args, **_kwargs):
            raise requests.ConnectionError("offline")

    client = GhostrootClient("http://server/")
    client._local.session = Session()

    result = client.create_intent("proj_001", ["f001"], "investigate", "reasoner")

    assert result.status_code == 0
    assert result.text == "offline"


def test_client_model_context_export_excludes_tool_events() -> None:
    class Response:
        text = "project: {}\n"

        def raise_for_status(self) -> None:
            return None

    class Session:
        params = None

        def get(self, *_args, **kwargs):
            self.params = kwargs["params"]
            return Response()

    session = Session()
    client = GhostrootClient("http://server/")
    client._local.session = session

    assert client.export_project_for_model_context("proj_001") == "project: {}\n"
    assert session.params == {"format": "yaml", "include_tool_events": "false"}


def test_startup_healthcheck_stdout_parser_extracts_status_and_compacts_preview() -> None:
    status, preview = _parse_stdout("http_status=200\n  hello\n  world  ")

    assert status == "200"
    assert preview == "hello world"


def test_startup_healthcheck_failure_summary_includes_worker_details() -> None:
    results = [
        StartupHealthcheckResult(
            worker_name="worker-a",
            ok=False,
            returncode=1,
            duration_ms=12,
            http_status="401",
            response_preview="unauthorized",
            stderr_preview="",
            command="curl",
        )
    ]

    assert format_failure_summary(results) == (
        "startup healthchecks failed for all workers: "
        "worker-a(http=401, code=1, preview=unauthorized)"
    )
