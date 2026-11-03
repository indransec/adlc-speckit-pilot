# ADLC lifecycle driven by LangGraph

**Status**: proposal (not implemented) · **Date**: 2026-11-03 · **Basis**: release 0.4.0 Spec Kit pilot
(`docs/pilot-plan.md`, `specs/0.4.0-missed-connection/`) · **Diagram**: `adlc-langgraph-lifecycle.drawio`
(page 1 end-to-end lifecycle, page 2 technology and trust zones, page 3 gate subgraph)

In the pilot a person drove each stage by typing Spec Kit commands in a Claude Code session. This design
keeps everything that worked (gate definitions, engine, ledger, Spec Kit preset, persona sign-off) and
adds a LangGraph flow that runs the lifecycle G0 → G7: it starts work, waits for people and CI, routes
rejections to rework and keeps going, without holding any authority itself.

---

## 1. Division of responsibility

| Layer | Owns | Never does |
|---|---|---|
| **ADLC engine** (`shared/lifecycle.py`, `shared/ledger.py`, `shared/usecase.py`, FastAPI `backend/app.py`) | Gate definitions G0–G7, checks, approver roles, signatures, hash-chained ledger, freeze and drift blocking | Write documents or code |
| **LangGraph lifecycle flow** (new, `lifecycle_flow/`) | Sequencing, waiting (interrupts), retries, rework routing, notifications, budgets | Sign, approve, reset, submit or confirm anything; it only reads gate state |
| **Claude Code + Spec Kit worker** (Claude Agent SDK, headless) | Specs, plans, tasks, code, fixes, gate briefs, rework notes, on the release branch | Run without the repo's `.claude/` settings and guard hook; edit authority files or `evidence/` |
| **CI** (GitHub Actions) | Tests, evals, RAI suites, perf, traceability: the only source of G3/G4 evidence | Decide a gate |
| **People** (ADLC UI under SSO, PR review) | Submit, confirm, answer clarifications, label ground truth, amend, sign | — |

Rule carried over from the pilot: **the engine decides, people sign, the flow and Claude only do work.**

---

## 2. Architecture

```
 Jira/ADO intake ─► LangGraph lifecycle flow (thread = use_case:release, Postgres checkpointer)
                       │ read status, run actions, draft as "claude"        ▲ Command(resume)
                       ▼                                                    │
                    ADLC engine API ─► ledger ─► outbox ─────────────────► event relay ◄── GitHub webhooks
                       ▲                                                    (workflow_run, pull_request_review, push)
                       │ decisions: people only, in the UI under SSO
                       │
                    Claude Code worker (Agent SDK, git worktree per thread)
                       └─► /speckit-* and /speckit-adlc-* commands ─► commits, PR, briefs
```

| Zone | Components |
|---|---|
| People and channels | ADLC UI (Streamlit; personas mapped to IdP groups), GitHub PR review, Slack/Teams notifications, labelling |
| Automation plane (no authority) | LangGraph flow, Postgres checkpointer, event relay, read-and-draft engine client, Claude Agent SDK worker, Spec Kit v1.2.5 + `adlc` preset, constitution |
| Authority plane | Engine API, lifecycle engine, gate profiles, gate store (Postgres), evidence ledger and content store, outbox, identity and roles |
| Delivery | Repository, GitHub Actions (`test`, `evals`, `gate-check`, `adlc-gates`, `release`), eval harness, release workflow |
| Product runtime | Product agents (LangGraph), tool gateway, model gateway, HITL workbench |
| Observability | OpenTelemetry / OpenInference → Phoenix, Prometheus / Grafana, GoAlert |

---

## 3. The lifecycle graph

### 3.1 Threads and state

- One LangGraph thread per use case and release: `thread_id = "<use_case>:<release>"`.
- The main graph has one subgraph per gate, G0 → G7, in the order defined in `shared/lifecycle.py`.
- Checkpointer: Postgres (LangGraph `PostgresSaver`).
- **Graph state is a cache.** Every resume starts with a `sync` node that re-reads the engine. A thread can be
  dropped and rebuilt from engine state, which is what makes flow upgrades safe while threads wait for weeks.

