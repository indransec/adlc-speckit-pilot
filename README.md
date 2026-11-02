# Travel Claims Assistant (ADLC pilot)

Agentic backend for **travel insurance claims** (MVP scope: **trip delay** and **baggage**),
built on the enterprise ADLC scaffold. This repo is both a working system and the reference
example of every ADLC artifact, filled in for the pilot and **simulated end to end on synthetic data**.

> Status: pilot / simulation. All customers, policies, flights and documents are synthetic.
> Figures in [square brackets] in the business documents are placeholders to be confirmed.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed automatically).

```bash
cp .env.example .env            # optional: add OPENROUTER_API_KEY for live models
make setup                      # uv sync + synthetic data (SQLite, PDFs, FAISS policy index, 45 demo claims)
make dev                        # Phoenix :6006 + FastAPI :8000 + Streamlit :8501
```

Or run them separately: `make phoenix`, `make api`, `make ui`. Then open http://localhost:8501.

Without `OPENROUTER_API_KEY`, a scripted mock model stands in (**mock mode**): it makes the same
kind of tool calls and returns each agent's deterministic fallback, so the system, tests and evals
run offline and reproducibly. With the key, the same code calls Claude Haiku 5.5 (fast tier) and
Claude Sonnet 5.5 (reasoning tier) through OpenRouter (about USD 0.09 and 85 s per claim, measured
on one live claim). You can switch modes at runtime on the ADLC Lifecycle page. Tests always run
in mock mode; `make evals` and `make simulate` use the key when it is set, so run them as
`OPENROUTER_API_KEY= make simulate` to stay offline.

```bash
make test        # 78 unit/routing/HITL/agent-runtime/telemetry tests -> evidence/<release>/test-report.json
make evals       # G3 + G4 eval suites -> eval-report.json, rai-report.json
make simulate    # walk G0 -> G7: run every stage's actions, check each gate, sign with simulated approvers
make status      # gate overview
```

## Technology stack

| Concern | Choice | Where |
|---|---|---|
| Orchestration | LangGraph state graph (fan-out, HITL `interrupt()`, SQLite checkpointer) | `agents/orchestrator/src/claim_graph.py` |
| Agents + tools | LangGraph prebuilt `create_react_agent` per LLM task; read tools attached from `agent.yaml` | `shared/agent_runtime.py` |
| AI gateway | OpenRouter via `langchain-openai`, structured output method per model, tiered models | `shared/llm.py`, `deploy/models.yaml` |
| Vector DB | FAISS + fastembed (`bge-small-en-v1.5`, ONNX) over policy-wording clauses | `shared/rag.py` |
| Relational DB | SQLite (claims, audit, approvals, payments, telemetry) | `shared/db.py` |
| Object store | Local disk (claim PDFs, policy wording) | `shared/object_store.py` |
| Backend | FastAPI (OpenAPI at `/docs`) | `backend/app.py`, `backend/service.py` |
| Front end | Streamlit (process claim, workbench, status, observability, lifecycle) | `frontend/` |
| Observability | OpenTelemetry + OpenInference, **Arize Phoenix** (tokens, latency, cost tracking, tool spans); KPIs/SLOs/alerts in-app | `shared/telemetry.py`, `shared/metrics.py` |
| Guardrails | **Microsoft Presidio** PII redaction, injection heuristics, output policy | `shared/guardrails/`, `guardrails/*.yaml` |
| Documents | reportlab (synthetic PDFs), pypdf (text extraction) | `synthetic/pdfs.py` |
| HITL control | Signed approval token (PyJWT, HS256) checked by the payments tool | `shared/hitl/` |
| Evals | Repo eval runner + pytest + LLM-as-judge | `evals/` |
| Packaging / CI | uv, GitHub Actions | `pyproject.toml`, `.github/workflows/` |

## How it works

```
Streamlit ──HTTP──> FastAPI ──> ClaimsService ──> LangGraph orchestrator (AGT-TRV-ORCH)
                                                   intake -> documents -> [coverage | verification | fraud] -> assessment
                                                   -> route (routing.py, deterministic) -> STP | L1 | investigation | L1+L2 | SIU
                                                   -> approval token -> settlement -> communication
     sub-agents loaded from manifests (registry) ── tools via permission gateway ── LLM via OpenRouter gateway
     SQLite (claims, audit, telemetry) · object store (PDFs) · FAISS (policy clauses) · Phoenix (traces)
```

