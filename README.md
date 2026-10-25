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
| G0 | Intake & triage | `docs/use-case-card.md`, `feasibility.md`, `risk-assessment.md`, `raci.md` | documents present, risk tier consistent |
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
