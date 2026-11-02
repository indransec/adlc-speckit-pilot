## ADLC Gate Precondition (adlc preset)

Before anything else, confirm G0 is approved. This command produces G1 requirements.

1. Run `uv run python -m scripts.adlc status` from the repository root and read the `G0` line.
2. If its status is not `approved` or `approved_with_conditions`: **stop**. Do not create or change
   any spec file. Tell the user G0 is not approved, show the G0 line, and suggest
   `/speckit-adlc-status` (what is missing) or `/speckit-adlc-gate G0` (brief for the signers).
3. If approved, continue. Feature folder: if `.specify/feature.json` names a folder created by
   `/speckit-adlc-intake` (`specs/<release>-<slug>/`), use it as `SPECIFY_FEATURE_DIRECTORY` instead
   of generating a numbered one.

Constitution I and II apply throughout: never sign, approve, reset or simulate a gate.
