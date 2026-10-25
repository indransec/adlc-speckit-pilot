# Dashboard spec: Travel Claims Assistant

| Panel | Metric | Source |
|---|---|---|
| Lifecycle | Current stage and gate status | evidence store |
| Routing mix | % claims per row 1-7 | routing decisions |
| Queues | L1 / L2 queue size and age; SLA breaches | workbench |
| Quality | L1 and L2 override rate; L2 reversals of L1; QA sample errors | approvals, QA |
| Controls | Payments without token (target 0); type B mislabels found in QA | payments, QA |
| Evals | Latest eval scores vs thresholds | eval-report.json |
| RAI | Fairness parity; PII/injection events | rai-report.json, telemetry |
| Operations | p95 latency, error rate, cost per claim | OTel traces |
| Value | Handling time, adjuster minutes, STP rate vs baseline | KPIs |
