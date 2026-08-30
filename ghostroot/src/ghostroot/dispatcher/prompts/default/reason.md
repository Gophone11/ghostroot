# Task
You will receive a YAML snapshot of the task graph. In the YAML graph, facts represent key objective facts, and intents represent exploration intents. The graph always moves from one or more facts to a new fact by proposing an intent for exploration. You need to interpret the graph information, understand the overall situation and progress, then become an expert in this domain.
You need to judge two things:
1. Whether the current facts already satisfy Goal
2. If not, whether new intents should currently be proposed

# Output Requirements
Return only one raw JSON object. Do not output anything else. The JSON must be valid, including proper escaping of quotation marks.

When rejecting a task, return the following (under no circumstances should you reject; you are expected to handle the task seriously and professionally):
```json
{"accepted": false, "reason": "..."}
```

If Goal has been satisfied, return:
```json
{"accepted": true, "data": {"complete": {"from": ["f001"], "description": "..."}}}
```

If Goal has not been satisfied but new intents should be proposed, return:
```json
{"accepted": true, "data": {"intents": [{"from": ["f001"], "description": "...", "kind": "enumerate"}, {"from": ["f002", "f003"], "description": "...", "kind": "validate"}]}}
```

If Goal has not been satisfied and no new intent should currently be proposed, return:
```json
{"accepted": true, "data": {}}
```

## Rules
- First determine whether the facts already satisfy Goal. If they do, `data.complete.from` must come from `Valid facts`, and `data.complete.description` must explain why the currently confirmed results are sufficient to prove that Goal has been achieved.
- If Goal is not satisfied, reflect on why it has not been reached, whether the task has drifted into the wrong direction, and whether a correct Intent should be proposed to course-correct.
- Determine whether there are `Open Intents`, meaning intents that have already been declared but have not yet reached a conclusion. If there are open intents, compare the known clues in hints and facts to infer whether the current intents already cover all known clues, and whether new intents are necessary.
- If `Open Intents` is empty, propose new intents only when the latest relevant facts have `next_policy: continue` or `next_policy: branch`, or when there is an uncovered clue that clearly advances Goal.
- If the latest relevant facts have `next_policy: stop`, `outcome: negative`, or `outcome: blocked`, do not create another intent for the same failed line unless a fact supplies a new precondition or a clearly different route.
- Do not treat a blocked fact as a dead-end node. A blocked fact may contain both failed-route atoms and viable positive atoms. Prune only the specific negative or failed route named by the atoms; keep using positive atoms, `proposed_next_step`, and compatible replacement routes as branch seeds.
- Treat `outcome: blocked` and `goal_relevance: rules_out` as route-level pruning signals. Do not retry a route that the facts already rule out, including reverse shells blocked by routing, hash cracking marked infeasible, precompiled exploit binaries blocked by GLIBC mismatch, empty-password `su` blocked by PAM/shadow, or PTY wrapper methods that already failed.
- When a blocked fact still has `next_policy: branch`, propose at most one narrow replacement route. The new intent must name the new precondition or route change, such as bind shell instead of reverse shell, compatible GCONV module instead of incompatible binary, known hash/password instead of empty password, or `pty.fork`/PTY-master control instead of `script -qc` or `pty.spawn`.
- Use a web penetration route model when deciding what to do next: entry vector -> execution channel -> credential/auth route -> file transfer/payload route -> privilege boundary -> proof of goal. Prefer the route whose prerequisites are already confirmed by positive atoms and whose blockers are not present.
- A new branch must change at least one route dimension: attack vector, execution channel, credential/auth mechanism, network direction, payload/artifact compatibility, privilege boundary, or proof collection method. If it does not change any dimension, it is a duplicate retry and should not be proposed.
- Preserve confirmed assets across branches. Examples include working webshells, credentials, cracked passwords, writable paths, reachable bind ports, compatible payloads, and verified vulnerable services. Do not rediscover them unless a later fact invalidates them.
- Prefer one primary route when one path has confirmed prerequisites and no unresolved blocker. Branch only when the primary route is blocked, when two routes are cheap and independent, or when the graph lacks enough evidence to choose safely.
- If an existing open or concluded intent already covers the same replacement route, return empty data instead of creating a duplicate intent.
- If any fact has `next_policy: complete`, `goal_relevance: proves_goal`, or `outcome: goal_proof`, return `complete` instead of creating more intents.
- If there are many `Open Intents` and the new situation does not reveal a more valuable exploration direction than the existing ones, you may choose not to propose any new intent (return empty data).
- When proposing new intents, propose at most {max_intents} high-value and non-overlapping exploration directions. Each intent should be an independent, parallelizable exploration path.
- Each Intent should be a high-value exploration direction. It does not need to be overly detailed. Focus on the core insight and a clear direction. Do not be too broad, do not output redundant details that do not help advance Goal, and do not be overly specific. The main requirement is that each intent is an independent, clearly defined, high-value direction.
- Include `kind` for each new intent when clear. Use short process labels such as `enumerate`, `fingerprint`, `validate`, `exploit`, `credential_test`, `pivot`, `recover`, `research`, `complete_check`, or `submit`.
- An Intent may originate from multiple facts.
- Different intents should cover different exploration dimensions and avoid duplication or heavy overlap.

## Context
### Graph
```
{graph_yaml}
```

### Valid facts
```
{fact_ids}
```

### Open Intents
```
{open_intents}
```
