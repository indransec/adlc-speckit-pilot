# G7 periodic review: release 0.2.0 (October 2026)

_Generated from production telemetry and the evidence store by `adlc g7-review`. Simulated data._

| Item | Finding | Action |
|---|---|---|
| Override reasons (top 5) | L1 set_amount: amount differs after evidence review (x5) | Add each reason as a golden eval case |
| Routing mix vs expectation | row 1: 31.5% vs 31.3% expected; row 2: 15.2% vs 20.5% expected; row 3: 5.1% vs 7.2% expected; row 4: 22.5% vs 20.5% expected; row 5: 15.7% vs 12.0% expected; row 6: 7.9% vs 6.0% expected; row 7: 2.2% vs 2.4% expected | Investigate rows deviating > 5 pts |
| Confidence calibration (0.7) | 0.3-0.4: 0.167 agreement (n=6); 0.9-1.0: 1.0 agreement (n=106) | Recalibrate if agreement in >=0.7 bins < 0.95 |
| Drift in claim mix / documents | claim types: baggage_delay 32, baggage_loss 63, delay 92; mean extraction certainty 0.991 | Compare with golden set mix |
| Value realised vs KPIs | STP 0.304, handling 0.46d vs 5.5d, adjuster min 16.4 vs 45, override 0.04, QA accuracy 1.0 | Report to sponsor |
| Authority widening proposal | L1 agreement on type A (row 2): 1.0 over 18 claims | Not yet: need > 98% over >= 20 claims |
| Decision | continue | Head of Claims to confirm at G7 |

## Override reasons

- L1 `set_amount`: amount differs after evidence review (x5)

## Confidence calibration

| Confidence | Claims | Agreement with final decision |
|---|---|---|
| 0.3-0.4 | 6 | 0.167 |
| 0.9-1.0 | 106 | 1.0 |

## Value realised

| KPI | Baseline | Value | Target | Met |
|---|---|---|---|---|
| KPI-01 Average handling time (days, simulated) | 5.5 | 0.46 | -40% | True |
| KPI-02 Straight-through processing rate | 0.0 | 0.304 | 15-25% | True |
| KPI-03 Adjuster minutes per claim (simulated) | 45 | 16.4 | -30% | True |
| KPI-04 Payment accuracy (QA vs ground truth) | 0.95 | 1.0 | >= baseline | True |
| KPI-05 Human override rate | None | 0.04 | < 15% | True |
| KPI-06 Complaints per 1,000 claims | None | None | <= baseline | None |
