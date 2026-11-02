# Implementation Plan: Missed Connection Benefit

**Branch**: `release/0.4.0` (created at G3) | **Date**: 2026-11-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/0.4.0-missed-connection/spec.md` (G1 approved 2026-11-02)

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

Add a missed connection benefit (policy clause 3.7) to the travel claims assistant. The intake agent
recognises the new claim type; the document agent extracts every itinerary leg; code identifies the
inbound leg, checks the booked connection against a versioned minimum connection time reference and
single-ticket booking; the verification agent confirms the inbound arrival delay and cause with the
flight-status source (whose output gains arrival times); the calculator pays reasonable rebooking,
hotel and meal costs within the plan's travel delay limit shared per journey, offsets compensation
and unused-ticket refunds and removes duplicate expenses. Routing, limits, confidence and the
payment path are unchanged: failures become rejections (row 5) or investigations (row 6) through
the existing rules. Design decisions are in [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.12 (`uv`)

**Primary Dependencies**: LangGraph (orchestrator, `create_react_agent` sub-agent tasks), FastAPI,
Streamlit, FAISS + fastembed (clause retrieval), Presidio (redaction), Arize Phoenix (telemetry).
No new dependency.

**Storage**: SQLite (mock claims system, flights), local object store (documents), FAISS index;
new versioned YAML reference `reference/minimum-connection-times.yaml`

**Testing**: pytest (`make test`), eval suites and thresholds (`make evals`), perf (`make perf`),
traceability (`make traceability`)

**Target Platform**: Linux / macOS server (local pilot), mock models by default (D-10)

**Project Type**: multi-agent backend service + workbench web UI

**Performance Goals**: decision package p95 ≤ 10 minutes (REQ-N-001) at 3x peak (REQ-N-004),
including missed connection claims

**Constraints**: no change to routing.py, system.yaml authority keys, the routing matrix or the
approval token; type A deductions produced only by the calculator (T9); bank details never reach a
model; no new data category

**Scale/Scope**: one new claim type, ~8 modules touched (intake, document, verification, assessment,
communication, orchestrator wiring, two tools), 12 golden + 6 adversarial cases

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Passed before Phase 0 and re-checked after Phase 1; see "ADLC: Constitution Check by Principle"
below. No violations, so Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
specs/0.4.0-missed-connection/
├── intake.md            # /speckit-adlc-intake
├── spec.md              # G1 (/speckit-specify, /speckit-clarify)
├── plan.md              # this file (G2)
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/           # Phase 1 (tool contract changes)
├── checklists/          # requirements.md, g1-review.md
├── briefs/              # G0.md, G1.md, G2.md ...
└── tasks.md             # /speckit-tasks (G3, not created here)
```

### Source Code (repository root)

```text
agents/
├── intake/src/agent.py            # claim type missed_connection, category delay
├── document/src/{agent,parsing}.py# legs[] extraction, journey direction, refund declaration, completeness
├── verification/src/agent.py      # inbound leg match, MCT check, single ticket, arrival delay + cause
├── coverage/                      # clause 3.7 eligibility; prompts
├── assessment/src/calculator.py   # missed_connection section, shared limit, 3.4 refund offset, 7.2 duplicates
├── communication/templates/       # missed_connection_* letters (G4, Compliance)
└── orchestrator/src/claim_graph.py# journey_benefits read -> assessment (routing.py unchanged)
tools/
├── flight_status/{tool.yaml,impl.py}   # arrival fields
└── claims_system/{tool.yaml,impl.py}   # journey_benefits (read)
reference/minimum-connection-times.yaml # new, ADR-006
shared/db.py                            # flights arrival columns; claims journey/itinerary/refund columns
synthetic/{policy_wording.md,scenarios.py}  # clause 3.7, missed connection scenarios
evals/                                  # metrics for the 4 new thresholds; datasets (labelled by people)
tests/                                  # unit tests incl. REQ-B-012 routing guard
frontend/pages/2_Adjuster_Workbench.py  # decision package view: connection, line benefit, shared limit
```

