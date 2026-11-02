# Intake: 0.4.0 missed connection (CARD-101)

- Card: [intake/CARD-101-missed-connection.md](../../intake/CARD-101-missed-connection.md)
- Use case: UC-TRV-CLM-001 (scope change), release 0.4.0, risk tier HIGH (unchanged)
- Draft record: `usecase/UC-TRV-CLM-001.yaml` (+ `.risk.json`, `-card.md`), saved 2026-11-02 by
  `claude` (ledger event `use_case_created`)

## Human steps for G0

1. Product owner (`po`): review the draft on the Use Case Intake page, edit, and submit for G0.
2. Data owner (`dataowner`): confirm the data classification (no new data categories in CARD-101).
3. `/speckit-adlc-gate G0` writes `briefs/G0.md`; Sponsor (`hoc`), Risk (`risk`) and Data owner
   (`dataowner`) sign G0 on the ADLC Lifecycle page.

## Placeholders to confirm (Business owner)

| Item | Placeholder |
|---|---|
| Benefit limit, Standard plan | [USD 500] per journey |
| Benefit limit, Premier plan | [USD 1,000] per journey |
| Missed-connection complaints per month | baseline [40], target [10] |
| Goodwill payments outside the claims system | baseline [USD 12,000 / month], target [0] |
| Decision time for a missed-connection claim | baseline [9 days, manual], target [2 days] |

## Open questions (for G1, Business owner)

1. Self-transfer itineraries on separate tickets: in or out? (draft: out, [to be confirmed])
2. Does the missed connection benefit share the travel delay limit or have its own?
3. Minimum connection time source: airline published value or a fixed [60 minutes]?

## For the risk assessment (Risk, G0)

New failure modes from the card: wrong minimum connection time; inbound delay attributed to the wrong
leg; double payment with the travel delay benefit for the same journey. Autonomy unchanged
(straight-through only within the existing STP limit).

## Note for the Product owner

The three feasibility conditions carried over from the seed have due dates computed from today
(2026-09-23 to 2026-10-08, already past). Update or mark them met in the form before submitting.
