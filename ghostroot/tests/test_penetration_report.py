from __future__ import annotations

from ghostroot.server.report import build_penetration_report, reconstruct_attack_path


def test_reconstruct_attack_path_follows_completion_dependencies() -> None:
    intents = [
        {"id": "i001", "to_fact_id": "f001", "description": "find admin panel"},
        {"id": "i002", "to_fact_id": "f002", "description": "use weak admin password"},
        {"id": "i003", "to_fact_id": "goal", "description": "web shell obtained"},
        {"id": "i004", "to_fact_id": "f999", "description": "unrelated branch"},
    ]
    sources_by_intent = {
        "i001": ["origin"],
        "i002": ["f001"],
        "i003": ["f002"],
        "i004": ["origin"],
    }

    path = reconstruct_attack_path(intents, sources_by_intent)

    assert [step["id"] for step in path] == ["i001", "i002", "i003"]


def test_build_penetration_report_contains_path_poc_notes_and_evidence() -> None:
    project = {
        "id": "proj_001",
        "title": "demo target",
        "status": "completed",
        "created_at": "2026-01-01T00:00:00Z",
    }
    facts = [
        {"id": "origin", "description": "Target is https://example.test in authorized lab scope."},
        {"id": "goal", "description": "Obtain a web server shell."},
        {"id": "f001", "description": "Admin panel exists at /admin."},
        {"id": "f002", "description": "Authenticated upload accepts server-side script files."},
    ]
    hints = [{"content": "Stay inside the lab target.", "creator": "human", "created_at": "2026-01-01T00:00:01Z"}]
    intents = [
        {
            "id": "i001",
            "to_fact_id": "f001",
            "description": "Enumerate common admin paths.",
            "creator": "reasoner",
            "worker": "agent-a",
            "created_at": "2026-01-01T00:00:02Z",
            "concluded_at": "2026-01-01T00:00:03Z",
        },
        {
            "id": "i002",
            "to_fact_id": "f002",
            "description": "Check upload behavior after authorized login.",
            "creator": "reasoner",
            "worker": "agent-b",
            "created_at": "2026-01-01T00:00:04Z",
            "concluded_at": "2026-01-01T00:00:05Z",
        },
        {
            "id": "i003",
            "to_fact_id": "goal",
            "description": "Shell access verified in the lab environment.",
            "creator": "agent-b",
            "worker": "agent-b",
            "created_at": "2026-01-01T00:00:06Z",
            "concluded_at": "2026-01-01T00:00:07Z",
        },
    ]
    sources_by_intent = {
        "i001": ["origin"],
        "i002": ["f001"],
        "i003": ["f002"],
    }

    report = build_penetration_report(project, facts, hints, intents, sources_by_intent)

    assert "# Penetration Report: demo target" in report
    assert "Reconstructed exploit path length: 3 step(s)." in report
    assert "### Step 1: `i001` -> `f001`" in report
    assert "### Step 3: `i003` -> `goal`" in report
    assert "**PoC Reproduction Notes**" in report
    assert "do not add unrecorded attack steps" in report
    assert "Shell access verified in the lab environment." in report
    assert "## 5. Evidence Index" in report
