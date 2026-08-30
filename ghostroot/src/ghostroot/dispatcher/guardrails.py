from __future__ import annotations

from dataclasses import dataclass
import re

from ghostroot.server.models import Fact, ProjectDetail


TERMINAL_OUTCOMES = {"negative", "blocked"}
TERMINAL_RELEVANCE = {"rules_out", "rules out"}

FAILURE_MARKERS = (
    "application-layer data",
    "before key exchange",
    "connection closed before",
    "curl 52",
    "destabilized",
    "dirty cow",
    "dirtycow",
    "empty byte string",
    "empty http reply",
    "empty reply",
    "hung",
    "kernel crash",
    "kex_exchange_identification",
    "no banner",
    "no data",
    "no response",
    "post-dirtycow",
    "protocol handshake fails",
    "recursive fault",
    "reboot",
    "reset vm",
    "service broken",
    "service destabilization",
    "service hung",
    "services broken",
    "services hung",
    "systemic broken",
    "unreachable",
    "unresponsive",
    "vm crashed",
    "webshell unreachable",
)

SERVICE_MARKERS = (
    "daemon",
    "http",
    "lighttpd",
    "port",
    "service",
    "shell.php",
    "squid",
    "ssh",
    "target",
    "tcp",
    "vm",
    "web",
    "webdav",
    "webshell",
)


@dataclass(frozen=True, slots=True)
class TerminalFailure:
    fact_id: str
    reason: str


def terminal_failure(project: ProjectDetail) -> TerminalFailure | None:
    for fact in _ordered_non_special_facts(project):
        reason = terminal_failure_reason(fact)
        if reason:
            return TerminalFailure(fact.id, reason)
    return None


def terminal_failure_reason(fact: Fact) -> str | None:
    next_policy = _norm(fact.next_policy)
    if next_policy != "stop":
        return None

    outcome = _norm(fact.outcome)
    relevance = _norm(fact.goal_relevance)
    if outcome not in TERMINAL_OUTCOMES and relevance not in TERMINAL_RELEVANCE:
        return None

    text = _fact_text(fact)
    if _has_any(text, FAILURE_MARKERS) and _has_any(text, SERVICE_MARKERS):
        return "target_or_core_service_unavailable"
    return None


def _ordered_non_special_facts(project: ProjectDetail) -> list[Fact]:
    facts = [fact for fact in project.facts if fact.id not in ("origin", "goal")]
    return sorted(facts, key=lambda fact: fact.id, reverse=True)


def _fact_text(fact: Fact) -> str:
    parts = [
        fact.id,
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


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers) or bool(re.search(r"\b(?:curl|ssh)\s+(?:exit\s+)?52\b", text))


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("_", " ").replace("-", " ")).strip().lower()
