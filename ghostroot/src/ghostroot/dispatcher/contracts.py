from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import re

from ghostroot.dispatcher.output_parser import extract_json_object


@dataclass(frozen=True, slots=True)
class FactPayload:
    description: str
    kind: str | None = None
    outcome: str | None = None
    goal_relevance: str | None = None
    next_policy: str | None = None
    stop_condition: str | None = None
    tags: list[str] = field(default_factory=list)
    atoms: list[dict[str, Any]] = field(default_factory=list)

    def client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.kind is not None:
            kwargs["kind"] = self.kind
        if self.outcome is not None:
            kwargs["outcome"] = self.outcome
        if self.goal_relevance is not None:
            kwargs["goal_relevance"] = self.goal_relevance
        if self.next_policy is not None:
            kwargs["next_policy"] = self.next_policy
        if self.stop_condition is not None:
            kwargs["stop_condition"] = self.stop_condition
        if self.tags:
            kwargs["tags"] = self.tags
        if self.atoms:
            kwargs["atoms"] = self.atoms
        return kwargs


def parse_json_output(stdout: str) -> dict[str, Any]:
    return extract_json_object(stdout)


def _unwrap_wrapped_payload(payload: dict[str, Any]) -> tuple[bool | None, dict[str, Any] | None]:
    accepted = payload.get("accepted")
    if accepted is False:
        return False, None
    if accepted is True:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("data must be an object")
        return True, data
    return None, None


def _is_dict(value: Any) -> bool:
    return isinstance(value, dict)


