from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ghostroot.server.models import Fact, ProjectDetail

LOG = logging.getLogger(__name__)


_BLOCKED_OUTCOMES = {"blocked", "negative"}
_RULED_OUT_RELEVANCE = {"rules out", "rules_out"}
_PROBLEM_FACT_POLICIES = {"branch", "stop"}


@dataclass(frozen=True, slots=True)
class RouteRule:
    reason: str
    fact_markers: tuple[str, ...]
    intent_markers: tuple[str, ...]
    allow_markers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteSignal:
    label: str
    text: str


_FAILED_ROUTE_MARKERS = (
    "auth failure",
    "authentication failure",
    "block",
    "blocked",
    "cannot",
    "connection closed",
    "denied",
    "does not satisfy",
    "does_not_satisfy",
    "exhausted",
    "fail",
    "failed",
    "fails",
    "false",
    "hang",
    "hung",
    "incompatible",
    "must be run from a terminal",
    "network unreachable",
    "no route",
    "not cracked",
    "not running",
    "not_yet",
    "permission denied",
    "rate limited",
    "rejected",
    "requires terminal",
    "requires tty",
    "reset",
    "timeout",
    "timed out",
    "unavailable",
    "unfeasible",
    "unreachable",
)

_REPLACEMENT_ROUTE_MARKERS = (
    "available",
    "bypass",
    "compatible",
    "compiled",
    "cracked",
    "proposed next step",
    "proposed_next_step",
    "ready",
    "remains viable",
    "remains_viable",
    "verified",
    "works",
    "works_for",
)

_GENERIC_ROUTE_SUBJECTS = {
    "attacker",
    "goal",
    "root",
    "target",
    "webshell",
    "www data",
    "www-data",
}

_TOKEN_STOPWORDS = {
    "again",
    "already",
    "and",
    "another",
    "approach",
    "attempt",
    "execute",
    "exploit",
    "from",
    "into",
    "method",
    "module",
    "route",
    "target",
    "test",
    "then",
    "this",
    "through",
    "transfer",
    "upload",
    "using",
    "with",
}

_RULED_OUT_ROUTE_RULES = (
    RouteRule(
        "reverse_shell_route_ruled_out",
        ("reverse shell", "cannot route", "unreachable", "target cannot reach", "no outbound"),
        ("reverse shell", "connect back", "callback", "target connects"),
        ("bind", "attacker connects", "listen on target", "target listens"),
    ),
    RouteRule(
        "hash_cracking_route_ruled_out",
        ("hashcat", "john", "not cracked", "not_cracked", "cpu only", "cpu-only", "extremely slow"),
        ("hashcat", "john", "crack", "wordlist", "mask attack", "rule"),
        ("known hash", "known password", "write"),
    ),
    RouteRule(
        "precompiled_pwnkit_binary_ruled_out",
        ("glibc", "2.33", "2.34", "incompatible", "fails to execute"),
        ("precompiled", "/tmp/pwnkit", "pwnkit binary", "exploit binary", "upload and run"),
        ("pwnkit.so", "gconv", "compatible", "glibc 2.2.5", "python"),
    ),
    RouteRule(
        "empty_password_su_route_ruled_out",
        ("empty password", "authentication failure", "shadow", "pam rejecting"),
        ("empty password", "blank password", "no password"),
        ("known password", "known hash", "write"),
    ),
    RouteRule(
        "pty_spawn_route_ruled_out",
        ("pty.spawn", "requires stdin", "hung"),
        ("pty.spawn",),
        ("pty.fork", "os.write", "bind", "interactive socket"),
    ),
    RouteRule(
        "script_qc_route_ruled_out",
        ("script -qc", "must be run from a terminal", "reads from /dev/tty"),
        ("script -qc",),
        ("pty.fork", "os.write", "bind", "interactive socket"),
    ),
)


