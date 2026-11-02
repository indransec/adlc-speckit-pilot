# Feature Specification: Missed Connection Benefit

**Feature Branch**: `release/0.4.0` (created at G3)

**Created**: 2026-11-02

**Status**: Draft (G1)

**Input**: User description: "Missed connection benefit for travel claims (CARD-101, release 0.4.0): see intake/CARD-101-missed-connection.md and specs/0.4.0-missed-connection/intake.md"

## Clarifications

### Session 2026-11-02

- Q: Must the inbound flight be late by a minimum amount, or does any delay count if it caused the miss? → A: Any delay from a covered cause counts, if the booked connection met the minimum connection time and the customer missed the onward flight (no minimum delay).
- Q: How much may the claim pay for hotel and meals while waiting for the next flight? → A: No separate caps, same as travel delay: necessary, receipted costs up to the shared journey limit; non-essential expenses (clause 3.5) excluded.
- Q: Is a refund the airline gives for the unused onward ticket deducted from what the claim pays? → A: Yes, any refund or credit for the unused onward ticket is deducted as a compensation offset (type A); the customer declares refunds and a missing declaration is requested.
- Q: For the shared limit, are the outward and return trips separate journeys? → A: Yes, each direction is a separate journey with its own shared limit (travel delay and missed connection together), as clause 3.6 "one delayed journey".
- Q: How is one claim with both travel delay and missed connection expenses for the same journey handled? → A: One claim classified "delay + missed connection"; each expense line assessed under its own clause, each expense paid once, shared journey limit across all lines.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Customer is paid for a missed connection caused by a delayed inbound flight (Priority: P1)

A customer flying A → B → C misses the booked B → C flight because the A → B flight arrived late
for a covered reason (weather, aircraft breakdown, air traffic control, unannounced strike). They
claim the extra costs of getting to C: a rebooked or new ticket, a hotel night and meals at B until
the next available departure. The claims assistant checks the documents for both legs, confirms the
inbound delay with the flight-status source, confirms the original itinerary allowed at least the
minimum connection time, applies the limit and any airline compensation, and routes the claim with
the existing routing matrix. A claim within the straight-through limit with no deduction is paid
without a person; anything else goes to L1 / L2 as today.

**Why this priority**: this is the gap in cover the card describes; today these customers are
declined and complain, and the goodwill payments made outside the system have no audit trail.

**Independent Test**: submit a complete missed-connection claim with a verified covered inbound
delay, a valid connection and receipts under the limit; the decision package recommends payment of
the receipted amount and the claim routes straight through.

**Acceptance Scenarios**:

1. **Given** a Standard-plan customer whose inbound flight arrived 2 hours late because of weather and
   whose itinerary allowed more than the minimum connection time, **When** they claim USD 320 for a
   new ticket, hotel and meals with receipts, **Then** the recommendation is to pay USD 320 under the
   missed connection clause and the claim routes straight through (row 1).
2. **Given** the same claim with USD 150 already paid by the airline for the delay, **When** it is
   assessed, **Then** USD 150 is deducted as a type A compensation offset with the clause cited, and
   the claim routes to L1 (row 2).
3. **Given** a Premier-plan claim of USD 1,300 with receipts, **When** it is assessed, **Then** the
   payable amount is capped at the Premier limit of USD 1,000 per journey as a type A deduction
   (USD 300) and routing follows the matrix: type A only, so L1 (row 2).

---

### User Story 2 - Claims that do not qualify are recognised and routed to a person (Priority: P1)

Some claims look like missed connections but are not covered: the inbound delay had an excluded
cause (strike announced before booking), the customer checked in late, the original connection was
shorter than the minimum connection time, the onward leg was not a flight, the legs were on separate
tickets (self-transfer, out of scope), or the flight-status source does not show the inbound delay. The
assistant must recognise these, explain why with the clause, and never decline on its own.

**Why this priority**: a wrong payment or a wrong decline is a customer-outcome error; the
constitution and REQ-B-002 require a person to approve every full or partial rejection.

