# ADLC Spec Kit pilot: plan and progress

Goal: show that the ADLC (G0 → G7 exactly as built in `shared/lifecycle.py`) can be driven by Claude
with GitHub Spec Kit, with named people reviewing and signing every gate, using light customisation
only (constitution, templates, a few commands, settings). Release 0.4.0 adds a missed connection
benefit (`intake/CARD-101-missed-connection.md`).

## Division of work

| Part | Owner |
|---|---|
| Authoring G1 requirements, G2 design, G3 build plan and code | Spec Kit skills run by Claude |
| Gate checks, actions, sign-off, evidence ledger, drift blocking | Existing engine: `scripts/adlc.py`, `shared/lifecycle.py`, `shared/ledger.py` |
| Review and sign-off | People, as personas from `ops/teams.yaml`, in the UI (Use Case Intake and ADLC Lifecycle pages) |
| Glue | `/speckit-adlc-*` commands (in the `adlc` preset), a gate-brief template, `.claude/settings.json` permissions |

One Spec Kit feature folder per release: `specs/<release>-<slug>/` (`spec.md` = G1, `plan.md` and
companions = G2, `tasks.md` = build plan, `briefs/G*.md` = gate briefs for signers).

## Decisions taken

- Pilot runs in **governed dev mode**, not a simulation run: the repo itself is the governed project,
  so Spec Kit, Claude and the gate checks see the same files. Real time clock.
- Gate definitions and actions stay as built (G3 = CI + unit tests + evals + perf + traceability,
  G4 = RAI). No renumbering.
- Tracker-ui comes later; the use-case card is a file in `intake/` for now.
- Spec Kit pinned at **v1.2.5**.

## Steps

| # | Step | Status |
|---|---|---|
| 0 | Repo created from baseline, setup, tests green (105) | done 2026-11-02 |
| 1 | Governed mode (`adlc_governed`), release 0.4.0, CARD-101; smoke test of intake + persona sign-off | done 2026-11-02 (`2a39001`) |
| 2 | Spec Kit v1.2.5 installed unmodified (`specify init --here --integration claude`) | done 2026-11-02 (`1d18f7c`) |
| 3 | Guardrails in `.claude/settings.json` + `.claude/hooks/adlc_guard.py` (below); tested that Claude cannot sign | done 2026-11-02 |
| 4 | Constitution via `/speckit-constitution` with the ADLC rules | done 2026-11-02 (v1.0.0, decision D-15) |
| 5 | ADLC sections in spec / plan / tasks templates, gate-brief template, as the local `adlc` preset (below) | done 2026-11-02 |
| 6 | `/speckit-adlc-intake`, `-gate <G>`, `-status`, `-rework` and the previous-gate check in the Spec Kit stage commands, all in the `adlc` preset (below) | done 2026-11-02 |
| 7 | Run G0 → G2 for 0.4.0 (user signs as personas) | next |
| 8 | Run G3 → G5 (release branch + PR, golden cases labelled by the user, one deliberate G4 rejection) | |
| 9 | Retro in `docs/pilot-retro.md` | |

G6 / G7 are out of the first pass (they need traffic over time).

### Step 3: permissions

- deny: `Bash(python scripts/adlc.py sign:*)`, `Bash(python scripts/adlc.py approve:*)`,
  `Bash(python scripts/adlc.py reset-gates:*)` (and the `uv run` forms), Edit/Write on `evidence/**`
- ask: Edit on `agents/orchestrator/src/routing.py`, `evals/thresholds.yaml`, `system.yaml`,
  `docs/design/tool-permission-matrix.yaml`, `evals/datasets/**`
- also denied: `adlc.py simulate` and `make simulate` (they approve every gate with simulated approvers).
- Prefix rules are easy to get around (`cd scripts && python adlc.py sign`, `curl`, `python -c`), so a
  PreToolUse hook, `.claude/hooks/adlc_guard.py`, checks the whole command or path and blocks it (exit 2):
  `adlc.py sign|approve|reset-gates|simulate` in any form, `make simulate`, POSTs to
  `/lifecycle/gates/*/sign|approve`, `/lifecycle/gates/reset`, `/usecases/*/submit`,
  `/usecases/*/classification/confirm`, `/changes/*/decide`, direct `lifecycle.sign/approve/...` and
  `usecase.submit/confirm_classification` calls, and Edit/Write on `evidence/**`.
- Test (2026-11-02): 22 hook cases (14 block, 8 allow) as expected. Live in the session, `adlc.py sign`
  was blocked by the hook and a Write to `evidence/` by the deny rule; gates all still pending, ledger OK,
  105 tests green.
- Known false positive: the hook also matches those strings anywhere in a command's text (a heredoc,
  `grep`, a commit message). Use the Edit tool or put the text in a file.
- Not covered: writing the gate store or ledger directly (e.g. SQL). Ledger tampering is caught by
  `adlc.py verify-ledger`. `make test` and engine actions still write `evidence/` as intended.