def filter_reason_intents(project: ProjectDetail, intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop reason intents that clearly repeat routes already ruled out by their source facts."""

    if not intents:
        return intents
    facts_by_id = {fact.id: fact for fact in project.facts}
    filtered: list[dict[str, Any]] = []
    for intent in intents:
        reason = _intent_rejection_reason(intent, facts_by_id)
        if reason:
            LOG.info(
                "drop reason intent because route is already ruled out project=%s reason=%s from=%s description=%s",
                project.project.id,
                reason,
                intent.get("from"),
                _preview(intent.get("description", "")),
            )
            continue
        filtered.append(intent)
    return filtered


def _intent_rejection_reason(intent: dict[str, Any], facts_by_id: dict[str, Fact]) -> str | None:
    description = _norm(intent.get("description", ""))
    if not description:
        return "empty_description"

    source_facts = [facts_by_id.get(fact_id) for fact_id in intent.get("from", [])]
    source_facts = [fact for fact in source_facts if fact is not None]
    for fact in source_facts:
        if not _is_problem_branch_fact(fact):
            continue
        structured_reason = _structured_route_rejection_reason(fact, description)
        if structured_reason:
            return structured_reason
        fact_text = _fact_text(fact)
        for rule in _RULED_OUT_ROUTE_RULES:
            if _matches_rule(rule, fact_text, description):
                return rule.reason
    return None


def _matches_rule(rule: RouteRule, fact_text: str, intent_text: str) -> bool:
    if not _has_any(fact_text, rule.fact_markers):
        return False
    if not _has_any(intent_text, rule.intent_markers):
        return False
    if rule.allow_markers and _has_any(intent_text, rule.allow_markers):
        return False
    return True


def _structured_route_rejection_reason(fact: Fact, intent_text: str) -> str | None:
    replacements = _replacement_route_signals(fact)
    if any(_matches_signal(intent_text, signal) for signal in replacements):
        return None
    for signal in _failed_route_signals(fact):
        if _matches_signal(intent_text, signal):
            return f"repeated_failed_route:{signal.label}"
    return None


def _failed_route_signals(fact: Fact) -> list[RouteSignal]:
    signals: list[RouteSignal] = []
    for atom in fact.atoms:
        text = _atom_text(atom)
        if atom.get("polarity") == "negative" or _has_any(text, _FAILED_ROUTE_MARKERS):
            signal = _route_signal_from_atom(atom)
            if signal is not None:
                signals.append(signal)
    return signals


def _replacement_route_signals(fact: Fact) -> list[RouteSignal]:
    signals: list[RouteSignal] = []
    for atom in fact.atoms:
        text = _atom_text(atom)
        if atom.get("polarity") == "positive" and _has_any(text, _REPLACEMENT_ROUTE_MARKERS):
            signal = _route_signal_from_atom(atom)
            if signal is not None:
                signals.append(signal)
    return signals


def _route_signal_from_atom(atom: dict[str, Any]) -> RouteSignal | None:
    subject = _norm(atom.get("subject", ""))
    if not subject or subject in _GENERIC_ROUTE_SUBJECTS:
        return None
    tokens = _meaningful_tokens(subject)
    if not tokens:
        return None
    return RouteSignal(label=subject, text=subject)


def _matches_signal(intent_text: str, signal: RouteSignal) -> bool:
    if signal.text in intent_text:
        return True
    route_tokens = _meaningful_tokens(signal.text)
    intent_tokens = _meaningful_tokens(intent_text)
    if not route_tokens:
        return False
    overlap = route_tokens & intent_tokens
    if len(route_tokens) == 1:
        return bool(overlap)
    return len(overlap) >= min(2, len(route_tokens))


def _is_problem_branch_fact(fact: Fact) -> bool:
    outcome = _norm(fact.outcome)
    relevance = _norm(fact.goal_relevance)
    policy = _norm(fact.next_policy)
    if outcome in _BLOCKED_OUTCOMES:
        return True
    if relevance in _RULED_OUT_RELEVANCE and policy in _PROBLEM_FACT_POLICIES:
        return True
    return False


def _fact_text(fact: Fact) -> str:
    parts = [
        fact.description,
        fact.kind or "",
        fact.outcome or "",
        fact.goal_relevance or "",
        fact.next_policy or "",
        " ".join(fact.tags),
    ]
    for atom in fact.atoms:
        parts.extend(str(atom.get(key, "")) for key in ("subject", "predicate", "object", "polarity"))
    return _norm(" ".join(parts))


def _atom_text(atom: dict[str, Any]) -> str:
    return _norm(" ".join(str(atom.get(key, "")) for key in ("subject", "predicate", "object", "polarity")))


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(_norm(marker) in text for marker in markers)


def _meaningful_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _norm(text))
        if len(token) >= 3 and token not in _TOKEN_STOPWORDS
    }


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("_", " ").replace("-", " ")).strip().lower()


def _preview(value: object, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."