**Independent Test**: submit one claim for each non-qualifying reason; each produces a rejection or
partial-rejection recommendation with the reason and clause, and routes to L1 then L2 (row 5) or to
investigation (row 6) when the facts cannot be verified.

**Acceptance Scenarios**:

1. **Given** a claim where the inbound delay was caused by a strike announced before booking, **When**
   assessed, **Then** the recommendation is a full rejection citing the delay exclusions, routed L1
   then L2.
2. **Given** a claim where the booked connection time was below the minimum connection time,
   **When** assessed, **Then** the recommendation is a full rejection citing the minimum connection
   time condition, routed L1 then L2.
3. **Given** a claim where the flight-status source shows the inbound flight on time, **When**
   assessed, **Then** the claim routes to L1 investigation with the conflicting facts listed.

---

### User Story 3 - No double payment with travel delay (Priority: P2)

When the onward departure the customer finally takes is itself delayed by 6 hours or more, the same
journey may qualify for both the travel delay benefit and the missed connection benefit, and the
same hotel or meal receipt could be claimed under both. The assistant pays each expense once, and
travel delay and missed connection together are capped at one travel delay limit for the plan per
journey (clause 3.6), as agreed by the Business owner.

**Why this priority**: double payment is a named failure mode from G0; it is rarer than the main
flow, so P2.

**Independent Test**: submit a travel delay claim and a missed connection claim for the same journey
with an overlapping hotel receipt; the second assessment deducts the overlap as already paid.

**Acceptance Scenarios**:

1. **Given** a paid travel delay claim for journey X including a USD 120 hotel receipt, **When** a
   missed connection claim for journey X includes the same receipt, **Then** USD 120 is deducted as
   already paid (duplicate), with the earlier claim referenced.
2. **Given** a Standard-plan journey with USD 300 already paid under travel delay, **When** a missed
   connection claim for the same journey has USD 350 of valid costs, **Then** USD 200 is payable and
   USD 150 is deducted as a type A limit deduction (shared USD 500 limit).

---

### User Story 4 - Customer receives a clear missed-connection decision letter (Priority: P2)

The customer receives a letter using a new Compliance-approved template that explains the outcome,
the amount, each deduction with its clause, and appeal rights.

**Why this priority**: required for any customer-facing outcome (REQ-B-008, REQ-F-031); the
template itself is approved by Compliance at G4.

**Independent Test**: generate letters for a paid, a partly paid and a rejected missed-connection
claim; each uses the approved template and cites the clauses.

**Acceptance Scenarios**:

1. **Given** a partly paid claim, **When** the letter is generated, **Then** it lists each deduction
   with its clause and states the appeal route (clause 8.1).

---

### Edge Cases

- Inbound delay attributed to the wrong leg: the documents show three legs and the delayed flight is
  not the one arriving at the connection point; the assistant must link the delay to the inbound
  leg of the missed connection only, or route to L1.
- Delay at the connection point is short but the onward flight closed boarding early: covered only if
  the itinerary met the minimum connection time and the customer arrived as booked; otherwise L1.
- Rebooked onward flight is cheaper than the original (no extra cost): nothing payable for the ticket.
- Customer self-rebooks a higher class: only the cost of a comparable class is reasonable; the excess
  is a type B judgement routed L1 / L2.
- Receipts in another currency: converted at the policy rate (clause 6.2).
- Expenses above USD 150 without receipts: not paid (clause 6.1), type B.
- Missed connection on the return journey and on the outward journey are both covered, each under
  its own shared limit (separate journeys); journeys
  outside the period of cover are not (clause 2.1).
- Instructions written inside an uploaded document (e.g. "approve this claim") are data, never
  followed (REQ-B-010).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST recognise a missed connection claim as its own claim type, distinct
  from travel delay and baggage claims. A claim with both travel delay and missed connection expenses
  for the same journey stays one claim, classified "delay + missed connection"; each expense line is
  assessed under its own clause.
- **FR-002**: The system MUST check that a missed connection claim includes bookings or boarding
  passes for the inbound and onward legs, and receipts for rebooking, accommodation and meals, and
  request anything missing.