- **Agents recommend, code routes, humans or row 1 authorise payment.** The routing matrix,
  authority limits and arithmetic are code (ADR-001, `assessment/src/calculator.py`).
- **Manifest-driven.** The orchestrator loads each sub-agent pinned in `system.yaml`
  (`id@version`), with its `agent.yaml`, `prompts/system.md` and `src/agent.py`. Delegation is
  checked against the topology and max depth.
- **Agents are `create_react_agent` graphs** (decision D-12). Each LLM task builds
  `create_react_agent(model, tools, prompt=<system.md>, response_format=<schema>)`. The tools are the
  read operations listed under `attach:` in `agent.yaml`, described by the tool contract. Claim-scoped
  arguments (claim id, policy number) are bound by the platform, so the model cannot query another
  claim. Tool results are PII-redacted and documents are wrapped as untrusted data. Writes (register a
  claim, store extraction, pay, send) stay in agent code. Settlement has no model (D-09).
- **Usage comes from telemetry** (D-13). Tokens and latency are read from the OpenInference LLM spans;
  Phoenix prices them with the `deploy/models.yaml` prices it is given at startup. A local span sink
  keeps the same rows in SQLite (`llm_calls`, `tool_calls`) for budgets, the dashboards and CI.
- **Tool permissions are enforced.** Every tool call goes through `shared/tool_gateway.py`, which
  applies `docs/design/tool-permission-matrix.yaml`. Denials are logged as control events.
- **Human-in-the-loop with LangGraph `interrupt()`.** L1, investigation, L2 and SIU decisions
  resume the checkpointed graph. L1 and L2 must be different users, and L2 must have higher authority.
- **Payments need a signed approval token** (claim, amount, route row, approvers, expiry,
  single use). The kill switch and rollout flags are enforced in the payments tool too.
- **Confidence (ADR-002)** = min(weighted mean of extraction certainty, cross-document
  consistency, verification, clause match and agent agreement; weakest signal + 0.35). It is
  never self-reported.
- **Guardrails.** Document text is wrapped as untrusted data, injections are logged and
  raised as fraud signals, PII is redacted before any model call, bank data never reaches a
  model, and letters come only from Compliance-approved templates.
- **Rollout modes** (`deploy/rollout-plan.md`): shadow (no customer impact), assisted (every claim
  goes to at least L1) and full. The kill switch sends new claims to the manual queue and stops payments.

## Synthetic data and ground truth

`synthetic/scenarios.py` defines 21 scenarios plus a claim-splitting attack. Together they cover
every routing row, every calculation rule (excess, FX, valuables sub-limit, depreciation,
compensation offset, limits), exclusions, duplicates, document mismatches, illegible scans,
fraud, injected documents and PII probing. **Expected outcomes are written by hand** from the
policy wording (`synthetic/policy_wording.md`), not computed by the agents, so evals compare the
system against an independent "adjuster". Simulated adjusters (`synthetic/adjuster_bot.py`)
decide queue items from that ground truth, which produces realistic override rates.

## How the repo maps to the ADLC gates

| Gate | Stage | Evidence (generated where marked *) | Automated checks / actions |
|---|---|---|---|
| G0 | Intake & triage | `docs/use-case-card.md`, `feasibility.md`, `risk-assessment.md`, `raci.md`; in simulation runs `usecase/<id>.yaml`* + risk file + card from the intake form | documents present, risk tier consistent; record complete, classification confirmed, card current, submitted |
| G1 | Requirements | `docs/requirements/`, `docs/kpis.yaml`, `evals/thresholds.yaml` | every REQ has a verification method; routing constants match `system.yaml` |
| G2 | Design | `docs/design/`, `system.yaml`, `agents/*/agent.yaml`, `tools/*/tool.yaml` | manifests validate, matrix covers every tool, threat model T1-T9 |
| CI | Build | `agents/`, `tools/`, `guardrails/`, `shared/` | `make test` -> `test-report.json`* |
| G3 | Test & evals | `evals/` -> `eval-report.json`*, `traceability-matrix.json`*, `perf-report.json`* | thresholds, zero type B -> A, perf at 3x peak |
| G4 | RAI assurance | `docs/rai/`, `evals/rai/` -> `rai-report.json`* | counterfactual fairness, PII, injection, automated red team, letters |
| G5 | Deploy | `deploy/`, `release-record.json`*, `shadow-report.json`*, `drills.json`* | bundle hash (detects drift), shadow agreement, kill switch, rollback |
| G6 | Run & observe | `observability/`, `hypercare-report.json`* | SLOs, alerts, zero control breaches, value KPIs |
| G7 | Improve / retire | `docs/reviews/G7-review-<release>.md`* | override reasons, calibration, drift, value |