### Step 5: `adlc` preset (chosen over editing `.specify/templates/`)

Spec Kit v1.2.5 supports **presets** (`specify preset add`, template resolution by priority). Packaging
the ADLC template changes as a local `adlc` preset instead of editing `.specify/templates/` directly
keeps Spec Kit upgradable and makes the customisation reusable for other apps. Its built-in
**workflow gates** (`.specify/workflows/speckit/workflow.yml`) are plain approve/reject for whoever runs
the CLI, with no roles, so they do not replace ADLC sign-off.

- Source `adlc/speckit-preset/` (see its README), installed with `specify preset add --dev` into
  `.specify/presets/adlc/`. Core templates in `.specify/templates/` are unchanged.
- `append` strategy: spec gets "ADLC: G1 Requirements", plan gets "Constitution Check by Principle" and
  "G2 Design Record", tasks get "ADLC Task Rules" and "Phase ADLC: Evidence and Gate Readiness (G3, G4)".
  New `gate-brief-template` for `specs/<release>-<slug>/briefs/G*.md`.
- Key point: the gate checks read the engine's files (`docs/requirements/*.yaml`, `docs/kpis.yaml`,
  `evals/thresholds.yaml`, `docs/design/*`, `evidence/`), not `spec.md` / `plan.md`. The ADLC sections
  are therefore registers that name the target engine file for every item, plus a checklist to write
  them before `adlc.py check`.
- Preset composition needs PyYAML in the resolver's Python; system `python3` has none, so
  `.claude/settings.json` sets `SPECKIT_PYTHON_EXECUTABLE=.venv/bin/python` (relative: run Spec Kit
  scripts from the repo root; outside Claude, export it in your shell). Without it the resolver fails
  loudly rather than silently dropping the ADLC sections.
- Verified 2026-11-02: all four templates resolve with the ADLC sections (bash resolver, `--json`
  mode, `specify preset resolve`); 105 tests green.

### Step 6: ADLC commands and previous-gate check (preset v0.2.0)

- Preset `command` entries; Spec Kit renders each as a Claude skill named `speckit-<name>`, so the
  commands are `/speckit-adlc-intake`, `/speckit-adlc-gate`, `/speckit-adlc-status`,
  `/speckit-adlc-rework` (skill names cannot contain dots, so `/adlc.gate` was not possible).
  What each does and never does: `adlc/speckit-preset/README.md`.
- Previous-gate check: `prepend` blocks on the core commands, `specify`/`clarify`/`checklist` need G0,
  `plan` needs G1, `tasks`/`implement`/`converge` need G2. The core command bodies are unchanged
  (verified against the pre-install copies); `specify preset remove adlc` restores them.
- Intake drafts: the record under `usecase/` is written only through `usecase.save`, which records
  `by` in the ledger. New persona `claude` (AI assistant, no roles) in `ops/teams.yaml`, so drafts are
  attributed to Claude and cannot be signed by it; the Product owner submits and the Data owner confirms.
  `adlc intake` also creates `specs/<release>-<slug>/` and `.specify/feature.json`, which
  `/speckit-specify` then uses instead of a numbered folder.
- Guard hook extended: G1 confirmation (`/usecases/*/g1`, `usecase.save_g1`), saving the record as
  anyone but `claude`, and the module form `python -m scripts.adlc sign|approve|...`. 34 hook cases pass.
- Spec Kit rewrites `scripts/...` paths in rendered commands to `.specify/scripts/...`; commands call the
  engine as `uv run python -m scripts.adlc` to avoid it.
- Live test 2026-11-02: `/speckit-specify` with G0 pending stopped at the precondition and created
  nothing. 105 tests green.

### Gate walkthrough (steps 7-8)

| Gate | Started with | Signers (`shared/lifecycle.py`) |
|---|---|---|
| G0 | `/speckit-adlc-intake CARD-101`; PO submits on the intake page, Data owner confirms classification | Sponsor, Risk, Data owner (hoc, risk, dataowner) |
| G1 | `/speckit-specify`, `/speckit-clarify` (Business owner answers), `/speckit-checklist`, `/speckit-adlc-gate G1`; Business owner confirms limits/KPIs | Business owner, AI lead (hoc, ailead) |
| G2 | `/speckit-plan`, `/speckit-analyze`, `/speckit-adlc-gate G2` | Architecture, Security (architect, security) |
| G3 | `/speckit-tasks`, `/speckit-implement` on `release/0.4.0` with a PR, `/speckit-adlc-gate G3` | AI lead, Product owner (ailead, po) + code-owner PR review |
| G4 | `/speckit-adlc-gate G4` | RAI/Risk, Compliance (rai, compliance) |
| G5 | CR raised on the board, `/speckit-adlc-gate G5` | CAB, Head of Claims (cab, hoc) |

## Open questions on CARD-101 (for G1)

1. Self-transfer itineraries on separate tickets: in or out?
2. Shared travel delay limit or its own limit?
3. Minimum connection time: airline published value or a fixed [60 minutes]?