```python
class LifecycleState(TypedDict, total=False):
    use_case: str; release: str; gate: str
    gate_status: dict            # cache of the engine's record; refreshed by sync
    worktree: str; branch: str; pr: int | None; head_sha: str
    rework_round: int; ci_attempts: int; blocked_task: dict | None
    flow_version: str
```

### 3.2 Gate subgraph (same pattern at every gate)

```
sync ─► precheck (previous gate approved?) ─► author (Claude) ─► ⏸ human input / review and amend
     ─► ⏸ ready for sign-off ─► check (engine) ─► fix loop (max N, then escalate) ─► brief
     ─► verify-ledger ─► ⏸ await signatures ─► sync ─► approved → next gate
                                                    └► rejected / drift → triage → rework
```

- `⏸` nodes call `interrupt()`. The event relay resumes them with `Command(resume=...)`.
- The resume payload is only a wake-up hint; the node re-reads the engine (or GitHub) before routing.
- An SLA timer on each wait sends a reminder, then escalates to the gate owner.

### 3.3 Per-gate content

| Gate | Claude worker | Human inputs (interrupts) | Signers (engine) |
|---|---|---|---|
| G0 | `/speckit-adlc-intake <card>` (drafts as `claude`) | PO submits; Data owner confirms classification | Sponsor, Risk, Data owner |
| G1 | `/speckit-specify`, `/speckit-checklist`, `/speckit-clarify` | Business owner answers clarifications and confirms limits/KPIs | Business owner, AI lead |
| G2 | `/speckit-plan` | — | Architecture, Security |
| G3 | `/speckit-tasks`, `/speckit-analyze`, `/speckit-implement` (see §6) | `[HUMAN]` tasks (reference data, golden/adversarial labels); PR code-owner review | AI lead, Product owner |
| G4 | RAI suites, letter templates, rework on rejection | — | RAI/Risk, Compliance |
| G5 | Release record, runbook, change request, shadow run | CAB decides the change request | CAB, Head of business |
| G6 | Hypercare report (scheduled) | — | Head of business |
| G7 | Override, drift and value review (scheduled) → next intake or retirement | — | Head of business, AI lead |

Every gate ends with `/speckit-adlc-gate <G>` writing `specs/<release>-<slug>/briefs/G*.md` for the signers.

### 3.4 Events

| Event | Source | Resumes |
|---|---|---|
| `use_case_submitted`, `classification_confirmed`, `g1_confirmed` | Engine ledger | G0 / G1 human-input waits |
| `ready_for_signoff` | Engine (UI button) | review-and-amend wait |
| `gate_signed`, `gate_approved`, `gate_rejected` | Engine ledger | signature wait |
| `cr_decided` | Engine | G5 CAB wait |
| `workflow_run` completed | GitHub | CI wait (G3/G4) |
| `pull_request_review`, `push` | GitHub | rework triage; evidence-stale handling |
| task done (`[HUMAN]` task) | UI / PR | G3 human-task wait |

The engine gets an **outbox**: every ledger append that is a lifecycle event is also published there; the relay
consumes it. No polling.

---

## 4. Claude Code worker

- **Runtime**: Claude Agent SDK, headless, one git worktree per thread on `release/<x>`.
- **Settings**: `setting_sources=["project"]`, so `.claude/settings.json` deny/ask rules and
  `.claude/hooks/adlc_guard.py` apply exactly as in the pilot.
- **Identity**: drafts saved through the engine as persona `claude` (no roles); commits as `claude`.
- **Scope per call**: one Spec Kit command, or one `/speckit-implement` phase; tools limited per node.
- **Output**: commits, PR updates, briefs and `--json` summaries the flow can route on. Not graph state.
- **Progress record**: `tasks.md` checkboxes. `/speckit-implement` skips `[X]` tasks, so a retried node
  continues where it stopped.
