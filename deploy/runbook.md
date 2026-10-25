# Runbook

| Alert | Likely cause | First action | Escalate to |
|---|---|---|---|
| Payment without valid token (any) | Control failure | Kill switch ON | Head of Claims, Security (Sev1) |
| Override rate > 20% in a day | Drift, bad release, new claim pattern | Switch to assisted mode | AI lead |
| p95 decision time > 10 min | Model latency, tool outage | Check tool health; scale | Platform |
| Cost per claim > budget | Loops, prompt growth | Check step counts | AI lead |
| Fraud flag spike | Attack or model change | Notify SIU | SIU lead |
| L1/L2 queue SLA breach | Volume | Rebalance adjusters | Claims ops |
