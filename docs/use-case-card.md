# Use-case card: Travel Claims Assistant

| Field | Value |
|---|---|
| Use-case ID | UC-TRV-CLM-001 |
| Business owner | Head of Claims, Travel |
| Product owner | [Name] |
| Date | 2026-10-26 |
| ADLC stage | G0 Intake & triage |

## Problem
Trip delay and baggage claims are high-volume and low-to-medium value, yet each needs
manual document checks, policy look-ups and calculation. Average handling time is
[X] days and about [Y]% of adjuster time is spent on claims under USD 1,000.

## Proposed solution
An agentic backend that receives submitted claims, extracts and cross-checks documents,
verifies coverage and facts, calculates the payable amount, and either decides
(simple, low-value, no-deduction cases) or prepares a decision package for human
adjusters (L1 / L2) under a defined routing matrix.

## Value hypothesis
- Handling time for delay and baggage claims reduced by **[40]%**.
- **[15-25]%** of claims processed straight-through at MVP (conservative routing).
- Adjuster minutes per claim reduced by **[30]%** via pre-built decision packages.
- Payment accuracy at or above current baseline.

## Scope
- In: trip delay, baggage loss/delay; USD; backend from claim receipt to closure.
- Out (MVP): medical, cancellation/interruption, missed connection, recovery,
  customer front end, payment rails.

## Initial risk tier: HIGH
Releases payments, affects customer financial outcomes, processes personal data.
See `risk-assessment.md`.

## Feasibility verdict
Go with conditions. See `feasibility.md`.

## Sign-off
| Role | Name | Decision | Date |
|---|---|---|---|
| Sponsor | | | |
| Risk | | | |
| Data owner | | | |
