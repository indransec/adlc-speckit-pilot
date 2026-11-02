# adlc Spec Kit preset

Adds the ADLC to Spec Kit (v1.2.5+) without editing Spec Kit's own templates, so Spec Kit can be
upgraded and the preset reused by other apps.

| Template | Strategy | Adds |
|---|---|---|
| `spec-template` | append | G1 section: requirement register (REQ IDs + verification), autonomy/HITL impact, KPIs, eval thresholds, ground truth to label, open decisions, G1 artifact checklist |
| `plan-template` | append | Constitution check by principle; G2 design record mapped to the engine's G2 files; rework of earlier gates; decisions for signers |
| `tasks-template` | append | Task rules (REQ IDs, `[AUTHORITY]`, `[HUMAN: persona]`) and the G3/G4 evidence phase |
| `gate-brief-template` | new | Brief for the people who sign a gate (`specs/<release>-<slug>/briefs/G*.md`) |

The ADLC engine (`shared/lifecycle.py`) still decides gate readiness from its own files
(`docs/requirements/`, `docs/design/`, `evals/`, `evidence/`); these sections make Spec Kit produce
and point to exactly those files.

Install (copies into `.specify/presets/adlc/`):

```bash
specify preset add --dev adlc/speckit-preset
specify preset resolve spec-template      # shows the composed layers
.specify/scripts/bash/resolve-template.sh gate-brief-template
```

After changing a file here, reinstall: `specify preset update adlc --dev adlc/speckit-preset`
(or remove and add).
