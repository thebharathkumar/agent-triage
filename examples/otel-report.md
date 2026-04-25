# Triage Report

Generated: 2026-04-25 08:40 UTC

**2 run(s) analyzed. 2 incident pattern(s) detected. Top 2 worth your attention this morning.**

Sources: `runs/examples/otel.json`

---

## #1 - [planner] dispatch_subtask / coordination_failure / retriever_state

**Category:** Coordination Failure

| Metric | Value |
|--------|-------|
| Severity Score | 15.00 / 15.00 |
| Frequency | 6 event(s) across 2 run(s) |
| Appeared in | 2/2 runs |
| Trend | insufficient data |
| Recovery Rate | [----------] 0% |
| Recovery Latency | no recoveries observed |
| Tail Risk | 6 failures unrecovered after 10 turns |
| Confidence | high (6 occurrences) |
| Final Score | **13.00** |

**Why this matters:**

Agent planner hit a **Coordination Failure** on `dispatch_subtask` 6 time(s) across 2 runs. Appeared in 2/2 runs. None of these failures were followed by a successful action within 3 turns, meaning the agent got stuck rather than adapting. Belief divergence was present on field(s): retriever_state, meaning the agent was operating on stale world state when it failed. With a final score of 13.00, this pattern ranks high because its classification carries significant weight in the scoring model.

**Suggested next action:** Review inter-agent messaging cadence and add position-sync checkpoints before high-stakes joint actions.

---

## #2 - [retriever] fetch_documents / information_lag / index_version

**Category:** Information Lag

| Metric | Value |
|--------|-------|
| Severity Score | 5.00 / 15.00 |
| Frequency | 2 event(s) across 2 run(s) |
| Appeared in | 2/2 runs |
| Trend | insufficient data |
| Recovery Rate | [##########] 100% |
| Recovery Latency | median recovery latency: 2 turns |
| Tail Risk | no tail risk |
| Confidence | medium (2 occurrences) |
| Final Score | **4.33** |

**Why this matters:**

Agent retriever hit a **Information Lag** on `fetch_documents` 2 time(s) across 2 runs. Appeared in 2/2 runs. About 100% of occurrences were followed by a successful action within 3 turns (median latency 2 turns), suggesting partial self-correction. Belief divergence was present on field(s): index_version, meaning the agent was operating on stale world state when it failed. With a final score of 4.33, this pattern ranks high because its classification carries significant weight in the scoring model.

**Suggested next action:** Increase belief-state refresh frequency or add an explicit re-observe step after N turns without a sync message.

---
