## ADLC Gate Precondition (adlc preset)

Before anything else, confirm G2 is approved. This command produces the G3 build.

1. Run `uv run python -m scripts.adlc status` from the repository root and read the `G2` line.
2. If its status is not `approved` or `approved_with_conditions`: **stop**. Do not create tasks or
   change code. Tell the user G2 is not approved, show the G2 line, and suggest
   `/speckit-adlc-status` or `/speckit-adlc-gate G2`.
3. If approved, continue. Code changes go on `release/<version>` (version from `system.yaml`), never
   on `main`; create the branch if it does not exist. Stop at any task marked `[HUMAN: ...]` and ask
   that person; stop and ask before changing a file marked `[AUTHORITY]` beyond what G1/G2 approved.

Constitution I and II apply throughout: never sign, approve, reset or simulate a gate.
