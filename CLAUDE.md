# ADLC Spec Kit pilot

This repo is a copy of travel-claims-assistant (baseline `43c4f03`, tag `pilot-baseline` in that repo)
used to run the ADLC with GitHub Spec Kit doing the authoring and Claude doing the work, while named
people sign every gate. Pilot scope: release **0.4.0**, missed connection benefit
(`intake/CARD-101-missed-connection.md`). Plan, decisions and progress: `docs/pilot-plan.md`.

## Rules for Claude in this repo

- **Never sign or approve a gate.** Do not run `scripts/adlc.py sign`, `approve` or `reset-gates`, and do
  not call the `/lifecycle/gates/*/sign|approve|reset` endpoints. People sign as personas in the UI.
  The engine refuses role-less approvals here (`adlc_governed: true` in `deploy/environments/dev.yaml`).
- **Gates run in order** (G0 → G7, as defined in `shared/lifecycle.py`; do not renumber or change gate
  actions). Before starting a stage, check `uv run python scripts/adlc.py status` and stop if the previous
  gate is not approved.
- **Authority rules change only through G1/G2 with their owners:** `agents/orchestrator/src/routing.py`,
  `evals/thresholds.yaml`, routing keys in `system.yaml`, `docs/design/tool-permission-matrix.yaml`.
- **Ground truth is human.** Propose golden/adversarial cases; a person labels them.
- **Evidence is generated, never hand-edited** (`evidence/**`).
- Keep customisations simple: prompts, templates and settings first; engine code only when a step is
  blocked (as with governed mode in step 1).

## Running

- `OPENROUTER_API_KEY` is blank in `.env` (mock models). Live models cost money: ask first.
- `make test`, `make api` (:8000), `make ui` (:8501), `make status`.
- Spec Kit v1.2.5 skills: `/speckit-constitution`, `-specify`, `-clarify`, `-plan`, `-checklist`,
  `-tasks`, `-analyze`, `-implement` (in `.claude/skills/`, templates in `.specify/`).
- If the shell has another repo's virtualenv active, `uv` warns; run `deactivate` first.
