from __future__ import annotations

from ghostroot.dispatcher.decision_guardrails import filter_reason_intents
from ghostroot.server.models import Fact

from conftest import make_project


def test_filters_repeated_ruled_out_reverse_shell_route() -> None:
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
                },
                {
                    "subject": "bind shell",
                    "predicate": "remains_viable",
                    "object": "attacker connects to target",
                    "polarity": "positive",
                },
            ],
        )
    )

    intents = [
        {
            "from": ["f004"],
            "description": "Try another reverse shell that connects back to the attacker listener.",
            "kind": "exploit",
        },
        {
            "from": ["f004"],
            "description": "Start a Python bind PTY shell on the target and connect from the attacker.",
            "kind": "exploit",
        },
    ]

    assert filter_reason_intents(project, intents) == [intents[1]]


def test_allows_known_hash_replacement_after_empty_password_su_fails() -> None:
    project = make_project()
    project.facts.append(
        Fact(
            id="f005",
            description="empty-password su escalation is blocked by PAM rejecting empty password",
            kind="exploration_result",
            outcome="blocked",
            goal_relevance="rules_out",
            next_policy="branch",
            atoms=[
                {
                    "subject": "empty-password su escalation",
                    "predicate": "is_blocked_by",
                    "object": "PAM rejecting empty password or shadow entry",
                    "polarity": "negative",
                }
            ],
        )
    )

    intents = [
        {
            "from": ["f005"],
            "description": "Retry su with an empty password through another PTY wrapper.",
            "kind": "exploit",
        },
        {
            "from": ["f005"],
            "description": "Generate a known SHA-512 hash, write it to /etc/passwd, and use the known password through a controlled PTY.",
            "kind": "exploit",
        },
    ]

    assert filter_reason_intents(project, intents) == [intents[1]]


def test_allows_compatible_gconv_after_precompiled_pwnkit_binary_fails() -> None:
    project = make_project()
    project.facts.append(
        Fact(
            id="f006",
            description="precompiled /tmp/pwnkit exploit binary fails because GLIBC 2.34 is required but target has GLIBC 2.27",
            kind="exploration_result",
            outcome="blocked",
            goal_relevance="advances",
            next_policy="branch",
            atoms=[
                {
                    "subject": "/tmp/pwnkit exploit binary",
                    "predicate": "fails_with",
                    "object": "GLIBC 2.34 required; target has GLIBC 2.27",
                    "polarity": "negative",
                },
                {
                    "subject": "pwnkit.so GCONV module",
                    "predicate": "is_compatible",
                    "object": "requires only GLIBC 2.2.5",
                    "polarity": "positive",
                },
            ],
        )
    )

    intents = [
        {
            "from": ["f006"],
            "description": "Upload and run the precompiled PwnKit exploit binary again.",
            "kind": "exploit",
        },
        {
            "from": ["f006"],
            "description": "Transfer the compatible pwnkit.so GCONV module and execute the GCONV_PATH method with Python.",
            "kind": "exploit",
        },
    ]

    assert filter_reason_intents(project, intents) == [intents[1]]


def test_blocked_fact_can_still_seed_different_positive_route() -> None:
    project = make_project()
    project.facts.append(
        Fact(
            id="f003",
            description="su and hash cracking blocked, but local privilege escalation evidence remains",
            kind="exploration_result",
            outcome="blocked",
            goal_relevance="advances",
            next_policy="branch",
            atoms=[
                {
                    "subject": "su pipe authentication",
                    "predicate": "fails_with",
                    "object": "must be run from a terminal",
                    "polarity": "negative",
                },
                {
                    "subject": "backdoor hash cracking",
                    "predicate": "failed_with",
                    "object": "rockyou and common passwords",
                    "polarity": "negative",
                },
                {
                    "subject": "local privilege escalation exploit",
                    "predicate": "compiled_locally",
                    "object": "ready for deployment through the webshell",
                    "polarity": "positive",
                },
            ],
        )
    )

    intents = [
        {
            "from": ["f003"],
            "description": "Retry su pipe authentication with the same password list.",
            "kind": "credential_test",
        },
        {
            "from": ["f003"],
            "description": "Deploy the local privilege escalation exploit through the webshell and collect proof non-interactively.",
            "kind": "exploit",
        },
    ]

    assert filter_reason_intents(project, intents) == [intents[1]]


def test_filters_generic_failed_route_without_vulnerability_specific_rule() -> None:
    project = make_project()
    project.facts.append(
        Fact(
            id="f010",
            description="directory brute force exhausted and rate limited",
            kind="exploration_result",
            outcome="blocked",
            goal_relevance="neutral",
            next_policy="branch",
            atoms=[
                {
                    "subject": "directory brute force",
                    "predicate": "failed_with",
                    "object": "rate limited after common wordlists produced no new paths",
                    "polarity": "negative",
                },
                {
                    "subject": "authenticated upload route",
                    "predicate": "remains_viable",
                    "object": "valid credentials and upload endpoint are confirmed",
                    "polarity": "positive",
                },
            ],
        )
    )

    intents = [
        {
            "from": ["f010"],
            "description": "Run another directory brute force with a larger wordlist.",
            "kind": "enumerate",
        },
        {
            "from": ["f010"],
            "description": "Use the authenticated upload route to validate server-side script execution.",
            "kind": "validate",
        },
    ]

    assert filter_reason_intents(project, intents) == [intents[1]]
