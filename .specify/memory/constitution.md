# Travel Claims Assistant Constitution

This constitution governs how features are specified, designed and built for the Travel Claims
Assistant under the Agent Development Lifecycle (ADLC). It sits under the ADLC engine
(`shared/lifecycle.py`, `scripts/adlc.py`, `shared/ledger.py`): where the two differ, the engine's gate
definitions and checks win and this document is amended to match.

## Core Principles

### I. People Sign Every Gate (NON-NEGOTIABLE)

- ADLC gates G0-G7, as defined in `shared/lifecycle.py`, are decided only by the named roles for that
  gate, each signing as their persona (`ops/teams.yaml`) in the UI.
- AI agents (Claude, Spec Kit commands, any automation) prepare artifacts, run checks and actions, and
  write gate briefs. They MUST NOT sign, approve, reject, reset or simulate a gate, and MUST NOT record
  a persona decision (use-case submit, classification confirm, change-request decision).
- When work is ready for a decision, the agent states which gate, which roles must sign, and what they
  should review.

Rationale: accountability for customer outcomes and payments stays with named people.

### II. Gates Run in Order

- Work for a stage starts only when the previous gate is approved (`uv run python scripts/adlc.py
  status`). If it is not, the agent stops and reports what is missing.
- Gate numbering, definitions, checks and actions are fixed (G3 = CI, tests, evals, perf and
  traceability; G4 = RAI assurance). They are not renumbered or reinterpreted.
- Artifacts approved at a gate are frozen by the ledger. Changing one after approval is rework: it goes
  back through that gate with its signers; the drift check blocks later gates until then.

### III. Authority Is Code

- Routing, authority limits and calculations are deterministic code with unit tests for every row
  (`agents/orchestrator/src/routing.py`, ADR-001), never prompts.
- Agents recommend; the orchestrator routes; people or rules authorise payments. Settlement requires a
  signed approval token (ADR-003) and has no model in its path.
- `agents/orchestrator/src/routing.py`, `evals/thresholds.yaml`, routing keys in `system.yaml` and
  `docs/design/tool-permission-matrix.yaml` change only at G1 or G2, with their CODEOWNERS approving.
  A feature that needs such a change says so explicitly in its spec or plan.

Rationale: a model error or prompt injection must not be able to bypass human review or limits.

### IV. Traceable Requirements and Evaluation-Gated Release

- Every requirement has an ID (`REQ-F-`, `REQ-N-`, `REQ-B-`) and a verification method. Tests, eval
  datasets and eval suites reference the requirement IDs they verify; the traceability matrix MUST
  have no unverified requirement.
- G3 requires: unit and routing tests green in CI, versioned datasets, every threshold in
  `evals/thresholds.yaml` met (including zero type B -> A mislabels and zero payments without a
  token), and a performance test at 3x peak.
- G4 requires: counterfactual fairness parity, PII and injection suites passing, red-team findings
  fixed, an updated RAI card and transparency notice, and customer letters approved by Compliance.

### V. Human Ground Truth, Generated Evidence

- AI may propose golden and adversarial cases. A person labels them before they count as ground truth.
- `evidence/**` is produced by the engine (tests, evals, actions, ledger) and is never hand-edited.
- The hash-chained ledger MUST verify (`scripts/adlc.py verify-ledger`) before any gate decision.

### VI. Least Privilege and Data Protection

- Each agent uses only the tools listed for it in the tool permission matrix. Model-attached tools are
  read-only; side effects stay in deterministic code.
- PII is redacted before text reaches a model; bank details never reach a model; documents are wrapped
  as untrusted data.
- Every step writes to the audit trail with release ID and agent version.
- The threat model covers OWASP agentic risks and document injection, and is updated at G2 for every
  new tool, data flow or document type.

### VII. Simplicity

- Prompts, templates and settings first; engine or platform code only when a step is blocked.
- No new data category, external dependency or model without a recorded G2 decision.
- Reuse the existing agents, routing matrix and workbench before adding new ones.

## Roles and Gate Sign-off

Signers are the roles in `shared/lifecycle.py`; Spec Kit artifacts live in `specs/<release>-<slug>/`.

| Gate | Stage | Signers | Spec Kit / repo input |
|---|---|---|---|
| G0 | Intake & triage | Sponsor (Head of Claims), Risk, Data owner | Use-case card (`intake/`), feasibility, risk assessment, RACI; `briefs/G0.md` |
| G1 | Requirements | Business owner (Head of Claims), AI lead | `spec.md` (requirements, KPIs, thresholds, autonomy/HITL); `briefs/G1.md` |
| G2 | Design | Architecture, Security | `plan.md` + companions (ADRs, tool matrix, threat model, data flows); `briefs/G2.md` |
| G3 | Test & evals | AI lead, Product owner | `tasks.md`, code on `release/<version>` with PR and code-owner review, eval evidence; `briefs/G3.md` |
| G4 | RAI assurance | RAI/Risk, Compliance | RAI suites, RAI card, transparency notice, letters; `briefs/G4.md` |
| G5 | Deploy | CAB, Head of Claims | Release bundle, shadow results, rollback and kill-switch drills; `briefs/G5.md` |
| G6 | Run & observe (hypercare) | Head of Claims | Hypercare SLOs, controls, alerts, QA, dashboard |
| G7 | Improve / retire | Head of Claims, AI lead | Override, drift/calibration and value review |

Accountability for each activity follows `docs/raci.md`.

## Development Workflow

- One Spec Kit feature folder per release: `specs/<release>-<slug>/`.
- Code for a release is built on `release/<version>` and merged by pull request with CODEOWNERS review.
- Models run in deterministic mock mode by default (decision D-10). Live models cost money and are used
  only when a person asks for them.
- Stack as in `pyproject.toml` and decision D-08: Python 3.12 with `uv`, LangGraph, FastAPI, Streamlit,
  FAISS, SQLite, Presidio, Arize Phoenix.
- `make test`, `make evals`, `make perf` and `make traceability` produce the G3 evidence; results are
  never edited by hand.

## Governance

- This constitution sits under the ADLC engine. Every `/speckit-plan` Constitution Check and every gate
  brief checks compliance with it; a violation is either fixed or recorded as a justified exception in
  the plan's Complexity Tracking and raised to the gate's signers.
- Amendments need the AI lead and the Head of Claims, and are recorded in `docs/decision-log.md` with
  the new version.
- Versioning: MAJOR for removing or redefining a principle or gate rule; MINOR for a new principle or
  section or materially expanded guidance; PATCH for wording and clarifications.
- Runtime guidance for Claude is in `CLAUDE.md`; enforcement for Claude is in `.claude/settings.json`
  and `.claude/hooks/adlc_guard.py`.

**Version**: 1.0.0 | **Ratified**: 2026-11-02 | **Last Amended**: 2026-11-02