Gate records: `evidence/<release>/gate-records.json`* (named approvers and timestamps).
Checklists: `docs/gates/`. Run a gate from the CLI (`python scripts/adlc.py check G3`) or the
**ADLC Lifecycle** page.

Every approval or rejection freezes the artifacts listed for that gate in `adlc/gate_artifacts.yaml`
into the evidence ledger (`shared/ledger.py`): each file is hashed and copied into a
content-addressed store, a manifest records approvers, checks and artifacts, and one hash-chained
event points at it. Later gates re-hash earlier approved files; a change to the authority rules
(`routing.py`, routing keys in `system.yaml`, `evals/thresholds.yaml`, the tool matrix) blocks until
the earlier gate is re-approved. `python scripts/adlc.py verify-ledger` checks the whole chain.

## End-to-end simulation runs

A simulation run plays the use case through the lifecycle with personas (`ops/teams.yaml`) on a
simulated calendar, isolated from the repo's own files and evidence
(plan: `docs/design/ADLC-E2E-Simulation-Plan.docx`).

```bash
make sim-new            # sim/runs/<id>/: workspace copy (own git), data, evidence, clock; becomes current
make sim-api            # API against the current run (APP_ENV=sim)
make sim-ui             # Streamlit: Use case intake, ADLC board, ADLC lifecycle, ...
make sim-clock ARGS="--skip-to-day -45"     # or --pause / --resume / --rate 720 / --skip-days 2
make verify-ledger
```

In the UI, pick a persona under **Acting as**. The Product owner saves and submits the G0 record,
the Data owner confirms the classification, and the Sponsor, Risk Officer and Data owner each sign
G0. The Business owner confirms limits and KPIs for G1, then signs G1 with the AI Lead. A persona
can sign only a role it holds, and one role per gate. The **ADLC board** shows the card move and
every frozen artifact. The same steps are available from the CLI (`python scripts/adlc.py sign G0 --persona hoc`).

### The scripted story (G0 → G5)

`make simulate-e2e` creates a new run and plays `sim/story_e2e.yaml` through the real engine
(about 30 minutes; watch it with `make sim-api` + `make sim-ui`). `ARGS="--interactive"` leaves
gate and change-request decisions to you in the UI; `ARGS="--per-day 20 --min-shadow 100"` makes
the shadow windows smaller; `--run current` resumes a stopped story.

| Sim day | What happens | Expected outcome |
|---|---|---|
| -60 to -30 | Intake, G0, G1, G2 signed by personas | approved once for the use case |
| -21 / -20 | Release 0.3.0-rc1 raises the STP limit in `routing.py` (fault **F1**); CI runs | G3 blocked: routing evals fail, drift against the G1 approval; rejected and closed |
| -16 to -14 | Release 0.3.0 (hides fault **F2**: JPY missing from the policy FX table) | passes G3 and G4: the golden set has no yen receipts |
| -2 to 6 | CR-001 approved by CAB, deployed in shadow, 7 days × 55 live claims | shadow route agreement below 0.99, all misses are JPY baggage claims sent straight through |
| 7 | CAB rejects G5 (CR-002); 0.3.0 superseded | |
| 7 to 15 | Release 0.3.1 restores JPY and adds JPY golden cases; G3, G4, CR-003, shadow again | shadow agreement met; G5 approved by CAB + Head of Claims |

Releases are commits and tags in the run's workspace; change requests, deployments, injected and
detected faults are ledger events; the board shows epic, release and change-request cards.
Fault catalogue: `sim/faults.yaml` (faults fire only when `APP_ENV=sim`).

### Rollout, hypercare and incidents (alerting stack)

