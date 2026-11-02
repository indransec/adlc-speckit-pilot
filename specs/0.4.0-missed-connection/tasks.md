# Tasks: Missed Connection Benefit

**Input**: Design documents from `/specs/0.4.0-missed-connection/` (G2 approved 2026-11-02 15:59 UTC)
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/tool-contracts.md, quickstart.md

**Tests**: required (constitution IV): every story has test tasks, written before implementation.

**Organization**: tasks are grouped by user story so each story can be implemented and tested independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependencies)
- **[Story]**: US1-US4 from spec.md
- REQ IDs in brackets; `[AUTHORITY]` = authority file, must match what G1/G2 approved;
  `[HUMAN: persona]` = only that person can do it, `/speckit-implement` stops and asks

## Path Conventions

Existing repository layout (plan.md "Source Code"): `agents/<agent>/src/`, `tools/<tool>/`,
`shared/`, `synthetic/`, `evals/`, `tests/`, `reference/`.

---

## Phase 1: Setup

- [X] T001 Confirm branch `release/0.4.0`, `uv run python -m scripts.adlc status` shows G2 approved, and `make test` is green before changes
- [X] T002 [P] Add clause 3.7 "Missed connection" (benefit, minimum connection time condition, single ticket, 6.3 ticket exclusion not applied, shared limit with 3.6 per journey) to `synthetic/policy_wording.md` [REQ-F-036, REQ-F-037]
- [X] T003 [P] Draft `reference/minimum-connection-times.yaml` per data-model.md (`as_of`, `source`, `owner: product-owner`, `fallback_minutes: 60`, one value per airport for every airport in `synthetic/scenarios.py` FLIGHTS) [REQ-F-035, ADR-006]
- [X] T004 [HUMAN: po] Product owner confirms the minimum connection time values and source in `reference/minimum-connection-times.yaml` (CODEOWNERS: Business owner + AI lead review the PR)

---

## Phase 2: Foundational (blocking)

**Purpose**: data, tools and claim type that every story needs.

### Tests first

- [X] T005 [P] Unit test: flight-status `lookup` returns `scheduled_arr`, `actual_arr`, `arrival_delay_minutes` in `tests/test_missed_connection_tools.py` [REQ-F-034]
- [X] T006 [P] Unit test: `journey_benefits(trip_id, direction, exclude_claim_id)` returns paid delay / missed connection amounts and fingerprints only for the same trip and direction, read-only, in `tests/test_missed_connection_tools.py` [REQ-F-038]
- [X] T007 [P] Unit test: inbound leg matching (unique match, three-leg itinerary, ambiguous → None with reason) and connection time check ("`booked_connection_min >= mct_min` (equal passes)", fallback 60 when airport missing) in `tests/test_missed_connection_rules.py` [REQ-F-033, REQ-F-035, REQ-B-013]

### Implementation

- [X] T008 Add `scheduled_arr`, `actual_arr` to the `flights` table and `journey_direction`, `itinerary_json`, `refund_declared_usd` to the `claims` table in `shared/db.py` (data-model.md: `journey_direction` "`outward` | `return`"; `refund_declared_usd` "default null"; for `missed_connection`, `flight_no` / `flight_date` = "the **onward** flight the customer missed")
- [X] T009 Generate arrival times for synthetic flights (actual arrival = scheduled + delay) in `synthetic/generate.py` / `synthetic/materialize.py`
- [X] T010 [P] Extend `tools/flight_status/impl.py` `lookup` output and `tools/flight_status/tool.yaml` output schema with `scheduled_arr`, `actual_arr`, `arrival_delay_minutes` (contracts/tool-contracts.md) (Security reviews the PR: CODEOWNERS `/tools/`) [REQ-F-034]
- [X] T011 [P] Add read operation `journey_benefits` to `tools/claims_system/impl.py` (`OPERATIONS[...] = "read"`) and `tools/claims_system/tool.yaml` (contracts/tool-contracts.md) (Security reviews the PR: CODEOWNERS `/tools/`) [REQ-F-038, ADR-007]
- [X] T012 [P] New module `agents/verification/src/connection.py`: `load_mct()` from `reference/minimum-connection-times.yaml`, `match_inbound(legs, onward)` and `check_connection(...)` returning the Connection object of data-model.md (code only, no model) [REQ-F-033, REQ-F-035, REQ-B-013]
- [X] T013 Add claim type `missed_connection` (category `delay`) to `agents/intake/src/agent.py` (`CATEGORY`, `claim_type` Literal and description, heuristic) [REQ-F-001]
- [X] T014 Document agent: `CHECKLIST["missed_connection"]` (booking or boarding pass for each leg, receipts, refund declaration) and extraction of `legs[]`, `journey_direction`, `refund_declared_usd` in `agents/document/src/agent.py` and `parsing.py`; facts `flight_no` / `flight_date` set to the onward flight; booking references redacted in logs [REQ-F-032, REQ-F-033]
- [X] T015 Run `make test` and `make validate`; T005-T007 pass

