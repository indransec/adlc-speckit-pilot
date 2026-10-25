# RAI card: Travel Claims Assistant

## Intended use
Assist claims operations for trip delay and baggage claims: prepare decisions and,
for simple low-value cases with no deductions, decide and pay automatically.

## Not intended for
Medical claims, cancellation claims, appeals, complaints, fraud decisions,
any claim outside the routing matrix.

## Decision authority
| Decision | Who |
|---|---|
| Approve in full, <= USD 1,000 aggregate, no deduction, low risk, conf >= 0.7 | Agent |
| Approve with type A deductions only | L1 adjuster |
| Approve > USD 1,000 with no deduction | L1 adjuster |
| Type B deduction or > 25% reduction, or rejection | L1 review, L2 final |
| Fraud flags | SIU |

## Known limitations
- Accuracy lower on handwritten and low-quality images.
- Confidence threshold 0.7 is provisional pending calibration.
- Languages beyond [list] not tested.

## Fairness
Outcome and deduction-rate parity checked across nationality, destination region,
age band and submission channel (see `fairness-test-plan.md`).

## Transparency
Customers are told automation is used (see `transparency-notice.md`) and every
deduction cites the policy clause; appeals always go to a human.

## Monitoring
Override rates, QA sample errors, control breaches, drift (see `observability/`).

## Sign-off
| Role | Name | Date |
|---|---|---|
| RAI / Risk | | |
| Compliance | | |