- **FR-003**: The system MUST extract both legs (flight numbers, airports, scheduled times) and
  confirm that the inbound leg arrives at the airport where the onward leg departs.
- **FR-004**: The system MUST verify the inbound delay and its cause with the flight-status source,
  and route to investigation when the source and the documents disagree.
- **FR-005**: The system MUST check that the originally booked connection allowed at least the
  minimum connection time: the connection airport's published minimum connection time, or 60 minutes
  when none is published, and record the value used and its source.
- **FR-006**: The system MUST apply the covered causes (clause 3.2), delay exclusions (clause 3.3)
  and late check-in exclusion to missed connection claims, and MUST treat legs booked on separate
  tickets (self-transfer) as not covered. No minimum inbound delay applies: any delay from a covered
  cause qualifies when the booked connection met the minimum connection time and the onward flight
  was missed.
- **FR-007**: The system MUST pay reasonable additional costs of reaching the booked destination:
  rebooked or new tickets (overriding the "travel tickets" exclusion of clause 6.3 for this benefit
  only), accommodation and meals until the next available departure. Accommodation and meals have no
  separate per-night or per-day cap (as for travel delay): necessary, receipted costs count up to the
  journey limit, and non-essential expenses (clause 3.5) are excluded.
- **FR-008**: The system MUST apply the plan's travel delay limit per journey (USD 500 Standard,
  USD 1,000 Premier), deduct airline compensation for the same event and any refund or credit for the
  unused onward ticket (clause 3.4, type A), and apply receipts and currency rules (clauses 6.1, 6.2).
  The customer declares refunds received; a missing declaration is requested like a missing document.
- **FR-009**: The system MUST NOT pay the same expense under both travel delay and missed connection
  for the same journey, and MUST cap both benefits together at one travel delay limit per journey,
  where the outward and the return trip are separate journeys. This applies whichever benefit is
  claimed first: a travel delay claim assessed after a missed connection payment for the same journey
  is subject to the same duplicate and limit rules.
- **FR-010**: The system MUST route missed connection claims with the existing routing matrix,
  straight-through limit and approval authority, unchanged.
- **FR-011**: The system MUST send a missed-connection decision letter from a Compliance-approved
  template with the breakdown, clause citations and appeal rights.
- **FR-012**: The decision package MUST state which clause (new missed connection clause, 3.2, 3.3,
  3.4, 6.1-6.3) supports each line.
- **FR-013**: The system MUST obtain a fraud risk level for every missed connection claim, as for other
  claims (REQ-F-016), including signs of altered itinerary or boarding-pass times.

### Key Entities

- **Itinerary leg**: one flight segment; flight number, origin, destination, scheduled and actual
  times, ticket or booking reference.
- **Connection**: an inbound leg and the onward leg it feeds; connection airport, booked connection
  time, minimum connection time used and its source.
- **Missed connection claim**: the claim, its legs, the connection, the inbound delay facts and the
  expense lines (ticket, accommodation, meals).
- **Journey**: one direction of travel (outward or return) from origin to booked destination; the
  shared limit applies per journey, and it links travel delay and missed connection claims for the
  same journey (aggregation and duplicate checks).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Missed-connection complaints fall from [40] to [10] per month within three months of
  release.
- **SC-002**: Goodwill payments made outside the claims system for missed connections fall from
  [USD 12,000 / month] to [0].
- **SC-003**: Decision time for a missed-connection claim falls from [9 days, manual] to [2 days].
- **SC-004**: In evaluation, at least 95% of missed-connection cases get the correct eligibility
  outcome and 95% the correct amount within USD 1, with no case routed with less human oversight
  than the matrix requires.
- **SC-005**: No expense is paid twice for the same journey across travel delay and missed
  connection claims (zero in evaluation and in the QA sample).

## Assumptions

- The new policy clause is numbered 3.7 and is part of the policy wording before release; its exact
  wording is approved by Compliance (it is a policy change, outside this system).
