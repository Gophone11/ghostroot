from __future__ import annotations

from ghostroot.dispatcher.runtime.containers import TELEMETRY_BIN, ContainerManager
from ghostroot.dispatcher.tasks.common import _collect_current_tool_events, _telemetry_path


class FakeTelemetryContainerManager:
    def __init__(self, raw: str):
        self.raw = raw

    def collect_tool_events(self, _container_name: str) -> str:
        return self.raw


def test_collect_current_tool_events_filters_by_task_run_and_deduplicates() -> None:
    raw = "\n".join(
        [
            '{"event_id":"evt-1","timestamp":"2026-01-01T00:00:00Z","project_id":"proj_001",'
            '"task_type":"explore","phase":"explore_execute","intent_id":"i001","worker":"w",'
            '"task_run_id":"tr_keep","tool":"curl","command":"curl http://target","cwd":"/work",'
            '"source":"path-wrapper"}',
            '{"event_id":"evt-1","timestamp":"2026-01-01T00:00:00Z","project_id":"proj_001",'
            '"task_type":"explore","phase":"explore_execute","intent_id":"i001","worker":"w",'
            '"task_run_id":"tr_keep","tool":"curl","command":"curl http://target","cwd":"/work",'
            '"source":"path-wrapper"}',
            '{"event_id":"evt-2","timestamp":"2026-01-01T00:00:01Z","project_id":"proj_001",'
            '"task_type":"explore","phase":"explore_execute","intent_id":"i001","worker":"w",'
            '"task_run_id":"tr_skip","tool":"nmap","command":"nmap target","cwd":"/work",'
            '"source":"path-wrapper"}',
            "not-json",
        ]
    )

    events = _collect_current_tool_events(FakeTelemetryContainerManager(raw), "container", "tr_keep")  # type: ignore[arg-type]

    assert events == [
        {
            "event_id": "evt-1",
            "timestamp": "2026-01-01T00:00:00Z",
            "task_type": "explore",
            "phase": "explore_execute",
            "intent_id": "i001",
            "worker": "w",
            "task_run_id": "tr_keep",
            "tool": "curl",
            "command": "curl http://target",
            "cwd": "/work",
            "source": "path-wrapper",
        }
    ]


def test_telemetry_path_prepends_wrapper_directory() -> None:
    assert _telemetry_path("/usr/bin") == f"{TELEMETRY_BIN}:/usr/bin"
    assert _telemetry_path(None).startswith(f"{TELEMETRY_BIN}:")


def test_tool_telemetry_install_script_records_task_run_id() -> None:
    script = ContainerManager._tool_telemetry_install_script()

    assert "GHOSTROOT_TASK_RUN_ID" in script
    assert "task_run_id" in script
    assert "curl" in script
