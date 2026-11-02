# Quickstart: validate the missed connection benefit

Run from the repository root on `release/0.4.0`, mock models (`OPENROUTER_API_KEY` blank).

## Prerequisites

```bash
make setup          # deps + synthetic data (rebuilds the clause index with clause 3.7)
```

## Scenarios (synthetic, `synthetic/scenarios.py`)

| Scenario | Expected recommendation | Expected route |
|---|---|---|
| Standard, inbound 2h late (weather), connection ≥ MCT, USD 320 receipted | approve USD 320 | row 1 straight-through |
| Same with USD 150 airline compensation | partial USD 170 (3.4, A) | row 4 L1 + L2 (reduction 47% > 25%) |
| Premier, USD 1,300 receipted | partial USD 1,000 (3.6, A) | row 2 L1 (type A only) |
| Inbound 50 min late (weather), booked connection 120 min ≥ LHR 90, onward missed | approve (no minimum inbound delay) | row 1 straight-through |
| Inbound delayed by strike announced before booking | reject (3.3) | row 5 L1 + L2 |
| Valid connection, inbound 20 min late, boarding closed early | investigate (inbound arrived in time per source) | row 6 |
| Connection below MCT | reject (3.7 connection condition) | row 5 L1 + L2 |
| Separate tickets (self-transfer) | reject | row 5 L1 + L2 |
| Flight status shows inbound on time | investigate | row 6 |
| Three-leg itinerary, delay on first leg, inbound leg on time | investigate (delay not on the inbound leg) | row 6 |
| Travel delay USD 300 paid on journey, missed connection USD 350 | partial USD 200 (3.6 shared, A) | row 4 L1 + L2 (reduction 43% > 25%) |
| Same hotel receipt already paid under travel delay (Premier, 120 of 420) | partial USD 300 (7.2, A) | row 4 L1 + L2 (28.6% > 25%) |

## Commands

```bash
make test           # unit + routing tests, incl. "never row 1-3 without verified delay and valid connection"
make evals          # golden + adversarial incl. missed connection metrics (thresholds.yaml)
make perf           # 3x peak with missed connection claims in the mix (REQ-N-001, REQ-N-004)
make traceability   # every REQ-F-032..039, REQ-B-012/013 linked to a test or eval
```

Expected: all thresholds met, including `missed_connection_eligibility_accuracy` ≥ 0.95,
`connection_time_check_accuracy` = 1.0, `inbound_leg_attribution_accuracy` = 1.0,
`duplicate_expense_paid_count` = 0.

## Workbench check

`make api` + `make ui`, process one missed connection claim, and confirm the decision package shows
the connection (booked time, MCT value and source), each line's benefit and clause, and the shared
limit remaining.