**Checkpoint**: tools, data and claim type ready.

---

## Phase 3: User Story 1 - Pay a valid missed connection (P1) 🎯 MVP

**Goal**: a complete, verified missed connection claim is assessed and routed by the existing matrix.

**Independent Test**: quickstart.md rows 1-3 (USD 320 → row 1; USD 150 offset → row 4; Premier USD 1,300 → USD 1,000, row 2).

### Tests first

- [X] T016 [P] [US1] Calculator tests for section `missed_connection`: receipted ticket/hotel/meals; plan limit (USD 500 / USD 1,000) clause 3.6 type A; compensation and unused-ticket refund offset 3.4 type A; ticket above economy or booked class type B; non-essential 3.5 type B; no 6.3 deduction on ticket lines; expense over USD 150 without receipt → 6.1 type B; EUR receipt converted at the policy rate → 6.2 type A; rebooked ticket costing no more than the original → nothing payable for the ticket, in `agents/assessment/tests/test_calculator_missed_connection.py` [REQ-F-037]
- [X] T017 [P] [US1] Verification tests: inbound arrival delay and cause verified → `inbound_delay_verified`, `connection_time_ok`, `single_ticket` set; a short covered delay (inbound 50 minutes late, weather, booked connection 120 ≥ MCT 90) qualifies (no minimum inbound delay), in `agents/verification/tests/test_missed_connection.py` (create `tests/` dir) [REQ-F-034, REQ-F-035, REQ-F-036]
- [X] T018 [P] [US1] Scenarios `mc_stp`, `mc_offset`, `mc_over_limit`, `mc_short_delay` (inbound 50 min late, weather, connection 120) in `synthetic/scenarios.py` with proposed `expected` (rows 1, 4, 2, 1 per quickstart.md) and `requirements` lists; not yet in `SCENARIOS` until labelled (T047) [REQ-F-032..037]

### Implementation

- [X] T019 [US1] Verification agent: for `missed_connection`, look up inbound and onward legs, set Connection fields from `connection.py`, `verification_success` = 1.0 only when `inbound_delay_verified` (connection time and single ticket are eligibility outcomes → rejection, row 5; lowering verification would send them to row 6 first, contrary to research R8), in `agents/verification/src/agent.py` [REQ-F-034, REQ-F-035]
- [X] T020 [US1] Coverage agent: `BENEFIT["missed_connection"] = "3.7"`, `SECTION_CLAUSES["missed_connection"]` (3.7, 3.2, 3.3, 3.4, 3.5, 3.6), prompt text for clause 3.7 in `agents/coverage/src/agent.py` and `agents/coverage/prompts/` [REQ-F-036]
- [X] T021 [US1] Calculator: section `missed_connection` per research R7 (limit `delay_limit − already paid` clause 3.6; 3.4 includes `refund_declared_usd`; "3.7" ticket class judgement type B) in `agents/assessment/src/calculator.py`; type A clauses only from code (T9) [REQ-F-037]
- [X] T022 [US1] Assessment agent: pass connection facts, refund and journey figures to the calculator; heuristic judgement for ticket class above reasonable; decision package lines carry `benefit` and clause, in `agents/assessment/src/agent.py` [REQ-F-037, REQ-F-021]
- [X] T023 [US1] Orchestrator: call `journey_benefits` (orchestrator code, not model-attached) and pass to assessment in `agents/orchestrator/src/claim_graph.py`; `routing.py` unchanged [REQ-F-038]
- [X] T024 [US1] Workbench: show the connection (booked time, MCT value, source, as-of), line benefit and shared limit remaining in the decision package view in `frontend/pages/2_Adjuster_Workbench.py` [REQ-F-021]
- [X] T025 [US1] `make test` green incl. T016-T017; run the three US1 scenarios end to end with `make api` and check routes match quickstart.md

