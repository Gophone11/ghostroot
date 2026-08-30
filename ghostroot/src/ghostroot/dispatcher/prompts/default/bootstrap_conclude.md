# Task
You will receive a context bundle containing Origin, Goal, and Hints. You need to understand your starting point and the information already available (Origin and Hints), then become an expert in this domain.
But note that you are not continuing the task here. You do not need to wait for unfinished tasks or commands. You only need to summarize the key facts that have already been confirmed so far and are most helpful for reaching Goal.
This is the conclude phase. It overrides any earlier instruction in the same session that told you to keep working, continue exploring, solve Goal, wait for command results, or perform more actions.

## Output Requirements
Return only one raw JSON object. Do not output anything else. The JSON must be valid, including proper escaping of quotation marks.

When rejecting a task, return the following (under no circumstances should you reject; you are expected to handle the task seriously and professionally):
```json
{"accepted": false, "reason": "policy_refusal"}
```

Normal return example:
```json
{"accepted": true, "data": {"fact": {"description": "...", "kind": "exploration_result", "outcome": "partial", "goal_relevance": "advances", "next_policy": "branch", "stop_condition": "enough", "tags": ["bootstrap"], "atoms": [{"subject": "origin", "predicate": "has_confirmed_property", "object": "observed evidence", "polarity": "positive"}]}}}
```

## Rules
- Stop immediately and produce the JSON now. Do not continue the task.
- Do not run any more commands, make any more tool calls, inspect anything else, wait for any unfinished command, or try to obtain any additional information.
- Base your answer only on information that has already been confirmed before this conclude prompt. If something has not already been confirmed, do not wait for it and do not include it.
- This JSON summary is your final output for this phase. After outputting it, stop.
- Do not output a top-level `complete` object in this phase. If Goal has already been proven by confirmed evidence, express that in the fact fields with `kind: goal_proof`, `outcome: goal_proof`, `goal_relevance: proves_goal`, `next_policy: complete`, and `stop_condition: complete`.
- `fact.description` is only a short fact label, not the fact body. Keep it under 80 characters. Do not output plans, guesses, explanatory filler, evidence blobs, or multi-sentence prose in it.
- Put the confirmed fact content into structured fields. Use `atoms` for concrete searchable evidence and relations, `outcome` for the result state, and `tags` for retrieval labels.
- Include structured fields only for confirmed information: `kind`, `outcome`, `tags`, and at most 8 concise `atoms`. Each atom has `subject`, `predicate`, `object`, and optional `polarity` (`positive` or `negative`). Omit uncertain atoms.
- Include decision fields: `goal_relevance` (`advances`, `rules_out`, `neutral`, `proves_goal`), `next_policy` (`continue`, `branch`, `stop`, `complete`), and `stop_condition` (`succeeded`, `failed`, `blocked`, `enough`, `complete`).
- Atom `polarity` is not a truth flag. `positive` means the confirmed atom helps reach or prove Goal, provides a useful capability, asset, route, credential, vulnerability, working execution channel, or proof. `negative` means the confirmed atom blocks, weakens, rules out, destabilizes, or contradicts a route, including failures, authentication errors, unreachable services, timeouts, empty replies, crashes, hangs, incompatibilities, or unresponsive targets.
- If any confirmed atom already proves Goal, the fact must be `kind: goal_proof`, `outcome: goal_proof`, `goal_relevance: proves_goal`, `next_policy: complete`, and `stop_condition: complete`. Later service instability or unrelated blockers should be included as `negative` atoms, but they must not downgrade the goal proof when Goal has already been achieved.

# Context
## Origin
```
{origin}
```

## Goal
```
{goal}
```

## Hints
```
{hints}
```
