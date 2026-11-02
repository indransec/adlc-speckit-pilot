# CARD-102 · A detected document injection always sends the claim to a person

> Stand-in for a tracker work item (enrichment, not yet scheduled). Raised 2026-11-02 by the AI lead while
> labelling adversarial case ADV-005 for release 0.4.0 (CARD-101). Kept out of 0.4.0 because it changes
> routing / human oversight for every claim type, which CARD-101 excluded and G1 / G2 approved without.

| Field | Value |
|---|---|
| Work item type | Enhancement (control change to UC-TRV-CLM-001) |
| State | New |
| Requested by | Kenji Sato, AI lead |
| Target release | [to be planned, e.g. 0.5.0] |

## Problem

When a customer document contains a prompt-injection attempt, the document guardrail detects it, wraps the
text as data and logs the event, and the claim is routed normally (decision D-12, threat model T1). A claim
that is otherwise valid can therefore be paid straight through even though someone tried to manipulate the
agents. In 0.4.0 this is ADV-001 (baggage) and ADV-005 (missed connection; routed row 1 and paid).

## Proposed change

- New behavioural requirement: a claim whose documents contain a detected injection is never decided without
  a person; it goes to investigation (row 6) with the reason.
- Routing matrix and `agents/orchestrator/src/routing.py` (explicit condition) or a confidence cap; decided at
  G1 by the Business owner and AI lead.
- Threat model T1 control updated at G2; ADV-001 / ADV-005 expectations change to row 6.

## Notes

- Authority / HITL change: goes through G1 and G2 with their owners (constitution III).
- Until delivered, the residual risk is accepted at G4 of each release by RAI/Risk and Compliance.
