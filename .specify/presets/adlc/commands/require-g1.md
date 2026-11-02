## ADLC Gate Precondition (adlc preset)

Before anything else, confirm G1 is approved. This command produces the G2 design.

1. Run `uv run python -m scripts.adlc status` from the repository root and read the `G1` line.
2. If its status is not `approved` or `approved_with_conditions`: **stop**. Do not create or change
   any plan or design file. Tell the user G1 is not approved, show the G1 line, and suggest
   `/speckit-adlc-status` or `/speckit-adlc-gate G1`.
3. If approved, continue. Anything this plan would change in a G0/G1 artifact is rework: list it in
   the plan's "Rework of Earlier Gates" section rather than editing the artifact.

Constitution I and II apply throughout: never sign, approve, reset or simulate a gate.