def _looks_like_reason_data(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    keys = set(payload)
    if keys == {"complete"}:
        complete = payload["complete"]
        return isinstance(complete, dict) and "from" in complete and "description" in complete
    if keys == {"intents"}:
        return isinstance(payload["intents"], list)
    if keys == {"intent"}:
        intent = payload["intent"]
        return isinstance(intent, dict) and "from" in intent and "description" in intent
    return False


def _looks_like_bootstrap_execute_data(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict) or set(payload) != {"fact", "complete"}:
        return False
    return _is_dict(payload.get("fact")) and _is_dict(payload.get("complete"))


def _looks_like_bootstrap_conclude_data(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    keys = set(payload)
    if keys not in ({"fact"}, {"fact", "complete"}):
        return False
    return _is_dict(payload.get("fact"))


def _looks_like_explore_data(payload: dict[str, Any]) -> bool:
    return isinstance(payload, dict) and "description" in payload


def _optional_string(value: Any, scope: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{scope} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{scope} must not be empty")
    return text


def _normalize_atoms(value: Any, scope: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{scope} must be an array")
    atoms: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{scope}[{index}] must be an object")
        atom_scope = f"{scope}[{index}]"
        atom = {
            "subject": _required_string(item, "subject", atom_scope),
            "predicate": _required_string(item, "predicate", atom_scope),
            "object": _required_string(item, "object", atom_scope),
        }
        polarity = item.get("polarity")
        if polarity is not None:
            if polarity not in ("positive", "negative"):
                raise ValueError(f"{atom_scope}.polarity must be positive or negative")
            atom["polarity"] = polarity
        atoms.append(atom)
    return atoms


def _normalize_fact_payload(data: dict[str, Any], scope: str = "data") -> FactPayload:
    raw_description = _required_string(data, "description", scope)
    raw_tags = data.get("tags")
    tags = [] if raw_tags is None else _string_list(raw_tags, f"{scope}.tags")
    kind = _optional_string(data.get("kind"), f"{scope}.kind")
    outcome = _optional_string(data.get("outcome"), f"{scope}.outcome")
    goal_relevance = _optional_string(data.get("goal_relevance"), f"{scope}.goal_relevance")
    next_policy = _optional_string(data.get("next_policy"), f"{scope}.next_policy")
    stop_condition = _optional_string(data.get("stop_condition"), f"{scope}.stop_condition")
    atoms = _normalize_atoms(data.get("atoms", []), f"{scope}.atoms")
    return FactPayload(
        description=_structured_fact_description(
            raw_description,
            kind=kind,
            outcome=outcome,
            tags=tags,
            atoms=atoms,
            goal_relevance=goal_relevance,
            next_policy=next_policy,
        ),
        kind=kind,
        outcome=outcome,
        goal_relevance=goal_relevance,
        next_policy=next_policy,
        stop_condition=stop_condition,
        tags=tags,
        atoms=atoms,
    )


def _structured_fact_description(
    fallback: str,
    *,
    kind: str | None,
    outcome: str | None,
    tags: list[str],
    atoms: list[dict[str, Any]],
    goal_relevance: str | None = None,
    next_policy: str | None = None,
) -> str:
    if not (kind or outcome or tags or atoms):
        return fallback
    parts: list[str] = []
    if kind:
        parts.append(kind)
    if outcome:
        parts.append(outcome)
    if goal_relevance:
        parts.append(goal_relevance)
    if next_policy:
        parts.append(next_policy)
    if atoms:
        atom = atoms[0]
        parts.append(f"{atom['subject']} {atom['predicate']} {atom['object']}")
    elif tags:
        parts.append(",".join(tags[:3]))
    text = " | ".join(parts).strip()
    if not text:
        text = fallback
    chars = list(text)
    return text if len(chars) <= 120 else "".join(chars[:117]) + "..."


def _normalize_intent_payload(intent: dict[str, Any], scope: str) -> dict[str, Any]:
    normalized = {
        "from": _string_list(intent.get("from"), f"{scope}.from"),
        "description": _required_string(intent, "description", scope),
    }
    intent_kind = _optional_string(intent.get("kind"), f"{scope}.kind")
    if intent_kind is not None:
        normalized["kind"] = intent_kind
    return normalized


def validate_reason_payload(
    payload: dict[str, Any], open_intents_empty: bool, max_intents: int,
) -> tuple[str, dict[str, Any] | list[dict[str, Any]] | None]:
    accepted, data = _unwrap_wrapped_payload(payload)
    if accepted is False:
        return "rejected", None
    if accepted is None:
        if not _looks_like_reason_data(payload):
            raise ValueError("accepted must be true or false")
        data = payload
    if not isinstance(data, dict):
        raise ValueError("accepted must be true or false")
    complete = data.get("complete")
    intents = data.get("intents")
    # backward compat: accept singular "intent" key from LLMs
    if intents is None:
        singular = data.get("intent")
        if isinstance(singular, dict):
            intents = [singular]
    if complete is not None:
        if intents is not None:
            raise ValueError("complete and intents cannot coexist")
        if not isinstance(complete, dict) or "from" not in complete or "description" not in complete:
            raise ValueError("invalid complete payload")
        return "complete", _normalize_intent_payload(complete, "complete")
    if intents is not None:
        if not isinstance(intents, list):
            raise ValueError("intents must be an array")
        for i, intent in enumerate(intents):
            if not isinstance(intent, dict) or "from" not in intent or "description" not in intent:
                raise ValueError(f"invalid intent at index {i}")
        if not intents and open_intents_empty:
            raise ValueError("intents must not be empty when open_intents is empty")
        intents = [_normalize_intent_payload(intent, f"intents[{i}]") for i, intent in enumerate(intents[:max_intents])]
        if not intents:
            return "noop", None
        return "intents", intents
    if open_intents_empty:
        raise ValueError("intents is required when open_intents is empty")
    return "noop", None


def validate_bootstrap_execute_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    accepted, data = _unwrap_wrapped_payload(payload)
    if accepted is False:
        return "rejected", None
    if accepted is None:
        if not _looks_like_bootstrap_execute_data(payload):
            raise ValueError("accepted must be true or false")
        data = payload
    if not isinstance(data, dict):
        raise ValueError("accepted must be true or false")

    fact = data.get("fact")
    if not isinstance(fact, dict):
        raise ValueError("fact is required")
    fact_payload = _normalize_fact_payload(fact, "fact")

    result: dict[str, Any] = {
        "fact": fact_payload,
        "fact_description": fact_payload.description,
    }
    complete = data.get("complete")
    if complete is None:
        raise ValueError("complete is required")
    if not isinstance(complete, dict):
        raise ValueError("complete must be an object")
    complete_description = complete.get("description")
    if not isinstance(complete_description, str) or not complete_description.strip():
        raise ValueError("complete.description is required")
    result["complete_description"] = complete_description.strip()
    return "complete", result


def validate_bootstrap_conclude_payload(payload: dict[str, Any]) -> tuple[str, FactPayload | None]:
    accepted, data = _unwrap_wrapped_payload(payload)
    if accepted is False:
        return "rejected", None
    if accepted is None:
        if not _looks_like_bootstrap_conclude_data(payload):
            raise ValueError("accepted must be true or false")
        data = payload
    if not isinstance(data, dict):
        raise ValueError("accepted must be true or false")
    extra_keys = set(data) - {"fact", "complete"}
    if extra_keys:
        raise ValueError("unexpected keys in conclude payload")
    fact = data.get("fact")
    if not isinstance(fact, dict):
        raise ValueError("fact is required")
    return "fact", _normalize_fact_payload(fact, "fact")


def validate_explore_payload(payload: dict[str, Any]) -> tuple[str, FactPayload | None]:
    accepted, data = _unwrap_wrapped_payload(payload)
    if accepted is False:
        return "rejected", None
    if accepted is None:
        if not _looks_like_explore_data(payload):
            raise ValueError("accepted must be true or false")
        data = payload
    if not isinstance(data, dict):
        raise ValueError("accepted must be true or false")
    return "fact", _normalize_fact_payload(data)


BACK_REFERENCE_PATTERNS = (
    re.compile(r"\bsee\s+[fi]\d{3}\b", re.IGNORECASE),
    re.compile(r"\brefer\s+to\s+[fi]\d{3}\b", re.IGNORECASE),
    re.compile(r"见\s*[fi]\d{3}", re.IGNORECASE),
    re.compile(r"参考\s*[fi]\d{3}", re.IGNORECASE),
)


def validate_report_payload(
    payload: dict[str, Any],
    *,
    fact_ids: set[str],
    intent_ids: set[str],
) -> tuple[str, dict[str, Any] | None]:
    accepted, data = _unwrap_wrapped_payload(payload)
    if accepted is False:
        return "rejected", None
    if accepted is None:
        raise ValueError("accepted must be true or false")
    if not isinstance(data, dict):
        raise ValueError("data must be an object")

    summary = data.get("attack_path_summary")
    if not isinstance(summary, list) or not summary:
        raise ValueError("attack_path_summary must be a non-empty array")
    normalized_summary: list[dict[str, Any]] = []
    for index, item in enumerate(summary):
        if not isinstance(item, dict):
            raise ValueError(f"attack_path_summary[{index}] must be an object")
        title = _required_string(item, "title", f"attack_path_summary[{index}]")
        why = _required_string(item, "why_it_matters", f"attack_path_summary[{index}]")
        source_facts = _string_list(item.get("source_facts"), f"attack_path_summary[{index}].source_facts")
        ids = _string_list(item.get("intent_ids"), f"attack_path_summary[{index}].intent_ids")
        result_fact = item.get("result_fact")
        if result_fact is not None and not isinstance(result_fact, str):
            raise ValueError(f"attack_path_summary[{index}].result_fact must be a string")
        for fact_id in source_facts:
            if fact_id not in fact_ids:
                raise ValueError(f"unknown source fact id: {fact_id}")
        if result_fact and result_fact not in fact_ids and result_fact != "goal":
            raise ValueError(f"unknown result fact id: {result_fact}")
        for intent_id in ids:
            if intent_id not in intent_ids:
                raise ValueError(f"unknown intent id: {intent_id}")
        normalized_summary.append(
            {
                "title": title,
                "source_facts": source_facts,
                "intent_ids": ids,
                "result_fact": result_fact,
                "why_it_matters": why,
            }
        )

    markdown = _required_string(data, "poc_markdown", "data")
    if any(pattern.search(markdown) for pattern in BACK_REFERENCE_PATTERNS):
        raise ValueError("poc_markdown must expand recorded details inline instead of asking users to look up fact/intent IDs")
    confidence = _required_string(data, "confidence", "data").lower()
    if confidence not in {"high", "medium", "low"}:
        raise ValueError("confidence must be high, medium, or low")
    gaps = _string_list(data.get("gaps", []), "gaps")

    return "report", {
        "attack_path_summary": normalized_summary,
        "markdown": markdown,
        "confidence": confidence,
        "gaps": gaps,
    }


def _required_string(data: dict[str, Any], key: str, scope: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{scope}.{key} is required")
    return value.strip()


def _string_list(value: Any, scope: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{scope} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{scope}[{index}] must be a non-empty string")
        result.append(item.strip())
    return result