**Checkpoint**: US1 works on its own.

---

## Phase 4: User Story 2 - Non-qualifying claims go to a person (P1)

**Goal**: excluded cause, late check-in, short connection, self-transfer, unverified or ambiguous inbound delay never pay automatically.

**Independent Test**: quickstart.md rows 4-8 route to row 5 (rejection) or row 6 (investigation).

### Tests first

- [X] T026 [P] [US2] Routing guard test: for every missed connection test case, routes 1-3 occur only when `inbound_delay_verified` and `connection_time_ok` and `single_ticket`; uses `agents/orchestrator/src/routing.py` unchanged, in `agents/orchestrator/tests/test_missed_connection_guard.py` [REQ-B-012]
- [X] T027 [P] [US2] Assessment tests: `rejection_reasons` returns a reason with clause for short connection (3.7), self-transfer (3.7), excluded cause / announced strike (3.3), late check-in for the inbound leg (3.3) in `agents/assessment/tests/test_missed_connection_rejections.py` [REQ-F-036]
- [X] T028 [P] [US2] Scenarios `mc_strike`, `mc_short_connection`, `mc_self_transfer`, `mc_inbound_on_time`, `mc_three_legs`, `mc_boarding_closed_early` (valid connection, boarding closed early → L1 with the reason) in `synthetic/scenarios.py` with proposed `expected` (rows 5, 5, 5, 6, 6, 6) [REQ-F-034, REQ-F-036, REQ-B-012, REQ-B-013]
- [X] T056 [P] [US2] Test: the duplicate-claim check (REQ-F-013) does not treat a travel delay claim and a missed connection claim on the same journey as duplicates; overlapping receipts are handled by 7.2 (T037) instead, in `tests/test_missed_connection_journey.py` [REQ-F-013, REQ-F-038]

### Implementation

- [X] T029 [US2] `rejection_reasons` for `missed_connection` in `agents/assessment/src/agent.py` (short connection, self-transfer, excluded cause, late check-in) → recommendation reject → row 5 [REQ-F-036, REQ-B-002]
- [X] T030 [US2] Verification: source not found, no cause, on-time inbound, document/source mismatch or ambiguous leg → `verification_success = 0` with discrepancy text → confidence ≤ 0.35 → row 6, in `agents/verification/src/agent.py` [REQ-F-034, REQ-B-012, REQ-B-013]
- [X] T031 [US2] Fraud screening receives itinerary time mismatches as anomaly flags (flag only) in `agents/fraud-screening/src/agent.py` [FR-013, REQ-F-016, T13]
- [X] T032 [US2] `make test` green incl. T026-T027, T056

---

## Phase 5: User Story 3 - No double payment (P2)

**Goal**: shared limit and duplicate expenses across claims and lines on the same journey, either order.

**Independent Test**: quickstart.md rows 9-10.

### Tests first

