# adlc Spec Kit preset

Adds the ADLC to Spec Kit (v1.2.5+) without editing Spec Kit's own templates, so Spec Kit can be
upgraded and the preset reused by other apps.

| Template | Strategy | Adds |
|---|---|---|
| `spec-template` | append | G1 section: requirement register (REQ IDs + verification), autonomy/HITL impact, KPIs, eval thresholds, ground truth to label, open decisions, G1 artifact checklist |
| `plan-template` | append | Constitution check by principle; G2 design record mapped to the engine's G2 files; rework of earlier gates; decisions for signers |
| `tasks-template` | append | Task rules (REQ IDs, `[AUTHORITY]`, `[HUMAN: persona]`) and the G3/G4 evidence phase |
| `gate-brief-template` | new | Brief for the people who sign a gate (`specs/<release>-<slug>/briefs/G*.md`) |

Commands (Spec Kit renders each as a Claude skill named `speckit-<name>`):

| Skill | Does | Never |
|---|---|---|
| `/speckit-adlc-intake <CARD>` | Drafts the G0 intake record (saved as persona `claude`) and creates `specs/<release>-<slug>/` | submits or confirms classification |
| `/speckit-adlc-gate <G>` | Runs the gate's checks, writes `briefs/<G>.md`, names who signs | signs, approves, resets |
| `/speckit-adlc-status` | Gate overview, what blocks the open gate, next command | changes anything |
| `/speckit-adlc-rework <artifact>` | Finds the gate that froze it, shows drift, writes a rework note | reopens a gate |

Previous-gate check (prepended): `specify`, `clarify`, `checklist` need G0; `plan` needs G1;
`tasks`, `implement`, `converge` need G2.

Authoring note: when Spec Kit renders commands it rewrites any path starting `scripts/`,
`templates/` or `memory/` (after a space, quote or backtick) to `.specify/...`. Commands therefore call
the engine as `uv run python -m scripts.adlc ...`.

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