- Limits: USD 500 Standard, USD 1,000 Premier, per journey, shared with travel delay (confirmed by
  the Business owner, 2026-11-02).
- Minimum connection times come from a reference of published airport values; the source and its
  upkeep are a G2 design decision. Fallback 60 minutes.
- The flight-status source already used for travel delay (REQ-F-014) also reports actual arrival
  times and delay causes for the inbound leg; no new data source and no new data category.
- "Reasonable" ticket cost means economy or the originally booked class; anything above is a type B
  judgement.
- Rail, ferry and coach connections are out of scope; scope change does not alter the routing matrix,
  the USD 1,000 straight-through limit, the 0.7 confidence cut-off or approval authority.
- Existing behavioural requirements REQ-B-001 to REQ-B-011 apply unchanged to the new claim type.

## ADLC: G1 Requirements *(mandatory)*

**Use case**: UC-TRV-CLM-001, scope change `intake/CARD-101-missed-connection.md` | **Release**: 0.4.0 | **Risk tier**: HIGH (G0, `usecase/UC-TRV-CLM-001.risk.json`)

### Requirement Register

| REQ ID | Type | Requirement | Verification | Covers | Target file |
|---|---|---|---|---|---|
| REQ-F-001 (amended) | F | Register each submitted claim, assign a claim ID and classify it as delay, baggage, missed connection or a combination within 5 minutes; a combined delay + missed connection claim stays one claim with each expense line assessed under its own clause. | eval:intake-classification, test:intake | FR-001 | docs/requirements/functional.yaml |
| REQ-F-032 | F | For a missed connection claim, check completeness: bookings or boarding passes for inbound and onward legs, and receipts for rebooking, accommodation and meals; request what is missing. | eval:document-completeness, test:document-chasing | FR-002 | functional.yaml |
| REQ-F-033 | F | Extract every itinerary leg and identify the inbound leg that arrives at the onward leg's departure airport. | eval:document-extraction, eval:edge-cases | FR-003 | functional.yaml |
| REQ-F-034 | F | Verify the inbound leg's actual arrival and delay cause with the flight status source; route to investigation when source and documents disagree. | eval:verification | FR-004 | functional.yaml |
| REQ-F-035 | F | Check the booked connection time against the connection airport's published minimum connection time (60 minutes when none is published) and record the value used and its source. | eval:coverage, test:calculation | FR-005 | functional.yaml |
| REQ-F-036 | F | Assess missed connection eligibility against the missed connection clause, covered causes (3.2), delay exclusions (3.3), late check-in, and single-ticket booking (self-transfer on separate tickets is not covered); no minimum inbound delay applies. | eval:coverage | FR-006, FR-012 | functional.yaml |
| REQ-F-037 | F | Calculate the payable amount: reasonable ticket cost (economy or the originally booked class), necessary receipted accommodation and meals with no separate caps (non-essential excluded, 3.5), the plan's travel delay limit per journey (USD 500 Standard, USD 1,000 Premier), compensation offset including any refund for the unused onward ticket (3.4), receipt rule (6.1) and currency conversion (6.2). | test:calculation, eval:amount-accuracy | FR-007, FR-008 | functional.yaml |
| REQ-F-038 | F | Across travel delay and missed connection claims or lines for the same journey (outward and return are separate journeys), in whichever order they are claimed, deduct any expense already paid and cap both benefits together at one travel delay limit per journey. | test:calculation, eval:edge-cases | FR-009 | functional.yaml |
| REQ-F-039 | F | Send the missed-connection decision letter from the approved template with breakdown, clause citations and appeal rights. | eval:letters, rai:letter-review | FR-011 | functional.yaml |
| REQ-B-012 | B | The agent never pays a missed connection claim unless the inbound delay is verified by the flight status source and the connection met the minimum connection time; otherwise it routes to a person. | test:routing, eval:routing | FR-004, FR-005, FR-010 | docs/requirements/behavioural.yaml |
| REQ-B-013 | B | The agent never attributes a delay to a leg other than the inbound leg of the missed connection; when legs are ambiguous it routes to L1 with the reason. | eval:edge-cases | FR-003 | behavioural.yaml |