- **Budget**: max turns per call; the flow checks spend before each call (§9).
- **Never**: force-push, overwrite an approver's edit (it comments or suggests instead), write `evidence/`.

---

## 5. Human amendments before approval

Rule: **until gate Gn is approved, anyone holding an approver role for Gn may amend Gn's artifacts.**
After approval the artifacts are frozen by the engine; changes go through rework at that gate.

No additional audit trail is kept beyond what the engine already does (freeze at decision, drift detection)
and git history.

| Gate | May amend (besides Claude's draft) | Artifacts |
|---|---|---|
| G0 | Sponsor, Risk, Data owner | Intake record, use-case card, feasibility, risk assessment, RACI |
| G1 | Business owner, AI lead | `spec.md`, requirements YAML, `kpis.yaml`, `thresholds.yaml`, routing rules |
| G2 | Architecture, Security | `plan.md`, architecture, ADRs, threat model, tool permission matrix, data flows |
| G3 | AI lead, Product owner | `tasks.md`, code and prompts on the release branch, datasets |
| G4 | RAI/Risk, Compliance | RAI card, transparency notice, letter templates |
| G5 | CAB, Head of business | Runbook, rollback plan, change request |

Enforcement:

- Repository files: CODEOWNERS per gate's artifact paths → that gate's approvers' GitHub teams, generated from
  `adlc/gate_artifacts.yaml` and a `github:` login per persona in `ops/teams.yaml`; branch protection requires
  their review.
- UI forms: the engine's role check extended to "holds an approver role for this gate, gate not yet approved".
- Flow: after `author` the flow waits for review and amendment; an approver clicks **Ready for sign-off**; the
  flow then checks and writes the brief from the amended content. Edits during the signature wait send the flow
  back to `check`.

Open decisions:

1. Whether an amendment after the first signature clears the signatures already given (recommended, so no
   approval rests on content a signer did not see).
2. Whether engineers get a non-signing "editor" role at G3, or approvers ask Claude for code changes.

---

## 6. G3 (build, unit tests, evals) in detail

```
sync ─► workspace ─► tasks ─► analyze ─┬─ clean ─► open_draft_pr ─► implement_phase ◄──────┐
                                       └─ issue in spec/plan ─► rework at G1/G2             │
                     implement_phase ─┬─ [HUMAN] task ─► ⏸ await_human ─────────────────────┤
                                      ├─ more phases ──────────────────────────────────────┘
                                      └─ done ─► push ─► ⏸ await_ci ─┬─ pass ─► ⏸ await_ready ─► brief ─► ⏸ await_signatures
                                                                     ├─ fail < N ─► fix_ci ─► push
                                                                     └─ fail = N ─► escalate
     await_signatures ─┬─ approved ─► G4
                       ├─ rejected ─► triage ─► implement_phase
                       └─ new commit ─► await_ci (evidence stale)
```

| Node | Does | Waits for |
|---|---|---|
| `tasks` | `/speckit-tasks` → `tasks.md` (incl. "Phase ADLC: Evidence and Gate Readiness") | — |
| `analyze` | `/speckit-analyze --json`; code-level findings fixed, spec/plan findings routed to rework at G1/G2 | — |
| `open_draft_pr` | Draft PR `release/<x>` → `main` from the start, so CI runs on every push | — |
| `implement_phase` | `/speckit-implement`, one phase per call, tests first; local `make test` for Claude's feedback only | — |
| `await_human` | `[HUMAN: persona]` task, e.g. PO confirms reference data, adjuster labels golden cases | task-done event |
| `await_ci` | — | `workflow_run` completed |
| `check` | Engine G3 checks on CI evidence stamped with the PR head SHA | — |
| `fix_ci` | Claude reads failing CI logs and check details, pushes a fix | — |
| `await_ready` | Approvers review/amend; code-owner review | `ready_for_signoff` |
| `brief` | `/speckit-adlc-gate G3` (PR, head SHA, CI run, checks) | — |
| `await_signatures` | — | `gate_signed` / `gate_rejected` / `push` |

Changes from the pilot:

- **CI is the only source of evidence.** In the pilot the reports came from `make test` / `make evals` on the
  machine where Claude wrote the code. Here CI on the PR head writes them and uploads them to the engine's
  evidence store with `commit_sha` and `ci_run_id`.
- **Evidence is tied to a commit.** A G3 check requires the evidence SHA to equal the PR head; any new commit
  needs a new CI run before anyone can sign.
- **The PR opens first.** In the pilot TA05 (open the PR) was still open when G3 was signed, and PR #1 merged
  before G3 was approved; the `adlc-gates` required check stays.
- **Live-model evals** are a per-risk-tier policy (e.g. mock on every push, one live run on the commit to be
  signed, within budget).

---

## 7. Rejection and rework

### 7.1 Sources

| Source | Lands in | Handled by |
|---|---|---|
| CI / eval / RAI failure | GitHub status → engine checks fail | Automated fix loop (max N), then escalation |
| PR "changes requested" | GitHub review | Rework loop, code scope |
| Gate rejected by a signer | Engine `gate_rejected` | Rework loop, gate scope |

### 7.2 Relaying a GitHub PR review to the engine

A PR review is **evidence, not a gate decision**.

```
reviewer "Request changes" ─► GitHub pull_request_review ─► Actions workflow (OIDC token)
  ─► engine POST /lifecycle/pr-reviews (record only)
       verify OIDC (repo, workflow, ref) · map PR → use_case:release (branch / label)
       map GitHub login → persona · fetch review + comments from the GitHub API
       ledger.append("pr_review", ...)
  ─► outbox ─► relay ─► thread resumes ─► sync ─► triage
```

- New G3 check `c_pr_review`: code owners approved and no outstanding "changes requested", read from GitHub at
  check time (pull), so lost or duplicated events cannot make a gate look ready.
- Transport: GitHub Actions + OIDC now; a GitHub App webhook to the relay when many repos join.

### 7.3 The loop

1. **sync**: read the gate record and signers' notes from the engine; read open PR review threads.
2. **triage**: classify each finding:

   | Kind | Meaning | Next |
   |---|---|---|
   | `in_scope` | Code, tests, prompts, letters, eval cases on the release branch | Claude fixes |
   | `rework_earlier_gate` | Touches a file an earlier gate froze (`adlc/gate_artifacts.yaml`) | `/speckit-adlc-rework` for that gate; current gate waits |
   | `needs_owner` | Authority file (`routing.py`, `thresholds.yaml`, `system.yaml` routing keys, tool permission matrix) | Proposal to G1/G2 owners only |
   | `needs_labels` | Golden / adversarial data | Labelling task for people |
   | `clarify` | No artifact identified, or Claude disagrees | Question to the reviewer; Claude never dismisses a comment |

3. **rework plan**: `/speckit-adlc-rework` writes `briefs/rework-<G>-<date>-r<n>.md`; `/speckit-converge`
   appends "Phase Rework R<n>" tasks to `tasks.md`; `/speckit-analyze` checks consistency.
4. **ack** (default at G4): the rejecting signer confirms the plan matches the request.
5. **implement** on the same branch and PR, one commit per finding (`R2-F3: ...`); reply on each PR thread with
   the commit SHA; reviewers resolve their own threads.
6. **verify**: CI reruns; evidence regenerated; engine check; fix loop.
7. **drift**: if a G4 fix touched code covered by G3's frozen evidence, G3 drift is flagged and its signers are
   informed.
8. **re-brief** with a "Response to rejection" section (finding → change → evidence).
9. **signatures**: re-request PR review; same signers sign a fresh round (the engine accepts a new round on a
   rejected gate without a reset).
10. **loop guard**: after a maximum number of rounds (e.g. 2) or the budget, escalate to the gate owner and AI lead.

### 7.4 Structured rejections (proposed engine change)

Today a rejection stores one free-text `notes` string, held only in the frozen manifest (the `gate_rejected`
ledger event carries no notes). Proposed: `sign(..., required_changes=[{id, text, blocking, paths, area}])`,
stored in the signature, the manifest and the decision event, and a matching field in the UI rejection form.
Each finding then has an ID that the rework plan, commits, tests and the round-2 brief refer to.
Free-text rejections keep working: each note becomes one finding.

---

## 8. Controls

1. Only people sign, approve, reset, submit or confirm, in the UI under SSO. The engine refuses role-less
   approvals (`adlc_governed: true`).
2. The flow's engine client exposes read and draft routes only; decision routes are not in it.
3. The Claude worker runs with project settings: deny rules plus `adlc_guard.py`; drafts as `claude` (no roles).
4. Gate order is enforced by the engine and re-checked by the `adlc` preset's previous-gate blocks.
5. One person, one role per gate.
6. Approved artifacts are frozen in the hash-chained ledger; drift on authority files blocks later gates.
7. Evidence comes from CI and engine actions only; `verify-ledger` runs before every signature wait.
8. Graph state is a cache; events are hints; every resume re-reads the engine.
9. `lifecycle_flow/`, gate profiles and the preset are under CODEOWNERS and change through platform governance.

The guard hook is defence in depth (it has known false positives and does not cover direct database writes);
enforcement lives in the engine.

---

## 9. Cost and token tracking per gate

Use the existing observability primitives (`shared/telemetry.py`: OpenInference token counts, priced with
`deploy/models.yaml`, same prices registered in Phoenix). Tag every LLM call; aggregate by tag. No cost is
written by hand.

| Source | Native telemetry |
|---|---|
| Claude Code worker | Claude Code OpenTelemetry metrics `claude_code.token.usage`, `claude_code.cost.usage`; Agent SDK result `total_cost_usd`, `usage` |
| Flow's own LLM calls | OpenInference LangChain instrumentation → Phoenix |
| CI evals (live models) | `shared/llm.py` → OpenInference → Phoenix |
| Product agents (shadow, hypercare) | Existing telemetry sink (`llm_calls`), reported as run cost |

Attributes on every span and metric: `adlc.use_case`, `adlc.release`, `adlc.gate`, `adlc.node`, `adlc.round`,
`adlc.actor`. For the worker the flow sets `CLAUDE_CODE_ENABLE_TELEMETRY=1`, the OTLP exporter and
`OTEL_RESOURCE_ATTRIBUTES` per call; CI sets them from the PR; the product runtime from the deployment phase.

Pipeline: OTel Collector → Prometheus (sums by `adlc_*` labels) and Phoenix (drill-down) → engine read route
`GET /board/costs` → ADLC board per gate: tokens (input / output / cached), cost split build / evals / run,
rework share (round ≥ 2), budget vs actual, Phoenix link. The gate brief shows a generated cost line.

Budgets per gate and release in the gate profile (by risk tier); the flow checks spend before each worker call
and stops and escalates at the limit; Grafana alerts at 80 % and 100 %.

Caveats: the 0.4.0 pilot ran Claude interactively and evals on mock models, so it has nothing to attribute;
Claude Code's cost metric is an API-price estimate.

---

## 10. Engine and repo changes required

| # | Change | Area | Approval needed |
|---|---|---|---|
| 1 | Outbox on lifecycle ledger events; event relay | Engine, new service | Platform |
| 2 | Read-and-draft engine client for the flow; service identity as `claude` | Flow | Platform, Security (G2-style review) |
| 3 | Engine keyed by `use_case + release` (today one current item, `release()` from `system.yaml`, `.specify/feature.json`) | Engine | Platform (largest change) |
| 4 | Gate profiles as versioned YAML per risk tier (from `GATES`); same order and actions | Engine | Governance bodies |
| 5 | Gate store SQLite → Postgres; separate schema for the checkpointer | Engine | Platform |
| 6 | CI evidence upload route (OIDC) with `commit_sha`, `ci_run_id`; G3 check "evidence SHA = PR head" | Engine, CI | G3 owners (AI lead, PO) |
| 7 | `POST /lifecycle/pr-reviews`, `pr_review` event, `c_pr_review` check, `adlc-pr-review.yml` | Engine, CI | G3 owners |
| 8 | `required_changes` on rejections and conditional approvals; UI form field | Engine, UI | Platform |
| 9 | Approver-only amendment until approval; CODEOWNERS generated from `gate_artifacts.yaml`; "Ready for sign-off" | Engine, UI, repo | Governance bodies |
| 10 | `--json` output for `/speckit-adlc-gate` and `-rework`; "Response to rejection" in the brief template | `adlc` preset | Platform |
| 11 | `adlc.*` telemetry attributes, Claude prices in `deploy/models.yaml`, `/board/costs` | Observability, engine | Platform |
| 12 | `lifecycle_flow/` (main graph, gate subgraphs, worker, relay client) | New | Platform; under CODEOWNERS |

Gate definitions and actions in `shared/lifecycle.py` are not renumbered or changed; new checks (6, 7) are
added through the owners of the gate they belong to.

---

## 11. Findings from the 0.4.0 pilot that shaped this design

- The G1 checklist found 7 real gaps → checklist and clarify stay as author steps with human answers.
- `/speckit-plan` found a misrouted scenario (US1-2) → rework back to the G1 signers, not a silent fix.
- `/speckit-analyze` needs `tasks.md`, so it runs at G3, not G2.
- PR #1 merged before G3 was signed → `adlc-gates` required check; in the flow the PR opens first.
- G3 evidence came from the authoring machine → CI-only evidence tied to the PR head SHA.
- Compliance's G4 rejection was recorded three times (ledger events 22–24): the engine accepts a repeated
  rejection of a gate that is already rejected → make rejection idempotent, and merge duplicates in triage.
- The G4 rework regenerated G3's `test-report.json` and `eval-report.json` after G3 approval (drift flagged,
  not blocking), while the hand-written rework note said G0–G3 were unchanged → triage reports cross-gate effects.
