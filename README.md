# Travel Claims Assistant (ADLC pilot)

Agentic backend for **travel insurance claims** (MVP scope: **trip delay** and **baggage**),
built on the enterprise ADLC scaffold. This repo is both a working skeleton and the
reference example of every ADLC artifact, filled in for the pilot.

> Status: sample/pilot content. Figures in [square brackets] are placeholders to be
> confirmed with the business owner.

## How the repo maps to the ADLC gates

| Gate | Stage | Evidence lives in |
|---|---|---|
| G0 | Intake & triage | `docs/use-case-card.md`, `docs/feasibility.md`, `docs/risk-assessment.md`, `docs/raci.md` |
| G1 | Requirements | `docs/requirements/`, `docs/kpis.yaml`, `evals/thresholds.yaml` |
| G2 | Design | `docs/design/`, `system.yaml`, `agents/*/agent.yaml`, `tools/*/tool.yaml` |
| CI | Build | `agents/`, `tools/`, `guardrails/`, `shared/`, unit + routing tests |
| G3 | Test & evals | `evals/` -> `evidence/<release>/eval-report.json`, `traceability-matrix.json` |
| G4 | RAI assurance | `docs/rai/`, `evals/rai/` -> `evidence/<release>/rai-report.json` |
| G5 | Deploy | `deploy/`, release record, `evidence/<release>/gate-records.json` |
| G6 | Run & observe | `observability/` + central telemetry |
| G7 | Improve / retire | `docs/reviews/` |

Gate checklists: `docs/gates/`.

## Key decisions baked in

- Agent decides alone **only** when: approved aggregate <= USD 1,000, no deduction,
  low fraud risk, confidence >= 0.7 (routing row 1).
- Any policy-norm (type A) deduction -> **1 human level** (L1 approves and releases).
- Any judgement (type B) deduction, or total reduction > **25%** of claimed amount,
  or a full rejection -> **2 human levels** (L1 review, L2 final decision).
- Routing and authority limits are **deterministic code** (`agents/orchestrator/src/routing.py`),
  never left to a prompt (ADR-001).

## Quick start

```bash
python -m unittest discover -s agents/orchestrator/tests -v   # routing rule tests
python scripts/validate_manifests.py                          # manifest checks
python scripts/build_traceability.py                          # REQ -> eval coverage
python scripts/gate_check.py --gate G1                        # evidence completeness
```

## Folder layout

```
system.yaml            system manifest (topology, pins, release)
docs/                  G0-G2 and RAI documents, gate checklists
agents/<name>/         agent.yaml, prompts/, src/, tests/
tools/<name>/tool.yaml tool contracts (scope, side effects)
guardrails/            input/output policies
shared/                telemetry conventions, HITL approval token
evals/                 datasets, suites, judges, thresholds
deploy/                environments, rollback, runbook
observability/         SLOs, alerts, dashboard spec
evidence/              CI-generated per release (never hand-edited)
schemas/, scripts/     manifest schemas and CI helper scripts
.github/workflows/     CI pipelines
```
