# Research: Missed Connection Benefit (0.4.0)

Phase 0 of `/speckit-plan`. Each item: decision, rationale, alternatives. Items R4, R5 and R6 need a
G2 decision by Architecture / Security (see plan.md "Decisions for G2 Signers").

## R1 Claim type and combined claims

- **Decision**: new claim type `missed_connection` (category `delay`). A claim with both travel
  delay and missed connection expenses keeps `claim_type = missed_connection` and tags each expense
  line with `benefit: delay | missed_connection`; each line is assessed under its own clause.
- **Rationale**: spec clarification 5 (one claim); line-level benefit keeps the calculator and the
  decision package explicit per clause; the intake Literal and `CATEGORY` map change, routing does not.
- **Alternatives**: a separate `delay_missed_connection` type (doubles every branch on claim type);
  splitting into two linked claims (rejected by the Business owner).

## R2 Itinerary and inbound leg

- **Decision**: the document agent extracts `legs[]` (flight number, date, origin, destination,
  scheduled departure and arrival, booking reference) from bookings and boarding passes. The
  inbound leg is the leg whose destination is the onward leg's origin and whose scheduled arrival
  precedes the onward scheduled departure; computed in code, not by the model. No unique match →
  ambiguous → L1 with the reason (REQ-B-013).
- **Rationale**: leg attribution is a named G0 failure mode; deterministic matching is testable and
  meets `inbound_leg_attribution_accuracy` = 1.0.
- **Alternatives**: let the model pick the inbound leg (not deterministic, fails the threshold).

## R3 Flight-status arrival data

- **Decision**: extend the existing `TOOL-FLIGHT-STATUS.lookup` output with `scheduled_arr`,
  `actual_arr` and `arrival_delay_minutes`. Same operation, same access (`read`), same agent
  (verification). The mock `flights` table gains the two arrival columns.
- **Rationale**: a missed connection depends on the inbound **arrival**; licensed flight-status
  sources report arrival times alongside departures, so no new source and no new data category.
- **Alternatives**: a new `arrival_lookup` operation (more surface, same data); estimating arrival
  from departure delay (inaccurate).

## R4 Minimum connection time reference (G1 carry-over CHK012/CHK013)

- **Decision**: a versioned reference file `reference/minimum-connection-times.yaml` (airport IATA
  code → minutes, with source and as-of date), read by verification code. One value per airport;
  fallback 60 minutes when the airport is not listed. Owner: Product owner (Claims operations),
  reviewed quarterly; changes by pull request with CODEOWNERS (Business owner + AI lead), because
  the value decides eligibility. Recorded as ADR-006.
- **Rationale**: deterministic, auditable (the value and its as-of date go into the decision
  package), no new external dependency (constitution VII); terminal-specific and
  domestic/international values are out of scope for 0.4.0.
- **Alternatives**: licensed MCT feed from the flight-data provider (new contract and cost, better
  coverage; candidate for a later release); fixed 60 minutes everywhere (rejected at G1).

## R5 Journey and the shared limit

- **Decision**: a journey is `trip_id` + `direction` (`outward` | `return`), with direction taken
  from the booking. New read operation `TOOL-CLAIMS-SYSTEM.journey_benefits(trip_id, direction,
  exclude_claim_id)` returns, for other claims on the same journey, the approved delay and missed
  connection amounts and the receipt fingerprints of their paid lines. Called by the orchestrator
  (which already reads linked totals, D-07) and passed to assessment. Recorded as ADR-007.
- **Rationale**: the limit and duplicate check must work in either claim order (FR-009); the
  orchestrator already has read access to the claims system; no side effects.
- **Alternatives**: give the assessment agent claims-system access (widens tool permissions);
  extend `linked_approved_total` (that total is for authority across the whole trip, not per benefit
  and direction; mixing them would change routing semantics).

## R6 Duplicate expenses

- **Decision**: a receipt fingerprint is the document SHA-256 if the same file is reused, else
  (merchant, date, currency, amount). A line whose fingerprint matches a paid line on the same
  journey is deducted in full under clause 7.2 (duplicate claims), **type A** (deterministic match,
  produced only by the calculator).
- **Rationale**: clause 7.2 already states the rule; a code-produced exact match is policy-norm,
  like the compensation offset; a fuzzy "looks similar" match is not used.
- **Alternatives**: type B (sends every overlap to L1 + L2, mostly for exact duplicates); fuzzy
  matching (false positives become customer-outcome errors).

## R7 Calculation

- **Decision**: in `assessment/src/calculator.py`, section `missed_connection` uses the plan's
  `delay_limit` reduced by the amount already paid on the journey (clause 3.6, type A, shared);
  compensation offset 3.4 (type A) includes the declared refund for the unused onward ticket;
  duplicates 7.2 (A); ticket above economy or the originally booked class, non-essential items (3.5)
  and missing receipts (6.1) are judgements (type B). Clause 3.7 is the benefit clause; 6.3's
  "travel tickets" exclusion is not applied to missed connection ticket lines.
- **Rationale**: keeps "type A only from code" (threat T9); reuses the existing order of
  application; shared limit computed per journey.

## R8 Enforcing REQ-B-012 without changing routing

- **Decision**: verification sets `verification_success = 0` when the inbound delay or its cause
  cannot be verified, or when documents and source disagree; the existing confidence cap
  (weakest signal + 0.35) then gives ≤ 0.35 → row 6 investigation. A connection shorter than the
  minimum connection time, self-transfer on separate tickets, an excluded cause or late check-in
  make the recommendation a rejection → row 5 (L1 + L2). A unit test asserts a missed connection is
  never routed row 1-3 without `inbound_delay_verified` and `connection_time_ok`.
- **Rationale**: routing, confidence weights and the 0.7 cut-off stay as approved at G1.
- **Alternatives**: a new routing row for missed connections (authority change, rejected at G1).

## R9 Performance and cost (G1 carry-over CHK029)

- **Decision**: one extra flight-status lookup per missed connection claim (inbound and onward legs);
  no extra model calls (leg matching, MCT and calculation are code); extraction prompts are longer
  for two-leg documents. REQ-N-001 (p95 ≤ 10 min) and REQ-N-004 (3x peak) are re-measured with
  missed connection claims in the perf mix at G3; REQ-N-003 stays a placeholder.

## R10 Boarding-pass data (G1 carry-over CHK030)

- **Decision**: extracted fields only: name, booking reference (PNR), flight number, date, seat,
  sequence. The barcode is not decoded or stored beyond the original document. These fall under the
  existing "Travel documents" (Confidential) category; booking references are redacted in logs like
  other identifiers. No new data category; the Data owner is informed at G2 via the data-flows update.

## R11 Letters and policy wording

- **Decision**: new templates `missed_connection_paid`, `missed_connection_partial`,
  `missed_connection_rejected` in `agents/communication/templates/approved.yaml` (Compliance approves
  at G4). Clause 3.7 is added to the synthetic policy wording (`synthetic/policy_wording.md`) and
  the clause index rebuilt; the real wording is a release dependency outside this system.