- Rejection notes are only in the frozen manifest, not on the ledger event → structured `required_changes`.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Flow upgraded while threads wait for weeks | State is a cache; `sync` rebuilds from the engine; `flow_version` in state |
| Two sources of truth for gate state | Flow never writes gate state; decisions only in the engine |
| Automation hides decisions from signers | Briefs and rework notes go to signers in the UI; the flow notifies, never summarises a decision for anyone |
| Rework loops burn budget | Max rounds and per-gate budgets, then escalation to people |
| Event loss or duplication | Pull-based checks at signing time; idempotent nodes |
| Signers slower than automation | SLA timers, reminders, escalation; cycle time per gate on the board |
| Cost of live models | Mock by default; live runs per risk-tier policy within budget |

---

## 13. Migration plan

1. Outbox + read-and-draft engine client (no behaviour change).
2. G0–G2 subgraphs; run CARD-102 through them with people signing in the UI as today.
3. G3–G5 with draft PR, CI evidence upload, PR-review relay and change requests; multi-use-case engine.
4. Rework loop with structured rejections and triage; cost tagging and board.
5. Gate profiles in YAML; second, different use case with another team; then rollout across teams.

---

## References

- Engine: `shared/lifecycle.py` (`GATES`, `sign`, `approve`), `shared/ledger.py` (`freeze`, `drift`, `verify`),
  `shared/usecase.py`, `shared/board.py`, `backend/app.py`
- Frozen artifacts per gate: `adlc/gate_artifacts.yaml`
- Spec Kit preset: `adlc/speckit-preset/` (commands `/speckit-adlc-intake`, `-gate`, `-status`, `-rework`)
- Guardrails: `.claude/settings.json`, `.claude/hooks/adlc_guard.py`, `CLAUDE.md`
- CI: `.github/workflows/test.yml`, `evals.yml`, `gate-check.yml`, `adlc-gates.yml`, `release.yml`
- Telemetry: `shared/telemetry.py`, `deploy/models.yaml`, `observability/`
- Pilot record: `docs/pilot-plan.md`, `specs/0.4.0-missed-connection/briefs/`
