---
name: speckit-adlc-gate
description: 'ADLC: prepare a gate for its human signers - run the engine checks and
  write the gate brief. Never signs.'
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: github-spec-kit
  source: preset:adlc
argument-hint: G0..G7
user-invocable: true
disable-model-invocation: false
---

# Speckit Adlc Gate Skill

## User Input

```text
$ARGUMENTS
```

## Outline

The argument is a gate ID (`G0`-`G7`). If missing, use the open gate from `adlc.py status`.
You prepare; people decide. Never sign, approve, reject, reset or simulate a gate, and never record a
persona decision (constitution I).

1. **State**: from the repository root run `uv run python -m scripts.adlc status`,
   `uv run python -m scripts.adlc check <GATE>` and `uv run python -m scripts.adlc verify-ledger`.
   Note the required signers printed by `check`.
2. **Order** (constitution II): if the previous gate is not approved, stop and say so; no brief.
3. **Feature folder**: `feature_directory` from `.specify/feature.json` (`specs/<release>-<slug>/`).
   If there is none (possible at G0), create `specs/<release>-<slug>/` from `system.yaml` `release`
   and a 2-4 word slug of the use case or card, and write it to `.specify/feature.json`.
4. **Brief**: resolve the template with
   `.specify/scripts/bash/resolve-template.sh gate-brief-template` and write
   `<feature folder>/briefs/<GATE>.md`. Fill it from real sources only:
   - Signers: roles from the `check` output, personas holding each role from `ops/teams.yaml`.
   - Readiness: every check line as printed, the previous gate's record, the ledger head.
   - What changed / What to review: the artifacts frozen at this gate (`adlc/gate_artifacts.yaml`)
     with their paths, and the Spec Kit sections they came from (G1: `spec.md` "ADLC: G1
     Requirements"; G2: `plan.md` "ADLC: G2 Design Record"; G3/G4: `tasks.md` ADLC phase).
   - Decisions asked: open decisions recorded in the spec/plan, plus "approve or reject <GATE>".
   - Checklists: for every `checklists/g<N>-*.md` of this gate (and `requirements.md` at G1), the
     ticked / open counts and the open items, so the signers decide with them in view; once the gate
     is approved, later commands treat those checklists as informational.
   - Constitution compliance: one line per principle I-VII; detail only exceptions.
   - Evidence: paths under `evidence/<release>/` as produced by the engine. Never write there.
   If checks fail, still write the brief, marked **NOT READY** at the top, listing what is missing.
5. **Report** to the user: ready or not, failing checks, the brief path, and exactly who signs:
   "Sign as <persona id> (<name>, <role>) on the ADLC Lifecycle page" for each required role.
