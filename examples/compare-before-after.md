# Triage Comparison Report

Generated: 2026-04-25 03:53 UTC

**Before:** `runs/examples/before.ndjson` (3 run(s), 30 event(s))
**After:** `runs/examples/monday.ndjson` (3 run(s), 28 event(s))

---

## Headline

- Agent Error: new (0 → 2)
- Agent Error unrecovered: new (0 → 2)
- Coordination Failure: down 39% (18 → 11)
- Coordination Failure unrecovered: down 39% (18 → 11)
- Environment Constraint: resolved (3 → 0)
- Environment Constraint unrecovered: resolved (3 → 0)
- new pattern: [executor] call_external_api / agent_error / no-divergence
- resolved: [navigator] move / environment_constraint / no-divergence

---

## Classification deltas

| Classification | Before | After | Δ frequency | Unrecovered before | Unrecovered after | Δ unrecovered |
|---|---|---|---|---|---|---|
| Agent Error | 0 | 2 | new | 0 | 2 | new |
| Coordination Failure | 18 | 11 | down 39% | 18 | 11 | down 39% |
| Environment Constraint | 3 | 0 | resolved | 3 | 0 | resolved |
| Information Lag | 6 | 6 | stable | 6 | 0 | resolved |

---

## New patterns (in `after` only)

- [executor] call_external_api / agent_error / no-divergence — 2 occurrences

---

## Resolved patterns (in `before` only)

- [navigator] move / environment_constraint / no-divergence — was 3 occurrences

---

## Persisting patterns

| Pattern | Before | After | Δ frequency |
|---|---|---|---|
| [planner] dispatch_subtask / coordination_failure / retriever_state+task_queue_head | 18 | 11 | down 39% |
| [retriever] fetch_documents / information_lag / index_version | 6 | 6 | stable |