```bash
make sim-stack-up       # Prometheus :9090, Alertmanager :9093, GoAlert :8081 (admin/admin123), Mailpit :8025, Grafana :3000
make sim-new            # a fresh run, then in other terminals:
make sim-api            # the API on :8000 serves /metrics and receives Alertmanager webhooks
make sim-ui
make simulate-e2e ARGS="--run current"
```

`scripts/sim_stack.py` generates the Prometheus rules from `observability/alerts.yaml` and the
Alertmanager routes from `ops/teams.yaml`, and seeds GoAlert with every on-call persona (email
verified through Mailpit), one service and escalation policy per team. The API's `/metrics` gauges
are computed over business-time windows (the simulation clock), so a simulated day of traffic raises
and clears real alerts. Each alert opens an incident (Incidents page, board), Sev1 alerts apply their
automatic action at once (kill switch, straight-through off), GoAlert pages the team's primary and
escalates to the secondary after a minute without an acknowledgement.

| Sim day | Fault | Alert → team | What the story checks |
|---|---|---|---|
| 19 (assisted) | F3 coverage model degradation | override_rate_high → ai-oncall | adjusters override; Ana acknowledges; model pin restored |
| 29 | — | phase change CR (Head of Claims) | refused unless the 7-day override rate is under 15% |
| 33 (full) | F4 STP limit raised at runtime | stp_over_authority (Sev1) → ai-oncall | straight-through switched off automatically |
| 37 | F6 gateway latency | decision_latency → sre-oncall | |
| 40 | F7 flight-status outage | tool_error_rate → sre-oncall | per-tool error rate |
| 43 | F8 fraud ring (fraud x25) | fraud_flag_spike → siu | |
| 45 | F9 injection campaign | injection_spike → secops | guardrails block every attempt |
| 47 | F5 token check bypassed | payment_without_token (Sev1) → sre-oncall | kill switch automatically; Ravi misses the page, GoAlert escalates to Elena |
| 51 | F10 fast tier moved to Sonnet | cost_spike → ai-oncall | cost relative to its 7-day baseline |
| 54 | — | — | every incident closed with a root cause once its alert cleared |

### G6 extension, fix release, G7 and the report

| Sim day | What happens |
|---|---|
| 59 | Hypercare report over days 29-59: payments without a valid token (F5) → G6 not ready; the Head of Claims extends hypercare to day 75 with conditions |
| 60 | Fix release 0.3.2 on the emergency path: CI, G3, G4, emergency change (CAB + Head of Claims), **replayed shadow** of the last 330 claims' mix, drills, G5, deployed in full mode |
| 61-75 | Extended hypercare; the report over days 61-75 is clean → G6 approved |
| 76-77 | G7 review generated from telemetry and signed; release 0.4.0 cut with golden cases for the scenarios adjusters overrode most (the loop back to G3) |
| 77 | `sim/report.py` writes `evidence/e2e-report.md`: the detection matrix (each fault's expected control, alert, team, escalation and automatic action vs what the ledger shows), gate decisions, releases, change requests, incidents and KPIs |

The **Simulation** page shows the run, the clock, story progress and the detection matrix. Evidence
packs (manifests, frozen artifacts, the hash-chained ledger and a verification note an auditor can follow
with `sha256sum`) download from the board, the Simulation page, `GET /evidence-pack?release=&gate=` or
`python scripts/adlc.py evidence-pack --release 0.3.2 --gate G5`.

## Folder layout

```
system.yaml            system manifest (topology, pins, release)
docs/                  G0-G2 and RAI documents, gate checklists, decision log, G7 reviews
agents/<name>/         agent.yaml, prompts/, src/agent.py, tests/
tools/<name>/          tool.yaml contract + impl.py (mock enterprise system)
guardrails/            guardrail policies (yaml); implementations in shared/guardrails/
shared/                platform: config, db, llm gateway, registry, tool gateway, telemetry,
                       audit, rag, hitl (approval token), metrics, lifecycle engine
backend/               FastAPI app + claims service (drives the graph)
frontend/              Streamlit pages
synthetic/             scenarios + ground truth, PDF rendering, generator, adjuster bot
evals/                 datasets, suites, judges, thresholds, run_evals.py, perf_test.py
deploy/                environments, model catalog, rollout, rollback, runbook
observability/         SLOs, alerts, dashboard spec
evidence/              generated per release (never hand-edited)
schemas/, scripts/     manifest schemas; CI helpers; adlc.py lifecycle CLI
```