- [X] T033 [P] [US3] Calculator tests: shared limit with prior paid delay (USD 300 + USD 350 → USD 200, 3.6 A); duplicate fingerprint → 7.2 type A full line; outward and return separate; combined claim with `benefit` lines, in `agents/assessment/tests/test_calculator_missed_connection.py` [REQ-F-038]
- [X] T034 [P] [US3] Order test: travel delay claim assessed after a paid missed connection on the same journey is capped and de-duplicated the same way, in `tests/test_missed_connection_journey.py` [REQ-F-038]
- [X] T035 [P] [US3] Scenarios `mc_after_delay_shared_limit`, `mc_duplicate_receipt`, `delay_after_mc` in `synthetic/scenarios.py` with proposed `expected` [REQ-F-038]

### Implementation

- [X] T036 [US3] Receipt fingerprint (document SHA-256, else merchant, date, currency, amount) on lines in `agents/document/src/agent.py` [REQ-F-038, ADR-007]
- [X] T037 [US3] Calculator: add "7.2" to `TYPE_A_CLAUSES`; deduct lines whose fingerprint is in `paid_fingerprints`; apply the shared limit to `delay` claims too using `journey_benefits` in `agents/assessment/src/calculator.py` [REQ-F-038]
- [X] T038 [US3] Combined claims: `benefit` per line, each line assessed under its own clause, one shared limit, in `agents/assessment/src/agent.py` [REQ-F-001, REQ-F-038]
- [X] T039 [US3] `make test` green incl. T033-T034

---

## Phase 6: User Story 4 - Decision letter (P2)

**Goal**: missed connection letters from approved templates with breakdown, clauses and appeal rights.

**Independent Test**: letters for a paid, a partial and a rejected missed connection claim pass the letter rubric.

- [X] T040 [P] [US4] Draft templates `missed_connection_paid`, `missed_connection_partial`, `missed_connection_rejected` in `agents/communication/templates/approved.yaml` (breakdown, clause per deduction, appeal 8.1) [REQ-F-039, REQ-B-008]
- [X] T041 [US4] Template selection for `missed_connection` in `agents/communication/src/agent.py` [REQ-F-039]
- [X] T042 [US4] Letter eval cases for the three outcomes in `evals/agents/communication/suite.yaml` [REQ-F-039]
- [ ] T043 [HUMAN: compliance] Compliance reviews and approves the three missed connection templates (G4 artifact `agents/communication/templates/approved.yaml`)

---

## Phase 7: Evals, datasets and metrics

- [X] T044 [P] Metrics in `evals/run_evals.py` for the 4 G1 thresholds: `missed_connection_eligibility_accuracy`, `connection_time_check_accuracy`, `inbound_leg_attribution_accuracy`, `duplicate_expense_paid_count`; each must be measured (not "not measured in this run") whenever missed connection cases exist [REQ-F-033..038]
- [X] T045 [P] Agent suites: missed connection cases in `evals/agents/verification/suite.yaml`, `evals/agents/assessment/suite.yaml`, `evals/agents/intake/suite.yaml`, `evals/agents/document/suite.yaml`
- [X] T046 [P] Adversarial cases: injected instruction in a receipt, altered boarding-pass time, self-transfer, upgraded class, three legs, ambiguous connection in `evals/datasets/adversarial/` source (via `synthetic/generate.py`)
- [X] T047 [HUMAN: l2-grace] Senior adjuster labels the proposed golden cases (at least the 12 planned at G1; T018, T028, T035 propose 14) (expected outcome, amount, deductions, route); `hoc` reviews; only labelled cases are added to `SCENARIOS` [Constitution V]
- [X] T048 [HUMAN: ailead] AI lead labels the 6 adversarial cases with `l2-grace`
- [X] T049 Regenerate datasets with `make datasets` (golden + adversarial, versioned) after T047-T048 [AUTHORITY: `evals/datasets/**` edits need approval]
- [X] T050 Add missed connection scenarios to `TRAFFIC_WEIGHTS` in `synthetic/scenarios.py` so `make perf` covers them [REQ-N-001, REQ-N-004]
- [ ] T051 Traceability: map `REQ-F-032..039`, `REQ-B-012/013` verification methods in `scripts/build_traceability.py` if new methods need mapping; `make traceability` shows no unverified requirement

