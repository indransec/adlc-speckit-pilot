---
description: "ADLC: show where the release is in G0-G7, what blocks the open gate, and the next command to run. Read-only."
---

## User Input

```text
$ARGUMENTS
```

## Outline

Read-only. Never sign, approve, reset or simulate a gate (constitution I).

1. From the repository root run:
   - `uv run python -m scripts.adlc status`
   - `uv run python -m scripts.adlc verify-ledger`
   - read `release` and `use_case` from `system.yaml`, and `.specify/feature.json` if it exists.
2. The **open gate** is the first gate that is not `approved` / `approved_with_conditions`. Run
   `uv run python -m scripts.adlc check <open gate>` and collect failing checks.
3. Report, in this order:
   - release, use case, feature folder;
   - one line per gate G0-G7 (status, checks passed);
   - the open gate: failing checks, whether the previous gate is approved, required signers
     (from the check output), and whether `specs/<release>-<slug>/briefs/<gate>.md` exists;
   - ledger result;
   - **Next step**, using this map:

| Open gate | Not ready yet | Ready (checks pass) |
|---|---|---|
| G0 | `/speckit-adlc-intake <card>`, Product owner submits, Data owner confirms | `/speckit-adlc-gate G0`, then signers sign in the UI |
| G1 | `/speckit-specify`, `/speckit-clarify`, `/speckit-checklist`, write the G1 artifacts | `/speckit-adlc-gate G1` |
| G2 | `/speckit-plan`, `/speckit-analyze`, write the G2 artifacts | `/speckit-adlc-gate G2` |
| G3 | `/speckit-tasks`, `/speckit-implement` on `release/<version>` | `/speckit-adlc-gate G3` |
| G4 | RAI tasks of the ADLC phase in `tasks.md` | `/speckit-adlc-gate G4` |
| G5+ | release actions (`adlc.py actions`) | `/speckit-adlc-gate <gate>` |

4. If a check reports drift ("Artifacts approved at earlier gates unchanged" failing), say which
   artifact and suggest `/speckit-adlc-rework <artifact>`.
