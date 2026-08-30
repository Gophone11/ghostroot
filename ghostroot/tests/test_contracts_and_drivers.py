from __future__ import annotations

import json

import pytest

from ghostroot.dispatcher.contracts import (
    parse_json_output,
    validate_bootstrap_execute_payload,
    validate_explore_payload,
    validate_report_payload,
    validate_reason_payload,
)
from ghostroot.dispatcher.runtime.process import ManagedProcess
from ghostroot.dispatcher.workers.adapters.pi import PiDriver


def test_parse_json_output_extracts_object_from_markdown_noise() -> None:
    assert parse_json_output('result:\n```json\n{"accepted": true, "data": {}}\n```') == {
        "accepted": True,
        "data": {},
    }


def test_reason_payload_limits_number_of_intents() -> None:
    kind, intents = validate_reason_payload(
        {
            "accepted": True,
            "data": {
                "intents": [
                    {"from": ["f001"], "description": "one"},
                    {"from": ["f001"], "description": "two"},
                ]
            },
        },
        open_intents_empty=True,
        max_intents=1,
    )

    assert kind == "intents"
    assert intents == [{"from": ["f001"], "description": "one"}]


def test_reason_payload_keeps_intent_kind() -> None:
    kind, intents = validate_reason_payload(
        {
            "accepted": True,
            "data": {
                "intents": [
                    {"from": ["f001"], "description": "check admin", "kind": "validate"},
                ]
            },
        },
        open_intents_empty=True,
        max_intents=3,
    )

    assert kind == "intents"
    assert intents == [{"from": ["f001"], "description": "check admin", "kind": "validate"}]


def test_reason_payload_requires_intent_when_none_are_open() -> None:
    with pytest.raises(ValueError, match="intents is required"):
        validate_reason_payload(
            {"accepted": True, "data": {}},
            open_intents_empty=True,
            max_intents=3,
        )


def test_explore_payload_rejects_planning_text() -> None:
    with pytest.raises(ValueError):
        validate_explore_payload(parse_json_output("Need inspect files and keep working."))


def test_explore_payload_accepts_structured_fact_fields() -> None:
    kind, fact = validate_explore_payload(
        {
            "accepted": True,
            "data": {
                "description": "admin login exists",
                "kind": "exploration_result",
                "outcome": "positive",
                "goal_relevance": "advances",
                "next_policy": "branch",
                "stop_condition": "succeeded",
                "tags": ["web", "auth"],
                "atoms": [
                    {
                        "subject": "endpoint:/admin",
                        "predicate": "exposes",
                        "object": "login page",
                        "polarity": "positive",
                    }
                ],
            },
        }
    )

    assert kind == "fact"
    assert fact is not None
    assert fact.description == "exploration_result | positive | advances | branch | endpoint:/admin exposes login page"
    assert fact.kind == "exploration_result"
    assert fact.outcome == "positive"
    assert fact.goal_relevance == "advances"
    assert fact.next_policy == "branch"
    assert fact.stop_condition == "succeeded"
    assert fact.tags == ["web", "auth"]
    assert fact.atoms == [
        {
            "subject": "endpoint:/admin",
            "predicate": "exposes",
            "object": "login page",
            "polarity": "positive",
        }
    ]


def test_bootstrap_payload_accepts_structured_fact_fields() -> None:
    kind, data = validate_bootstrap_execute_payload(
        {
            "accepted": True,
            "data": {
                "fact": {
                    "description": "goal proven",
                    "kind": "goal_proof",
                    "outcome": "goal_proof",
                    "goal_relevance": "proves_goal",
                    "next_policy": "complete",
                    "stop_condition": "complete",
                    "tags": ["proof"],
                    "atoms": [
                        {
                            "subject": "goal",
                            "predicate": "is_satisfied_by",
                            "object": "flag",
                            "polarity": "positive",
                        }
                    ],
                },
                "complete": {"description": "flag proves the goal"},
            },
        }
    )

    assert kind == "complete"
    assert data is not None
    assert data["fact"].kind == "goal_proof"
    assert data["fact"].goal_relevance == "proves_goal"
    assert data["fact"].next_policy == "complete"
    assert data["fact"].stop_condition == "complete"
    assert data["fact"].description == "goal_proof | goal_proof | proves_goal | complete | goal is_satisfied_by flag"
    assert data["complete_description"] == "flag proves the goal"


def test_report_payload_validates_evidence_references_and_markdown() -> None:
    kind, data = validate_report_payload(
        {
            "accepted": True,
            "data": {
                "attack_path_summary": [
                    {
                        "title": "获得管理入口",
                        "source_facts": ["origin"],
                        "intent_ids": ["i001"],
                        "result_fact": "f001",
                        "why_it_matters": "确认可进入后续验证点。",
                    }
                ],
                "poc_markdown": "# 报告\n\n1. 打开 https://example.test/admin 并确认返回登录页。",
                "confidence": "medium",
                "gaps": [],
            },
        },
        fact_ids={"origin", "goal", "f001"},
        intent_ids={"i001"},
    )

    assert kind == "report"
    assert data is not None
    assert data["confidence"] == "medium"
    assert data["attack_path_summary"][0]["result_fact"] == "f001"


def test_report_payload_rejects_back_references_in_poc() -> None:
    with pytest.raises(ValueError, match="expand recorded details inline"):
        validate_report_payload(
            {
                "accepted": True,
                "data": {
                    "attack_path_summary": [
                        {
                            "title": "获得管理入口",
                            "source_facts": ["origin"],
                            "intent_ids": ["i001"],
                            "result_fact": "f001",
                            "why_it_matters": "确认可进入后续验证点。",
                        }
                    ],
                    "poc_markdown": "# 报告\n\n1. 参考 f001 继续操作。",
                    "confidence": "medium",
                    "gaps": [],
                },
            },
            fact_ids={"origin", "goal", "f001"},
            intent_ids={"i001"},
        )


def test_pi_driver_extracts_session_and_last_assistant_text() -> None:
    driver = PiDriver()
    stdout = "\n".join(
        [
            json.dumps({"type": "session", "id": "session-123"}),
            json.dumps(
                {
                    "type": "turn_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": '{"accepted":true,"data":{}}'}],
                    },
                }
            ),
        ]
    )

    assert driver.extract_session(None, stdout, "") == "session-123"
    assert driver.extract_response_text(stdout, "") == '{"accepted":true,"data":{}}'


def test_close_stream_closes_response_even_when_stream_close_fails() -> None:
    class Response:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class Stream:
        def __init__(self) -> None:
            self._response = Response()

        def close(self) -> None:
            raise ValueError("already closed")

    stream = Stream()
    ManagedProcess._close_stream(stream)

    assert stream._response.closed
