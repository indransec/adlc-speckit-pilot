---
description: "ADLC: turn a use-case card (intake/) into a draft G0 intake record and the release feature folder. The Product owner submits; the Data owner confirms."
argument-hint: "CARD-ID, e.g. CARD-101"
---

## User Input

```text
$ARGUMENTS
```

## Outline

You draft; people submit and confirm. Never submit the use case or confirm the classification
(constitution I); those are persona actions in the Use Case Intake page.

1. **Read** `intake/<CARD-ID>*.md`, `system.yaml` (`release`, `use_case`) and the current record:
   `uv run python -c "from shared import usecase, config; import json; print(config.intake_enabled()); print(json.dumps(usecase.template(), default=str))"`.
   If `intake_enabled` is False, stop: the repo is not in governed mode.
2. **Status**: run `uv run python -m scripts.adlc status`. If G0 is already approved, stop and
   suggest `/speckit-adlc-rework` (a changed record after approval is drift).
3. **Draft the record** for use case `system.yaml: use_case`, starting from the current record and
   applying the card as a scope change for this release:
   - problem / proposed solution: add the card's problem and solution (keep the existing text);
   - value hypothesis: add the card's metrics;
   - scope in/out: add the card's items; remove the new benefit from `scope_out`;
   - data classification rows: change only if the card adds data (a change needs the Data owner again);
   - risk answers: unchanged unless the card changes autonomy, data or actions;
   - feasibility: keep the verdict; add conditions only for owned, dated items from the card;
   - placeholders in [square brackets] stay as written, quoted, and are listed as open items.
   Show the draft diff to the user and ask before saving.
4. **Save as Claude** (persona `claude`, no ADLC role) so the ledger shows who drafted it:
   write the draft to a scratch JSON file, then
   `uv run python -c "import json,sys; from shared import usecase; print(usecase.save(json.load(open(sys.argv[1])), by='claude')['tier'])" <file>`.
5. **Feature folder**: create `specs/<release>-<slug>/` (2-4 word slug from the card) with
   `intake.md` linking the card and listing open questions and placeholders, and write
   `{"feature_directory": "specs/<release>-<slug>"}` to `.specify/feature.json`.
6. **Report**: risk tier, open items, and the human steps, in order:
   - Product owner (`po`) reviews the draft on the Use Case Intake page, edits, and **submits** it;
   - Data owner (`dataowner`) **confirms the data classification**;
   - then run `/speckit-adlc-gate G0` for the brief; Sponsor, Risk and Data owner sign G0.
