# Routing matrix (MVP)

Deduction types:
- **Type A (policy-norm):** excess/deductible, sub-limits, benefit caps, currency
  conversion at policy rate, offset of compensation already received.
- **Type B (judgement):** items judged ineligible/excluded, missing or untrusted
  receipts, depreciation, partial exclusion, part of delay judged not covered.

Significance net: total reduction > **25%** of claimed amount.

Rules are evaluated **in this order**; the first match wins.

| Order | Row | Condition | Route | Final decision |
|---|---|---|---|---|
| 1 | 7 | Fraud risk medium or high | SIU / senior adjuster | SIU |
| 2 | 6 | Confidence < 0.7, or any type B deduction without evidence | L1 investigates, then re-route | Depends |
| 3 | 5 | Recommendation is full rejection | L1 then L2 | L2 |
| 4 | 4 | Any type B deduction, or reduction > 25% of claimed | L1 then L2 | L2 |
| 5 | 2 | Type A deductions only | L1 approves and releases | L1 |
| 6 | 3 | Approved aggregate > USD 1,000, no deduction | L1 | L1 |
| 7 | 1 | Otherwise (<= USD 1,000, no deduction, low risk, conf >= 0.7) | Straight-through | Agent |

Two-level rules: L1 and L2 different users; L2 higher authority; L1 records a view,
L2 decides; if L1 removes all type B deductions the claim drops to the 1-level path;
payment only after the last required approval.
