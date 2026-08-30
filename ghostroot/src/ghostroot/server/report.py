from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


Row = Mapping[str, Any]


def _value(row: Row, key: str, default: Any = "") -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _format_timestamp(value: str | None) -> str:
    if not value:
        return "n/a"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _bullet_text(text: str, indent: str = "  ") -> str:
    lines = str(text or "").strip().splitlines()
    if not lines:
        return f"{indent}- n/a"
    return "\n".join(f"{indent}{line}" if index else f"{indent}- {line}" for index, line in enumerate(lines))


def _numbered_block(text: str, indent: str = "   ") -> str:
    lines = str(text or "").strip().splitlines()
    if not lines:
        return f"{indent}n/a"
    return "\n".join(f"{indent}{line}" for line in lines)


def _fact_description(facts_by_id: dict[str, str], fact_id: str) -> str:
    return facts_by_id.get(fact_id, "")


def reconstruct_attack_path(
    intents: Sequence[Row],
    sources_by_intent: Mapping[str, Sequence[str]],
) -> list[Row]:
    producing_intent_by_fact: dict[str, Row] = {}
    completion_intents: list[Row] = []
    for intent in intents:
        to_fact_id = _value(intent, "to_fact_id", None)
        if not to_fact_id:
            continue
        if to_fact_id == "goal":
            completion_intents.append(intent)
        else:
            producing_intent_by_fact[to_fact_id] = intent

    if not completion_intents:
        return []

    completion = completion_intents[-1]
    ordered: list[Row] = []
    visited_intents: set[str] = set()
    visiting_facts: set[str] = set()

    def visit_fact(fact_id: str) -> None:
        if fact_id in {"origin", "goal"} or fact_id in visiting_facts:
            return
        producing_intent = producing_intent_by_fact.get(fact_id)
        if producing_intent is None:
            return
        visiting_facts.add(fact_id)
        intent_id = _value(producing_intent, "id")
        for source_id in sources_by_intent.get(intent_id, []):
            visit_fact(source_id)
        if intent_id not in visited_intents:
            ordered.append(producing_intent)
            visited_intents.add(intent_id)
        visiting_facts.remove(fact_id)

    completion_id = _value(completion, "id")
    for source_id in sources_by_intent.get(completion_id, []):
        visit_fact(source_id)
    if completion_id not in visited_intents:
        ordered.append(completion)

    return ordered


