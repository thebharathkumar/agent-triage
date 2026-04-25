# Triage Comparison Report

Generated: 2026-04-25 08:38 UTC

**Before:** `runs/examples/before.ndjson` (3 run(s), 30 event(s))
**After:** `runs/examples/monday.ndjson` (3 run(s), 28 event(s))

---

## Score Summary

| Metric | Before | After | Δ |
|---|---|---|---|
| Distinct incident patterns | 3 | 3 | stable |
| Failure events | 27 | 19 | down 30% |
| Unrecovered events | 27 | 13 | down 52% |
| Coordination failure events | 18 | 11 | down 39% |
| Top final score | 13.00 | 13.00 | stable |
| Mean final score | 7.10 | 8.40 | +1.30 |

---

## Headline

- Agent Error: new (tentative) (0 → 2)
- Agent Error unrecovered: new (tentative) (0 → 2)
- Coordination Failure: down 39% (18 → 11)
- Coordination Failure unrecovered: down 39% (18 → 11)
- Environment Constraint: resolved (tentative) (3 → 0)
- Environment Constraint unrecovered: resolved (tentative) (3 → 0)
- new pattern: [executor] call_external_api / agent_error / no-divergence
- resolved: [navigator] move / environment_constraint / no-divergence

---

## Classification deltas

| Classification | Before | After | Δ frequency | Unrecovered before | Unrecovered after | Δ unrecovered | Δ recovery latency |
|---|---|---|---|---|---|---|---|
| Agent Error | 0 | 2 | new (tentative) | 0 | 2 | new (tentative) | n/a |
| Coordination Failure | 18 | 11 | down 39% | 18 | 11 | down 39% | n/a |
| Environment Constraint | 3 | 0 | resolved (tentative) | 3 | 0 | resolved (tentative) | n/a |
| Information Lag | 6 | 6 | stable | 6 | 0 | resolved | new (2 turns) |

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
