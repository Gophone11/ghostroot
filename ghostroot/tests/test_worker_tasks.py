from __future__ import annotations

from collections.abc import Iterator

from ghostroot.dispatcher.protocol.client import ApiResult
from ghostroot.dispatcher.runtime.cancellation import TaskCancellation
from ghostroot.dispatcher.runtime.process import ProcessResult
from ghostroot.dispatcher.tasks.common import HealthcheckRun
from ghostroot.dispatcher.tasks import bootstrap, explore, reason
from ghostroot.server.models import Fact

from conftest import (
    FakeClient,
    FakeContainerManager,
    FakeDriver,
    FakeLease,
    make_config,
    make_intent,
    make_project,
)


def _healthy(*_args, **_kwargs) -> HealthcheckRun:
    return HealthcheckRun(ProcessResult(0, "", ""), duration_ms=1)


def _lease_factory(lease: FakeLease):
    return lambda *_args, **_kwargs: lease


def test_reason_writes_graph_snapshot_and_creates_intent(monkeypatch) -> None:
    config = make_config()
    project = make_project()
    client = FakeClient(project)
    containers = FakeContainerManager()
    driver = FakeDriver()
    lease = FakeLease()
    graph_yaml = "project:\n  title: huge\n" + ("x" * 100_000)

    monkeypatch.setattr(reason, "get_driver", lambda _name: driver)
    monkeypatch.setattr(reason.HeartbeatLease, "for_reason", _lease_factory(lease))
    monkeypatch.setattr(reason, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        reason,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"intents":[{"from":["f001"],"description":"next step","kind":"enumerate"}]}}',
            "",
        ),
    )

    outcome = reason.run_reason_task(
        config,
        client,
        containers,
        project,
        graph_yaml,
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.created_intents == [("proj_001", ["f001"], "next step", "test-worker")]
    assert client.created_intent_payloads[0]["kind"] == "enumerate"
    assert client.released_reasons == [("proj_001", "test-worker")]
    assert lease.started and lease.stopped
    assert len(containers.writes) == 1
    container_name, path, content = containers.writes[0]
    assert container_name == "container-proj_001"
    assert path.startswith("/tmp/ghostroot-prompts/reason_execute-")
    assert path.endswith("/graph.yaml")
    assert content == graph_yaml
    assert graph_yaml not in driver.execute_prompts[0]
    assert path in driver.execute_prompts[0]


def test_reason_terminal_failure_guard_stops_project_without_worker_execution(monkeypatch) -> None:
    config = make_config()
    project = make_project()
    project.facts.append(
        Fact(
            id="f016",
            description="target VM services remain hung: SSH sends no banner and HTTP returns empty reply after DirtyCOW",
            kind="exploration_result",
            outcome="negative",
            goal_relevance="rules_out",
            next_policy="stop",
            atoms=[
                {
                    "subject": "192.168.24.132:22 SSH daemon",
                    "predicate": "sends_identification_banner",
                    "object": "never - banner is empty byte string",
                    "polarity": "negative",
                }
            ],
        )
    )
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    monkeypatch.setattr(reason, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(reason.HeartbeatLease, "for_reason", _lease_factory(lease))
    monkeypatch.setattr(
        reason,
        "run_worker_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("reason worker must not run")),
    )

    outcome = reason.run_reason_task(
        config,
        client,
        containers,
        project,
        "graph",
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.status_updates == [("proj_001", "stopped")]
    assert client.created_intents == []
    assert client.released_reasons == [("proj_001", "test-worker")]
    assert containers.writes == []
    assert lease.started and lease.stopped


def test_reason_rules_out_stop_runs_worker_instead_of_stopping_project(monkeypatch) -> None:
    config = make_config()
    project = make_project()
    project.facts.append(
        Fact(
            id="f005",
            description="LXD route is blocked and no viable next step remains",
            kind="exploration_result",
            outcome="blocked",
            goal_relevance="rules_out",
            next_policy="stop",
            atoms=[
                {
                    "subject": "www-data",
                    "predicate": "member_of_group",
                    "object": "lxd",
                    "polarity": "negative",
                }
            ],
        )
    )
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()
    driver = FakeDriver()

    monkeypatch.setattr(reason, "get_driver", lambda _name: driver)
    monkeypatch.setattr(reason.HeartbeatLease, "for_reason", _lease_factory(lease))
    monkeypatch.setattr(reason, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        reason,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"intents":[{"from":["f005"],"description":"Validate a different privilege route instead of retrying the ruled-out LXD route","kind":"validate"}]}}',
            "",
        ),
    )

    outcome = reason.run_reason_task(
        config,
        client,
        containers,
        project,
        "graph",
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.status_updates == []
    assert client.created_intents == [
        (
            "proj_001",
            ["f005"],
            "Validate a different privilege route instead of retrying the ruled-out LXD route",
            "test-worker",
        )
    ]
    assert client.created_intent_payloads[0]["kind"] == "validate"
    assert client.released_reasons == [("proj_001", "test-worker")]
    assert len(containers.writes) == 1
    assert lease.started and lease.stopped


def test_reason_filters_repeated_ruled_out_route(monkeypatch) -> None:
    config = make_config()
    config.tasks.reason.filter_intents = True
    project = make_project()
    project.facts.append(
        Fact(
            id="f004",
            description="reverse shell blocked because target cannot route back to attacker",
            kind="exploration_result",
            outcome="blocked",
            goal_relevance="advances",
            next_policy="branch",
            atoms=[
                {
                    "subject": "reverse shell",
                    "predicate": "blocked_by",
                    "object": "target cannot route to attacker",
                    "polarity": "negative",
                }
            ],
        )
    )
    client = FakeClient(project)
    containers = FakeContainerManager()
    driver = FakeDriver()
    lease = FakeLease()

    monkeypatch.setattr(reason, "get_driver", lambda _name: driver)
    monkeypatch.setattr(reason.HeartbeatLease, "for_reason", _lease_factory(lease))
    monkeypatch.setattr(reason, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        reason,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"intents":[{"from":["f004"],"description":"Try another reverse shell that connects back to the attacker listener","kind":"exploit"}]}}',
            "",
        ),
    )

    outcome = reason.run_reason_task(
        config,
        client,
        containers,
        project,
        "graph",
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.created_intents == []
    assert client.released_reasons == [("proj_001", "test-worker")]
    assert lease.started and lease.stopped


def test_explore_terminal_failure_guard_releases_claim_without_worker_execution(monkeypatch) -> None:
    config = make_config()
    intent = make_intent()
    project = make_project(intents=[intent])
    project.facts.append(
        Fact(
            id="f016",
            description="target VM services remain hung: SSH sends no banner and HTTP returns empty reply after DirtyCOW",
            kind="exploration_result",
            outcome="negative",
            goal_relevance="rules_out",
            next_policy="stop",
            atoms=[
                {
                    "subject": "192.168.24.132:80",
                    "predicate": "serves",
                    "object": "empty reply to all HTTP requests - lighttpd still hung",
                    "polarity": "negative",
                }
            ],
        )
    )
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    monkeypatch.setattr(explore, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(explore.HeartbeatLease, "for_intent", _lease_factory(lease))
    monkeypatch.setattr(
        explore,
        "_run_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("explore worker must not run")),
    )

    outcome = explore.run_explore_task(
        config,
        client,
        containers,
        project,
        "graph",
        intent,
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.status_updates == [("proj_001", "stopped")]
    assert client.released == [("proj_001", "i001", "test-worker")]
    assert client.concluded == []
    assert containers.writes == []
    assert lease.started and lease.stopped


def test_explore_early_plain_text_exit_uses_conclude_fallback(monkeypatch) -> None:
    config = make_config()
    intent = make_intent()
    project = make_project(intents=[intent])
    client = FakeClient(project)
    containers = FakeContainerManager()
    driver = FakeDriver()
    lease = FakeLease()
    results: Iterator[ProcessResult] = iter(
        [
            ProcessResult(0, "Need inspect files and keep working.", ""),
            ProcessResult(
                0,
                '{"accepted":true,"data":{"description":"confirmed fact","kind":"exploration_result",'
                '"outcome":"positive","goal_relevance":"advances","next_policy":"branch","stop_condition":"succeeded","tags":["web"],'
                '"atoms":[{"subject":"target","predicate":"confirms","object":"fact","polarity":"positive"}]}}',
                "",
            ),
        ]
    )

    monkeypatch.setattr(explore, "get_driver", lambda _name: driver)
    monkeypatch.setattr(explore.HeartbeatLease, "for_intent", _lease_factory(lease))
    monkeypatch.setattr(explore, "run_healthcheck", _healthy)
    monkeypatch.setattr(explore, "_run_process", lambda *_args, **_kwargs: next(results))

    outcome = explore.run_explore_task(
        config,
        client,
        containers,
        project,
        "facts:\n- id: f001\n",
        intent,
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.concluded == [
        ("proj_001", "i001", "test-worker", "exploration_result | positive | advances | branch | target confirms fact")
    ]
    assert client.concluded_payloads[0]["kind"] == "exploration_result"
    assert client.concluded_payloads[0]["outcome"] == "positive"
    assert client.concluded_payloads[0]["goal_relevance"] == "advances"
    assert client.concluded_payloads[0]["next_policy"] == "branch"
    assert client.concluded_payloads[0]["stop_condition"] == "succeeded"
    assert client.concluded_payloads[0]["tags"] == ["web"]
    assert client.concluded_payloads[0]["atoms"] == [
        {"subject": "target", "predicate": "confirms", "object": "fact", "polarity": "positive"}
    ]
    assert len(containers.writes) == 2
    assert "/explore_execute-" in containers.writes[0][1]
    assert "/explore_conclude-" in containers.writes[1][1]
    assert len(driver.execute_prompts) == 1
    assert len(driver.conclude_prompts) == 1
    assert lease.started and lease.stopped


def test_explore_healthcheck_failure_releases_claim(monkeypatch) -> None:
    config = make_config()
    config.runtime.worker_healthcheck = "startup_and_task"
    intent = make_intent()
    project = make_project(intents=[intent])
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    monkeypatch.setattr(explore, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(explore.HeartbeatLease, "for_intent", _lease_factory(lease))
    monkeypatch.setattr(
        explore,
        "run_healthcheck",
        lambda *_args, **_kwargs: HealthcheckRun(ProcessResult(1, "", "unhealthy"), duration_ms=1),
    )

    outcome = explore.run_explore_task(
        config,
        client,
        containers,
        project,
        "graph",
        intent,
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "unhealthy"
    assert client.released == [("proj_001", "i001", "test-worker")]
    assert containers.writes == []


def test_bootstrap_success_concludes_fact_then_completes_project(monkeypatch) -> None:
    config = make_config()
    intent = make_intent()
    project = make_project(intents=[intent])
    client = FakeClient(project)
    containers = FakeContainerManager()
    driver = FakeDriver()
    lease = FakeLease()

    monkeypatch.setattr(bootstrap, "get_driver", lambda _name: driver)
    monkeypatch.setattr(bootstrap.HeartbeatLease, "for_intent", _lease_factory(lease))
    monkeypatch.setattr(bootstrap, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        bootstrap,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"fact":{"description":"solved","kind":"goal_proof","outcome":"goal_proof",'
            '"goal_relevance":"proves_goal","next_policy":"complete","stop_condition":"complete",'
            '"tags":["proof"],"atoms":[{"subject":"goal","predicate":"is_satisfied_by","object":"solved"}]},'
            '"complete":{"description":"goal met"}}}',
            "",
        ),
    )

    outcome = bootstrap.run_bootstrap_task(
        config,
        client,
        containers,
        project,
        intent,
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.concluded == [
        ("proj_001", "i001", "test-worker", "goal_proof | goal_proof | proves_goal | complete | goal is_satisfied_by solved")
    ]
    assert client.concluded_payloads[0]["kind"] == "goal_proof"
    assert client.concluded_payloads[0]["outcome"] == "goal_proof"
    assert client.concluded_payloads[0]["goal_relevance"] == "proves_goal"
    assert client.concluded_payloads[0]["next_policy"] == "complete"
    assert client.concluded_payloads[0]["stop_condition"] == "complete"
    assert client.concluded_payloads[0]["tags"] == ["proof"]
    assert client.concluded_payloads[0]["atoms"] == [
        {"subject": "goal", "predicate": "is_satisfied_by", "object": "solved"}
    ]
    assert client.completed == [("proj_001", ["f002"], "goal met", "test-worker")]
    assert lease.started and lease.stopped


def test_reason_complete_treats_inactive_project_as_success(monkeypatch) -> None:
    config = make_config()
    project = make_project()
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    def complete(*_args, **_kwargs) -> ApiResult:
        return ApiResult(403, text="inactive")

    client.complete = complete  # type: ignore[method-assign]
    monkeypatch.setattr(reason, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(reason.HeartbeatLease, "for_reason", _lease_factory(lease))
    monkeypatch.setattr(reason, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        reason,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"complete":{"from":["f001"],"description":"done"}}}',
            "",
        ),
    )

    outcome = reason.run_reason_task(
        config,
        client,
        containers,
        project,
        "graph",
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.released_reasons == [("proj_001", "test-worker")]


def test_reason_startup_only_mode_skips_task_healthcheck(monkeypatch) -> None:
    config = make_config()
    config.runtime.worker_healthcheck = "startup_only"
    project = make_project()
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    monkeypatch.setattr(reason, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(reason.HeartbeatLease, "for_reason", _lease_factory(lease))
    monkeypatch.setattr(
        reason,
        "run_healthcheck",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("task healthcheck should be skipped")),
    )
    monkeypatch.setattr(
        reason,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"intents":[{"from":["f001"],"description":"next"}]}}',
            "",
        ),
    )

    outcome = reason.run_reason_task(
        config,
        client,
        containers,
        project,
        "graph",
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.created_intents == [("proj_001", ["f001"], "next", "test-worker")]
