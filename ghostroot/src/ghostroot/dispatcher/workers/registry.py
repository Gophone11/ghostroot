from __future__ import annotations

from ghostroot.dispatcher.workers.adapters import ClaudeCodeDriver, CodexDriver, MockDriver, PiDriver
from ghostroot.dispatcher.workers.base import WorkerDriver


DRIVERS: dict[str, WorkerDriver] = {
    "claudecode": ClaudeCodeDriver(),
    "codex": CodexDriver(),
    "pi": PiDriver(),
    "mock": MockDriver(),
}


def get_driver(name: str) -> WorkerDriver:
    return DRIVERS[name]
