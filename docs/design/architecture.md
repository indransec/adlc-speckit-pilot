# Architecture

## Topology
```
Claim submitted
   |
Orchestrator (deterministic flow + routing rules)
   |-- Intake agent          classify, register, acknowledge
   |-- Document agent        extract, cross-check, chase missing docs
   |-- Coverage agent        policy validity, benefits, exclusions
   |-- Verification agent    flight status and fact checks
   |-- Fraud screening       SIU model score + anomaly flags (flag only)
   |-- Assessment agent      amount (via rules engine), deduction labels A/B, decision package
   |
Routing rules (code) --> row 1 straight-through
                     --> L1 queue / L1 + L2 queue / investigation / SIU
   |
Settlement agent  (only with approval token)
Communication agent (approved templates)
```

## Principles
1. Routing, authority limits and calculations are code, not prompts (ADR-001).
2. Agents recommend; the orchestrator routes; humans or rules authorise payments.
3. Each agent has only the tools in `tool-permission-matrix.yaml`.
4. Every step writes to the audit trail with release ID and agent version.

## Routing matrix
See `routing-matrix.md` (source of truth for `agents/orchestrator/src/routing.py`).
