# Use-case card: Travel Claims Assistant

_Generated from `usecase/UC-TRV-CLM-001.yaml` by the G0 intake form. Edit the record, not this card._

| Field | Value |
|---|---|
| Use-case ID | UC-TRV-CLM-001 |
| Sponsor | Helen Ortiz, Head of Claims (Travel) |
| Business owner | Helen Ortiz, Head of Claims (Travel) |
| Product owner | Arjun Rao, Product Owner |
| Risk tier (computed) | **HIGH** |

## Problem
Trip delay and baggage claims are high-volume and low-to-medium value, yet each needs manual document checks, policy look-ups and calculation. Most adjuster time is spent on claims under USD 1,000, and handling times are long for customers waiting on small amounts. Release 0.4.0 (CARD-101): customers whose delayed inbound flight makes them miss a booked onward connection have no cover today. The travel delay benefit (policy section 3) only pays while a departure is delayed by 6 hours or more, so a 3-hour weather delay that strands someone overnight at a hub is declined. These claims arrive as complaints and manual goodwill payments, handled outside the claims system with no audit trail.

## Proposed solution
An agentic backend that receives submitted claims, extracts and cross-checks documents, verifies coverage and facts, calculates the payable amount, and either decides simple, low-value, no-deduction cases or prepares a decision package for L1 / L2 adjusters under a deterministic routing matrix. Release 0.4.0 (CARD-101): add a missed connection benefit (new policy clause 3.7), handled like trip delay and baggage: extract the documents for both legs, verify the inbound delay with the flight-status service, match the clause, calculate the amount and route the claim with the existing routing matrix. Pays reasonable extra costs to reach the booked destination (rebooked or new tickets, accommodation and meals until the next available departure) when a delay of the arriving flight from a covered cause (clause 3.2) makes the customer miss a booked onward departure; the itinerary must allow at least the minimum connection time; same exclusions and compensation offset as travel delay (clause 3.4); limit [USD 500] Standard, [USD 1,000] Premier, per journey. The agents recommend; routing, limits and payment authority stay unchanged.

## Value hypothesis
| Metric | Baseline | Target |
|---|---|---|
| Handling time, delay + baggage | measure from last 6 months | -40% |
| Straight-through processing rate | 0% | 15-25% |
| Adjuster minutes per claim | time study | -30% |
| Payment accuracy (QA sample) | current QA error rate | >= baseline |
| Missed-connection complaints per month (CARD-101) | [40] | [10] |
| Goodwill payments outside the claims system (CARD-101) | [USD 12,000 / month] | [0] |
| Decision time for a missed-connection claim (CARD-101) | [9 days, manual] | [2 days] |

## Scope
- **In:** Trip delay, Baggage loss and delay, Claims in USD, Backend from claim receipt to closure, Missed connection, Standard and Premier plans, flights only (CARD-101, release 0.4.0), Missed-connection documents: bookings or boarding passes for both legs, rebooking, accommodation and meal receipts, Inbound delay verified with the flight-status tool, New customer letter for missed-connection decisions (Compliance to approve)
- **Out:** Medical, Cancellation and interruption, Recovery, Customer front end, Payment rails, Missed connections by rail, ferry or coach, Self-transfer itineraries booked on separate tickets [to be confirmed], Any change to the routing matrix, straight-through limit or approval authority

## Risk tier: HIGH
- affects customer outcomes and takes actions in systems
- processes personal data and decides on its own

## Data classification
| Data | Class | Handling |
|---|---|---|
| Claimant identity | Confidential - PII | Redact in logs and non-essential prompts |
| Bank details | Restricted | Never sent to models; used only by Settlement |
| Travel documents | Confidential | Stored in the document store; extracted fields only in prompts |
| Claim outcome | Confidential | Audit trail retained per the records policy |

Confirmed by: Mei Chen, Data Owner (Claims data)

## Feasibility: Go with conditions
Hybrid design: AI for documents and coverage judgement, deterministic rules for arithmetic, routing and payment release. Model risk is confined to document and judgement steps.

| Condition | Owner | Due |
|---|---|---|
| Golden set of 300-500 decided claims labelled by senior adjusters | Zoe Martin, Data Scientist | 2026-09-28 |
| Data residency and no-training terms confirmed for model hosting | Nadia Haddad, Security Architect | 2026-09-23 |
| Payments instruction API enforces the approval-token check | Kenji Sato, AI Lead | 2026-10-08 |

## RACI
| Activity | Accountable | Responsible | Consulted | Informed |
|---|---|---|---|---|
| Use case and value (G0) | Head of Claims | Product owner | AI lead; Risk; Security | Platform; Compliance |
| Requirements and thresholds (G1) | Head of Claims | Product owner; AI lead | Platform; Security; Risk; SIU | Finance |
| Design and threat model (G2) | Product owner | AI lead; Platform; Security | Head of Claims; Risk; SIU | Compliance |
| Evals (G3) | Product owner | AI lead | Head of Claims; Platform; Risk; SIU | Security |
| RAI assurance (G4) | RAI/Risk | Product owner; AI lead; Compliance | Head of Claims; Security | Platform |
| Release (G5) | Head of Claims | Product owner; AI lead; Platform | Security; Risk; Compliance; Finance | SIU |
| Run and hypercare (G6) | Head of Claims | Product owner; AI lead; Platform | SIU | Security; Risk |
| Improve or retire (G7) | Head of Claims | Product owner; AI lead | Risk; Compliance; SIU | Platform |

---
Record sha256: 0bdf59bc3c5b9326ac2be23e682c9a7155bddf9259edb27a2710f0d21816d171
