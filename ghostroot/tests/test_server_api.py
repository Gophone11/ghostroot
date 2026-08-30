from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from ghostroot.server import db
from ghostroot.server.app import app
from ghostroot.server.routers.reports import get_report_context


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(tmp_path / "ghostroot.db")
    with TestClient(app) as test_client:
        yield test_client


def _create_project(client: TestClient) -> str:
    response = client.post(
        "/projects",
        json={
            "title": "test",
            "origin": "starting point",
            "goal": "finish",
            "hints": [{"content": "initial clue", "creator": "human"}],
        },
    )
    assert response.status_code == 201
    assert response.json()["project"]["bootstrap_enabled"] is True
    return response.json()["project"]["id"]


def test_project_workflow_create_conclude_complete_and_reopen(client: TestClient) -> None:
    project_id = _create_project(client)

    response = client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "investigate", "creator": "reasoner", "worker": None},
    )
    assert response.status_code == 201
    assert response.json()["id"] == "i001"

    response = client.post(
        f"/projects/{project_id}/intents/i001/heartbeat",
        json={"worker": "explorer"},
    )
    assert response.status_code == 200
    assert response.json()["worker"] == "explorer"

    response = client.post(
        f"/projects/{project_id}/intents/i001/conclude",
        json={"worker": "explorer", "description": "new fact"},
    )
    assert response.status_code == 200
    assert response.json()["fact"] == {"id": "f001", "description": "new fact"}

    response = client.post(
        f"/projects/{project_id}/complete",
        json={"from": ["f001"], "description": "solved", "worker": "reasoner"},
    )
    assert response.status_code == 200
    assert response.json()["to"] == "goal"

    response = client.post(
        f"/projects/{project_id}/reopen",
        json={"description": "human correction", "creator": "human"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["project"]["status"] == "active"
    assert payload["fact"] == {
        "id": "f002",
        "description": "human correction",
        "kind": "human_feedback",
        "outcome": "corrective",
        "goal_relevance": "rules_out",
        "next_policy": "branch",
    }
    assert payload["intent"]["from"] == ["f001"]
    assert payload["intent"]["to"] == "f002"
    assert payload["intent"]["kind"] == "complete_check"
    assert payload["intent"]["stop_condition"] == "corrective"


def test_structured_fact_and_intent_fields_are_optional_and_exported(client: TestClient) -> None:
    project_id = _create_project(client)

    response = client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": ["origin"],
            "description": "validate SQL injection",
            "kind": "validate",
            "creator": "reasoner",
            "worker": None,
        },
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "validate"

    client.post(f"/projects/{project_id}/intents/i001/heartbeat", json={"worker": "explorer"})
    response = client.post(
        f"/projects/{project_id}/intents/i001/conclude",
        json={
            "worker": "explorer",
            "description": "uid parameter is injectable",
            "kind": "exploration_result",
            "outcome": "positive",
            "goal_relevance": "advances",
            "next_policy": "branch",
            "stop_condition": "succeeded",
            "tags": ["web", "sqli"],
            "atoms": [
                {
                    "subject": "endpoint.uid",
                    "predicate": "vulnerable_to",
                    "object": "vuln_sqli",
                    "polarity": "positive",
                }
            ],
        },
    )

    assert response.status_code == 200
    fact = response.json()["fact"]
    assert fact["kind"] == "exploration_result"
    assert fact["outcome"] == "positive"
    assert fact["goal_relevance"] == "advances"
    assert fact["next_policy"] == "branch"
    assert fact["tags"] == ["web", "sqli"]
    assert fact["atoms"][0]["predicate"] == "vulnerable_to"
    assert fact["description"] == "exploration_result | positive | advances | branch | endpoint.uid vulnerable_to vuln_sqli"

    detail = client.get(f"/projects/{project_id}").json()
    assert detail["intents"][0]["kind"] == "validate"
    assert detail["intents"][0]["stop_condition"] == "succeeded"
    assert detail["facts"][2]["atoms"][0]["subject"] == "endpoint.uid"

    exported = client.get(f"/projects/{project_id}/export?format=yaml").text
    assert "kind: exploration_result" in exported
    assert "outcome: positive" in exported
    assert "goal_relevance: advances" in exported
    assert "next_policy: branch" in exported
    assert "status: concluded" in exported
    assert "stop_condition: succeeded" in exported
    assert "- web" in exported
    assert "predicate: vulnerable_to" in exported
    assert "description: uid parameter is injectable" not in exported
    assert "description: validate SQL injection" in exported


def test_report_context_includes_structured_fact_columns(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(tmp_path / "ghostroot.db")
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO projects (id, title, status, bootstrap_enabled, created_at)
            VALUES ('proj_001', 'test', 'completed', 1, '2026-01-01T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO facts (
                id, project_id, description, kind, outcome, goal_relevance,
                next_policy, tags_json, atoms_json
            )
            VALUES (
                'origin', 'proj_001', 'starting point', NULL, NULL, NULL,
                NULL, NULL, NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO facts (
                id, project_id, description, kind, outcome, goal_relevance,
                next_policy, tags_json, atoms_json
            )
            VALUES (
                'f001', 'proj_001',
                'exploration_result | positive | proves_goal | complete | endpoint.uid vulnerable_to vuln_sqli',
                'exploration_result', 'positive', 'proves_goal', 'complete',
                '["web", "sqli"]',
                '[{"subject":"endpoint.uid","predicate":"vulnerable_to","object":"vuln_sqli","polarity":"positive"}]'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO facts (
                id, project_id, description, kind, outcome, goal_relevance,
                next_policy, tags_json, atoms_json
            )
            VALUES (
                'goal', 'proj_001', 'solved', 'completion', 'positive',
                'proves_goal', 'stop', NULL, NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO intents (
                id, project_id, to_fact_id, description, kind, stop_condition,
                creator, worker, created_at, concluded_at
            )
            VALUES (
                'i001', 'proj_001', 'f001', 'validate SQL injection',
                'validate', 'succeeded', 'reasoner', 'explorer',
                '2026-01-01T00:00:01Z', '2026-01-01T00:00:02Z'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO intent_sources (intent_id, project_id, fact_id)
            VALUES ('i001', 'proj_001', 'origin')
            """
        )
        conn.execute(
            """
            INSERT INTO project_reports (
                id, project_id, status, source_completed_intent_id, created_at
            )
            VALUES ('r001', 'proj_001', 'pending', 'i001', '2026-01-01T00:00:03Z')
            """
        )

    context = get_report_context("proj_001", "r001")
    fact = next(item for item in context.facts if item.id == "f001")
    assert fact.goal_relevance == "proves_goal"
    assert fact.next_policy == "complete"
    assert fact.atoms[0]["predicate"] == "vulnerable_to"


def test_stopping_project_releases_claims_and_reason_but_keeps_hints_writable(client: TestClient) -> None:
    project_id = _create_project(client)
    client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "work", "creator": "worker-a", "worker": "worker-a"},
    )
    client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-b", "trigger": "facts:2->3"},
    )

    response = client.put(f"/projects/{project_id}/status", json={"status": "stopped"})
    assert response.status_code == 200
    assert response.json()["reason"] is None

    detail = client.get(f"/projects/{project_id}").json()
    assert detail["intents"][0]["worker"] is None
    assert client.post(
        f"/projects/{project_id}/hints",
        json={"content": "manual note", "creator": "human"},
    ).status_code == 201
    assert client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "blocked", "creator": "reasoner", "worker": None},
    ).status_code == 403


def test_intent_creation_rejects_goal_source_and_mismatched_initial_worker(client: TestClient) -> None:
    project_id = _create_project(client)

    assert client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["goal"], "description": "invalid", "creator": "reasoner", "worker": None},
    ).status_code == 400
    assert client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "invalid", "creator": "reasoner", "worker": "explorer"},
    ).status_code == 400


def test_settings_and_export_are_backed_by_the_same_database(client: TestClient) -> None:
    project_id = _create_project(client)

    response = client.put("/settings", json={"intent_timeout": 30, "reason_timeout": 45, "report_timeout": 60})
    assert response.status_code == 200
    assert client.get("/settings").json() == {"intent_timeout": 30, "reason_timeout": 45, "report_timeout": 60}

    exported = client.get(f"/projects/{project_id}/export?format=yaml")
    assert exported.status_code == 200
    assert "origin: starting point" in exported.text
    assert "goal: finish" in exported.text
    assert client.get(f"/projects/{project_id}/export?format=invalid").status_code == 400


def test_yaml_export_can_exclude_tool_events_for_model_context(client: TestClient) -> None:
    project_id = _create_project(client)

    response = client.post(
        f"/projects/{project_id}/tool-events",
        json={
            "events": [
                {
                    "event_id": "evt-001",
                    "timestamp": "2026-01-01T00:00:01Z",
                    "task_type": "explore",
                    "phase": "explore_execute",
                    "intent_id": "i001",
                    "worker": "worker-a",
                    "task_run_id": "tr_001",
                    "tool": "curl",
                    "command": "curl http://target/",
                    "cwd": "/workspace",
                    "source": "path-wrapper",
                }
            ]
        },
    )
    assert response.status_code == 200

    default_export = client.get(f"/projects/{project_id}/export?format=yaml")
    assert default_export.status_code == 200
    assert "tool_events:" not in default_export.text
    assert "command: curl http://target/" not in default_export.text

    user_export = client.get(f"/projects/{project_id}/export?format=yaml&include_tool_events=true")
    assert user_export.status_code == 200
    assert "tool_events:" in user_export.text
    assert "command: curl http://target/" in user_export.text

    model_export = client.get(f"/projects/{project_id}/export?format=yaml&include_tool_events=false")
    assert model_export.status_code == 200
    assert "tool_events:" not in model_export.text
    assert "command: curl http://target/" not in model_export.text


def test_expired_intent_and_reason_leases_can_be_reclaimed(client: TestClient) -> None:
    project_id = _create_project(client)
    client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "work", "creator": "worker-a", "worker": "worker-a"},
    )
    client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-a", "trigger": "bootstrap"},
    )
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE intents SET last_heartbeat_at = '2000-01-01T00:00:00Z' WHERE project_id = ?",
            (project_id,),
        )
        conn.execute(
            "UPDATE projects SET reason_last_heartbeat_at = '2000-01-01T00:00:00Z' WHERE id = ?",
            (project_id,),
        )

    response = client.post(
        f"/projects/{project_id}/intents/i001/heartbeat",
        json={"worker": "worker-b"},
    )
    assert response.status_code == 200
    assert response.json()["worker"] == "worker-b"

    response = client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-b", "trigger": "facts:2->3"},
    )
    assert response.status_code == 200
    assert response.json()["reason"]["worker"] == "worker-b"


def test_live_reason_lease_rejects_competing_worker(client: TestClient) -> None:
    project_id = _create_project(client)
    assert client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-a", "trigger": "bootstrap"},
    ).status_code == 200

    response = client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-b", "trigger": "facts:2->3"},
    )

    assert response.status_code == 409
    assert "worker-a" in response.json()["detail"]


def test_project_creation_persists_disabled_bootstrap_and_exports_it(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={
            "title": "no bootstrap",
            "origin": "start",
            "goal": "finish",
            "bootstrap_enabled": False,
        },
    )

    assert response.status_code == 201
    project_id = response.json()["project"]["id"]
    assert client.get(f"/projects/{project_id}").json()["project"]["bootstrap_enabled"] is False
    assert "bootstrap_enabled: false" in client.get(f"/projects/{project_id}/export?format=yaml").text


def test_project_creation_rejects_invalid_bootstrap_enabled(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={
            "title": "invalid bootstrap",
            "origin": "start",
            "goal": "finish",
            "bootstrap_enabled": "sometimes",
        },
    )

    assert response.status_code == 422