---

## Phase N: Polish & Cross-Cutting

- [X] T052 [P] Add `missed-connection` to `system.yaml` `scope` (not a routing key; approval prompt applies to `system.yaml`)
- [X] T053 [P] Update `docs/rai/rai-card.md` and `docs/rai/transparency-notice.md` for the new claim type
- [X] T054 [P] Update `README.md` claim types and quickstart pointer
- [ ] T055 Run quickstart.md end to end; fix any mismatch

---

## ADLC Task Rules

- Work happens on `release/0.4.0` and merges by pull request with CODEOWNERS review.
- Each implementation and test task names the REQ IDs it serves.
- No task changes `agents/orchestrator/src/routing.py`, `evals/thresholds.yaml`, `system.yaml` routing
  keys or confidence weights. `docs/design/tool-permission-matrix.yaml` already reflects G2.
- Tasks only a person can do are marked `[HUMAN: <persona>]`; `/speckit-implement` stops there and
  asks. Gate decisions are never tasks.
- Nothing under `evidence/` is written by hand; evidence comes from the commands in the phase below.

## Phase ADLC: Evidence and Gate Readiness (G3, G4)

- [ ] TA01 `make test` green (unit + routing tests)
- [ ] TA02 `make evals` meets every threshold in `evals/thresholds.yaml`, with all 4 missed connection metrics measured
- [ ] TA03 `make perf` (3x peak, missed connection in the mix)
- [ ] TA04 `make traceability`: no unverified requirement
- [ ] TA05 Open the pull request `release/0.4.0` → `main` with CODEOWNERS reviewers
- [ ] TA06 `uv run python -m scripts.adlc check G3`; `/speckit-adlc-gate G3` writes `briefs/G3.md`
- [ ] TA07 RAI: fairness, PII, injection and red-team suites pass (in `make evals`); RAI card and transparency notice updated (T053)
- [ ] TA08 [HUMAN: compliance] Letter templates approved (T043)
- [ ] TA09 `uv run python -m scripts.adlc check G4`; `/speckit-adlc-gate G4` writes `briefs/G4.md`

**Checkpoint**: G3 is signed by AI lead + Product owner; G4 by RAI/Risk + Compliance.

---

## Dependencies & Execution Order

- Setup (T001-T004) → Foundational (T005-T015) → US1 (T016-T025) → US2 (T026-T032) and US3
  (T033-T039) in either order → US4 (T040-T043) → Evals (T044-T051) → Polish → Phase ADLC.
- T004 (MCT values) must be done before T047 labelling, because labels depend on MCT outcomes.
- T047/T048 (human labelling) gate T049 and the G3 eval evidence.
- T043 (Compliance) gates G4, not G3.

### User Story Dependencies

- US1 needs Foundational. US2 reuses US1's verification path. US3 needs T023 (journey benefits
  wired). US4 needs US1-US3 outcomes for its letter cases.

### Parallel Opportunities

- T002, T003; T005-T007; T010-T012; tests within each story ([P]); T044-T046; T052-T054.

## Parallel Example: User Story 1

```text
T016 calculator tests | T017 verification tests | T018 scenarios
```

## Implementation Strategy

1. MVP = Setup + Foundational + US1 (valid claims paid within the shared limit).
2. Add US2 (safety: nothing non-qualifying pays automatically) before any demo to signers.
3. US3, US4, then evals with human-labelled cases, then the G3 / G4 evidence.

## Notes

- `evals/run_evals.py` `compare()` reports a threshold without a metric as "not measured" rather than
  failing; T044 and TA02 make sure the 4 new metrics are actually measured.
- Golden cases in this repo are synthetic scenarios with expected outcomes; "labelling" (T047) means
  a named person confirms or corrects each proposed expected outcome before the case counts.