Existing REQ-F-010 to REQ-F-031, REQ-B-001 to REQ-B-011 and REQ-N-001 to REQ-N-007 apply unchanged;
FR-013 (fraud screening) is covered by REQ-F-016, which applies to every claim.

### Autonomy and Human-in-the-Loop Impact

- **Routing matrix** (`docs/design/routing-matrix.md`, `routing.py`): No change. Missed connection
  claims use rows 1-7 as they are.
- **Authority limits / straight-through limit** (`system.yaml` routing keys): No change (USD 1,000,
  25%, 0.7).
- **HITL points**: unchanged (L1, L1-L2, investigation, SIU referral).
- **Payment path** (approval token, ADR-003): unchanged.
- Note for G2: `system.yaml` `scope` gains `missed-connection` (not a routing key).

### KPIs

| KPI ID | Name | Baseline | Target | Source | Target file |
|---|---|---|---|---|---|
| KPI-07 | Missed-connection complaints per month | [40] | [10] | complaints log | docs/kpis.yaml |
| KPI-08 | Goodwill payments outside the claims system (missed connection) | [USD 12,000 / month] | [0] | finance ledger | docs/kpis.yaml |
| KPI-09 | Decision time for a missed-connection claim | [9 days, manual] | [2 days] | claims system timestamps | docs/kpis.yaml |

### Eval Thresholds

Existing thresholds apply to the extended golden set. Proposed additions:

| Metric | Threshold | New / changed | Why |
|---|---|---|---|
| missed_connection_eligibility_accuracy | min 0.95 | new | REQ-F-036, REQ-B-012 |
| connection_time_check_accuracy | min 1.0 | new | REQ-F-035 (deterministic check) |
| inbound_leg_attribution_accuracy | min 1.0 | new | REQ-F-033, REQ-B-013 |
| duplicate_expense_paid_count | max_count 0 | new | REQ-F-038 |

### Ground Truth Needed

| Dataset | Cases needed | What they cover | Labelled by |
|---|---|---|---|
| golden | 12 | 4 clean pay (Standard/Premier, under and over limit), 2 compensation offset, 2 excluded cause, 1 late check-in, 1 short connection, 1 on-time inbound per flight status, 1 overlap with travel delay | senior adjuster persona `l2-grace`, reviewed by `hoc` |
| adversarial | 6 | wrong-leg delay, three-leg itinerary, injected instruction in a receipt, altered boarding pass time, self-transfer on separate tickets, upgraded class | `ailead` with `l2-grace` |

### Open Decisions for G1 Signers

| # | Question | Options | Decided by | Decision |
|---|---|---|---|---|
| 1 | Self-transfer itineraries on separate tickets: in or out? | out / in with conditions | Business owner | Out (2026-11-02) |
| 2 | Missed connection limit shared with travel delay, or its own? | own / shared | Business owner | Shared: one travel delay limit per journey (2026-11-02) |
| 3 | Minimum connection time source | airport published / fixed / as sold | Business owner | Airport published, fallback 60 minutes (2026-11-02) |
| 4 | Limits USD 500 Standard / USD 1,000 Premier per journey | confirm / change | Business owner | Confirmed (2026-11-02) |
| 5 | KPI-07 to KPI-09 baselines and targets (card placeholders) | confirm / change | Business owner | Card figures kept as targets in the G1 form (2026-11-02); baselines still to be measured |

### G1 Artifact Updates

- [ ] `docs/requirements/*.yaml` updated with the register above
- [ ] `docs/kpis.yaml` updated (KPI-07 to KPI-09)
- [ ] `evals/thresholds.yaml` updated (CODEOWNERS: Business owner + AI lead)
- [x] Routing / `system.yaml` authority keys: no change
- [ ] Business owner confirms limits, KPIs and thresholds (Use Case Intake, "G1 · Limits and KPIs")
- [ ] `uv run python -m scripts.adlc check G1` passes; brief written to `briefs/G1.md`
