
---

## ADLC Task Rules

<!-- Added by the adlc preset. Apply to every task above. -->

- Work happens on `release/<version>` and merges by pull request with CODEOWNERS review.
- Each implementation and test task names the REQ IDs it serves, e.g. `T012 [US1] [REQ-F-021] ...`.
- Tasks that touch an authority file (`routing.py`, `evals/thresholds.yaml`, `system.yaml` routing
  keys, `tool-permission-matrix.yaml`) are marked `[AUTHORITY]` and must match what G1/G2 approved.
- Tasks only a person can do are marked `[HUMAN: <persona>]`; `/speckit-implement` stops there and
  asks. Gate decisions are never tasks.
- Nothing under `evidence/` is written by hand; evidence comes from the commands in the phase below.

## Phase ADLC: Evidence and Gate Readiness (G3, G4)

**Purpose**: produce the G3/G4 evidence for this release once the stories above are done.

- [ ] TA01 Propose golden cases for the new REQ IDs in `evals/datasets/golden/` (draft, unlabelled)
- [ ] TA02 [HUMAN: <persona>] Label the golden cases (ground truth)
- [ ] TA03 Propose adversarial cases (injection, fraud, edge cases) in `evals/datasets/adversarial/`
- [ ] TA04 [HUMAN: <persona>] Label the adversarial cases
- [ ] TA05 Link every new REQ ID to its tests/evals; `make traceability` shows no unverified requirement
- [ ] TA06 `make test` green (unit + routing tests)
- [ ] TA07 `make evals` meets every threshold in `evals/thresholds.yaml`
- [ ] TA08 `make perf` (3x peak)
- [ ] TA09 `uv run python scripts/adlc.py check G3`; write `briefs/G3.md`
- [ ] TA10 RAI: fairness, PII, injection and red-team suites pass; update `docs/rai/rai-card.md` and
  `docs/rai/transparency-notice.md`
- [ ] TA11 [HUMAN: Compliance] Approve new or changed customer letters
- [ ] TA12 `uv run python scripts/adlc.py check G4`; write `briefs/G4.md`

**Checkpoint**: G3 and G4 are signed by people (AI lead + Product owner; RAI/Risk + Compliance).