def build_penetration_report(
    project: Row,
    facts: Sequence[Row],
    hints: Sequence[Row],
    intents: Sequence[Row],
    sources_by_intent: Mapping[str, Sequence[str]],
) -> str:
    facts_by_id = {_value(fact, "id"): _value(fact, "description") for fact in facts}
    path = reconstruct_attack_path(intents, sources_by_intent)
    completion = next((intent for intent in path if _value(intent, "to_fact_id") == "goal"), None)

    title = _value(project, "title", "Untitled Project")
    project_id = _value(project, "id", "unknown")
    status = _value(project, "status", "unknown")
    origin = facts_by_id.get("origin", "")
    goal = facts_by_id.get("goal", "")

    lines: list[str] = [
        f"# Penetration Report: {title}",
        "",
        "> Generated from Ghostroot recorded facts and intents. Use this report only for systems where you have explicit authorization.",
        "",
        "## 1. Scope and Authorization",
        "",
        f"- Project ID: `{project_id}`",
        f"- Status: `{status}`",
        f"- Created At: {_format_timestamp(_value(project, 'created_at', None))}",
        "- Authorization Boundary: Confirm the target, timeframe, and allowed techniques before reproducing any step.",
        "",
        "### Origin",
        "",
        _bullet_text(origin, ""),
        "",
        "### Goal",
        "",
        _bullet_text(goal, ""),
        "",
        "## 2. Executive Summary",
        "",
    ]

    if path:
        lines.extend(
            [
                f"- Reconstructed exploit path length: {len(path)} step(s).",
                f"- Completion worker: `{_value(completion, 'worker', _value(completion or {}, 'creator', 'n/a'))}`" if completion else "- Completion worker: n/a",
                f"- Completion rationale: {_value(completion, 'description', 'n/a')}" if completion else "- Completion rationale: n/a",
            ]
        )
    else:
        lines.append("- No completed path could be reconstructed from the recorded graph.")

    if hints:
        lines.extend(["", "## 3. Operator Hints", ""])
        for hint in hints:
            lines.extend(
                [
                    f"- `{_format_timestamp(_value(hint, 'created_at', None))}` by `{_value(hint, 'creator', 'unknown')}`",
                    _numbered_block(_value(hint, "content"), "  "),
                ]
            )

    lines.extend(["", "## 4. Reconstructed Penetration Path", ""])
    if not path:
        lines.append("No path available. The project must be completed with a goal intent to generate a path report.")
    else:
        for index, intent in enumerate(path, start=1):
            intent_id = _value(intent, "id")
            source_ids = list(sources_by_intent.get(intent_id, []))
            to_fact_id = _value(intent, "to_fact_id")
            produced_label = "goal" if to_fact_id == "goal" else to_fact_id
            lines.extend(
                [
                    f"### Step {index}: `{intent_id}` -> `{produced_label}`",
                    "",
                    f"- Created By: `{_value(intent, 'creator', 'unknown')}`",
                    f"- Worker: `{_value(intent, 'worker', 'n/a')}`",
                    f"- Created At: {_format_timestamp(_value(intent, 'created_at', None))}",
                    f"- Concluded At: {_format_timestamp(_value(intent, 'concluded_at', None))}",
                    f"- Source Facts: {', '.join(f'`{source_id}`' for source_id in source_ids) or 'n/a'}",
                    "",
                    "**Preconditions**",
                    "",
                ]
            )
            if source_ids:
                for source_id in source_ids:
                    lines.extend(
                        [
                            f"- `{source_id}`",
                            _numbered_block(_fact_description(facts_by_id, source_id)),
                        ]
                    )
            else:
                lines.append("- n/a")

            lines.extend(["", "**Action / Intent**", "", _bullet_text(_value(intent, "description"), ""), ""])

            if to_fact_id == "goal":
                produced_text = _value(intent, "description")
                lines.extend(["**Completion Proof**", "", _bullet_text(produced_text, ""), ""])
            else:
                produced_text = _fact_description(facts_by_id, to_fact_id)
                lines.extend(
                    [
                        "**Observed Result / New Fact**",
                        "",
                        f"- Fact ID: `{to_fact_id}`",
                        _numbered_block(produced_text),
                        "",
                    ]
                )

            lines.extend(
                [
                    "**PoC Reproduction Notes**",
                    "",
                    "1. Start from the listed preconditions in an authorized test environment.",
                    "2. Reproduce the recorded action exactly as described above; do not add unrecorded attack steps.",
                    "3. Validate success by checking that the observed result matches the produced fact or completion proof.",
                    "4. Capture request/response pairs, command output, screenshots, and timestamps as evidence.",
                    "",
                ]
            )

    lines.extend(["## 5. Evidence Index", ""])
    for fact in facts:
        fact_id = _value(fact, "id")
        lines.extend([f"### `{fact_id}`", "", _bullet_text(_value(fact, "description"), ""), ""])

    lines.extend(["## 6. Remediation Notes", ""])
    lines.extend(
        [
            "- Derive remediation from the verified vulnerable component in each path step.",
            "- Patch the affected service, rotate exposed credentials, and remove unauthorized persistence observed in the path.",
            "- Re-run the PoC steps in a controlled validation window to confirm the issue is fixed.",
            "",
            "## 7. Full Intent Ledger",
            "",
        ]
    )
    for intent in intents:
        intent_id = _value(intent, "id")
        lines.extend(
            [
                f"### `{intent_id}`",
                "",
                f"- From: {', '.join(f'`{source_id}`' for source_id in sources_by_intent.get(intent_id, [])) or 'n/a'}",
                f"- To: `{_value(intent, 'to_fact_id', 'open')}`",
                f"- Creator: `{_value(intent, 'creator', 'unknown')}`",
                f"- Worker: `{_value(intent, 'worker', 'n/a')}`",
                "",
                _bullet_text(_value(intent, "description"), ""),
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"
