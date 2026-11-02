---
name: speckit-adlc-rework
description: 'ADLC: an artifact approved at an earlier gate needs to change - find
  the gate that froze it, show the drift and write a rework note for its signers.
  Never reopens gates.'
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: github-spec-kit
  source: preset:adlc
argument-hint: artifact path or name, plus the reason
user-invocable: true
disable-model-invocation: false
---

# Speckit Adlc Rework Skill

## User Input

```text
$ARGUMENTS
```

## Outline

Constitution II: approved artifacts are frozen; changing one goes back through its gate. You explain
and prepare; a person reopens the gate. Never reset, sign or approve a gate.

1. **Find the gate**: match the argument against `adlc/gate_artifacts.yaml` (name or path, globs
   included). If it is not a frozen artifact, say so: no rework needed.
2. **State**: `uv run python -m scripts.adlc status`, and `uv run python -m scripts.adlc check` for
   that gate and every later gate up to the open one. Note the drift lines and `on_drift: block`
   entries, which stop later gates until the gate is re-approved.
3. **Rework note**: write `<feature folder>/briefs/rework-<GATE>-<YYYYMMDD>.md` with:
   the artifact and gate, the reason (from the user), the proposed change (diff or summary),
   which Spec Kit sections must be updated with it (`spec.md` for G1, `plan.md` for G2),
   which later gates are affected, the signers of that gate (from `check`), and
   the steps: a person reopens the gate on the ADLC Lifecycle page, the change is made,
   `/speckit-adlc-gate <GATE>` prepares a new brief, the signers sign again.
4. Do not make the change to the artifact until the user confirms, and never to a file marked
   authority (`routing.py`, `evals/thresholds.yaml`, `system.yaml` routing keys,
   `tool-permission-matrix.yaml`) without its owners.
5. **Report** the note's path and who must act.
