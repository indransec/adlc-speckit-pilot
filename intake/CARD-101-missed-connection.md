# CARD-101 · Missed connection benefit for travel claims

> Stand-in for a tracker work item (tracker-ui / Azure DevOps later). Captured by the Product owner;
> `/adlc.intake CARD-101` turns it into the G0 use-case record. Figures in [square brackets] are
> placeholders for the Business owner to confirm.

| Field | Value |
|---|---|
| Work item type | Use case (scope change to UC-TRV-CLM-001) |
| State | Submitted |
| Requested by | Arjun Rao, Product owner |
| Sponsor | Helen Ortiz, Head of Claims (Travel) |
| Business owner | Helen Ortiz, Head of Claims (Travel) |
| Target release | 0.4.0 |

## Problem

Customers whose delayed inbound flight makes them miss a booked onward connection have no cover today.
The travel delay benefit (policy section 3) only pays while a departure is delayed by 6 hours or more,
so a 3-hour weather delay that strands someone overnight at a hub is declined. These claims arrive as
complaints and manual goodwill payments, handled outside the claims system with no audit trail.

## Proposed solution

Add a missed connection benefit (new policy clause 3.7) and let the claims assistant handle it like the
existing trip delay and baggage claims: extract the documents for both legs, verify the inbound delay
with the flight-status service, match the clause, calculate the amount and route the claim with the
existing routing matrix. The agents recommend; routing, limits and payment authority stay unchanged.

Proposed cover:
- Pays reasonable extra costs to reach the booked destination when a delay of the arriving flight, from
  a covered cause (clause 3.2), makes the customer miss a booked onward departure: rebooked or new
  tickets, plus accommodation and meals until the next available departure.
- The original itinerary must allow at least the airport's minimum connection time.
- Same exclusions as travel delay (strikes announced before booking, late check-in) and the same
  compensation offset (clause 3.4).
- Limit: [USD 500] Standard plan, [USD 1,000] Premier plan, per journey.

## Value hypothesis

| Metric | Baseline | Target |
|---|---|---|
| Missed-connection complaints per month | [40] | [10] |
| Goodwill payments outside the claims system | [USD 12,000 / month] | [0] |
| Decision time for a missed-connection claim | [9 days, manual] | [2 days] |

## Scope

In scope:
- Missed connection claims on the Standard and Premier plans, flights only
- Documents: bookings or boarding passes for both legs, rebooking receipts, accommodation and meal receipts
- Inbound delay verified with the flight-status tool
- A new customer letter for missed-connection decisions (Compliance to approve)

Out of scope:
- Connections by rail, ferry or coach
- Self-transfer itineraries booked on separate tickets [to be confirmed]
- Any change to the routing matrix, straight-through limit or approval authority

## Data

Same data as the current travel claims (booking references, flight numbers, receipts, names). No new
data categories; bank details stay out of every model as today.

## Notes for the risk assessment

- Customer outcome: yes (pays or declines a benefit)
- Autonomy: unchanged; straight-through only within the existing STP limit
- New failure modes to consider: wrong minimum connection time, inbound delay attributed to the wrong
  leg, double payment with the travel delay benefit for the same journey

## Open questions

1. Self-transfer itineraries on separate tickets: in or out?
2. Does the missed connection benefit share the travel delay limit or have its own?
3. Minimum connection time source: airline published value or a fixed [60 minutes]?