**Structure Decision**: extend the existing agents and tools in place; no new agent (constitution
VII). One new reference file under `reference/`.

## Complexity Tracking

No violations.

## ADLC: Constitution Check by Principle

| Principle | Status | Evidence / note |
|---|---|---|
| I. People sign every gate | pass | No step records a gate or persona decision; labelling and letter approval are `[HUMAN]` tasks |
| II. Gates run in order | pass | G1 approved 2026-11-02 13:04; one correction to a G1 source document listed under Rework (not a frozen artifact) |
| III. Authority is code | pass | routing.py, routing matrix, system.yaml routing keys, confidence weights and approval token unchanged; failures reach rows 5/6 via existing rules (research R8); tool matrix change goes to Security at G2 |
| IV. Traceable, eval-gated release | pass | Every REQ-F-032..039 / REQ-B-012/013 has a verification method; 4 thresholds agreed at G1 get eval metrics at G3 |
| V. Human ground truth, generated evidence | pass | 12 golden + 6 adversarial cases labelled by `l2-grace` / `ailead`; evidence from `make` targets only |
| VI. Least privilege, data protection | pass | No new tool; one new read operation for the orchestrator, one extended read output; boarding-pass data within "Travel documents"; booking references redacted in logs; threat model T10-T13 added |
| VII. Simplicity | pass | No new dependency, agent or model; one versioned reference file (ADR-006) |

## ADLC: G2 Design Record *(mandatory)*

| G2 artifact | Change for this release | File |
|---|---|---|
| Architecture and topology | Missed connection flow and the MCT reference added to the architecture; topology (agents, delegation) unchanged | docs/design/architecture.md, system.yaml#topology (unchanged) |
| ADRs | ADR-006 Minimum connection time reference; ADR-007 Journey-level shared limit and duplicate expenses | docs/design/adr/ |
| Tool permission matrix | TOOL-FLIGHT-STATUS lookup output adds arrival times (read, verification); TOOL-CLAIMS-SYSTEM new read op `journey_benefits` (orchestrator, not attached to a model) | docs/design/tool-permission-matrix.yaml (Security) |
| Threat model | T10 stale/wrong MCT; T11 delay attributed to the wrong leg; T12 double payment across claims / reused receipts; T13 altered itinerary or boarding-pass times | docs/design/threat-model.md |
| Data flows and redaction points | Boarding-pass / itinerary fields, booking reference redaction, MCT reference read | docs/design/data-flows.md |
| Change control | CODEOWNERS for `reference/` (Business owner + AI lead) | CODEOWNERS |
| Manifests | No agent or topology change; tool.yaml contracts updated at G3 to match this design | `make validate` |

### Rework of Earlier Gates

- `spec.md` User Story 1, acceptance scenario 2 said the USD 150 offset case routes to L1 (row 2);
  under the approved routing matrix a 47% reduction is above the 25% significance net, so it routes
  L1 then L2 (row 4). Corrected in the spec with a note. `spec.md` is the source of the G1
  requirements, not a frozen G1 artifact; the requirements, thresholds and routing are unaffected.
  For the G1 signers' information; no re-approval of G1 proposed.
- After `/speckit-analyze` (G3), the Business owner clarified that the late check-in exclusion refers
  to the inbound leg (spec Clarifications, FR-006). This narrows REQ-F-036 without changing its text
  in the frozen `docs/requirements/functional.yaml`; no re-approval of G1 proposed.

### Decisions for G2 Signers

| # | Decision | Options | Recommended | Signer |
|---|---|---|---|---|
| 1 | MCT source (ADR-006) | A versioned reference file, one value per airport, 60 min fallback / B licensed MCT feed | A for 0.4.0 (no new dependency); B as a later improvement | Architecture |
| 2 | Journey benefits read (ADR-007) | A new read op for the orchestrator / B claims-system access for the assessment agent | A (no wider permissions) | Security |
| 3 | Duplicate expense deduction type | A type A (exact match, code only) / B type B (judgement) | A, as research R6 | Architecture, Security |
| 4 | Flight-status contract change | extend `lookup` output / new operation | extend `lookup` (same data, same agent) | Security |
