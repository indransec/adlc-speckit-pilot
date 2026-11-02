
## ADLC: G1 Requirements *(mandatory)*

<!--
  Added by the adlc preset. The G1 gate checks do not read this file: they read
  docs/requirements/*.yaml, docs/kpis.yaml, evals/thresholds.yaml, agents/orchestrator/src/routing.py
  and system.yaml. This section is the proposal; the same content is written to those files before
  G1 is checked. Signers: Business owner (Head of Claims) and AI lead.
-->

**Use case**: [CARD-ID, intake/ file] | **Release**: [x.y.z] | **Risk tier**: [from docs/risk-assessment.md / G0]

### Requirement Register

Each FR/SC above maps to at least one ADLC requirement. New IDs continue the numbering in
`docs/requirements/` (`REQ-F-` functional, `REQ-N-` non-functional, `REQ-B-` behavioural).
Verification uses the existing forms: `test:<name>`, `eval:<suite>`, `review:<who>`.

| REQ ID | Type | Requirement | Verification | Covers | Target file |
|---|---|---|---|---|---|
| [REQ-F-0xx] | F | [what the system must do] | [eval:suite, test:name] | [FR-00x] | docs/requirements/functional.yaml |
| [REQ-B-0xx] | B | [what the agents must or must never do] | [eval:suite] | [FR-00x] | docs/requirements/behavioural.yaml |
| [REQ-N-0xx] | N | [latency, cost, availability, audit] | [test:perf] | [SC-00x] | docs/requirements/non-functional.yaml |

### Autonomy and Human-in-the-Loop Impact

<!-- Constitution III: routing, limits and payment authority are code, owned by the Business owner and AI lead. -->

- **Routing matrix** (`docs/design/routing-matrix.md`, `routing.py`): [No change | proposed change + reason]
- **Authority limits / straight-through limit** (`system.yaml` routing keys): [No change | proposed change]
- **HITL points** (who decides what; L1/L2/SIU): [unchanged | changes]
- **Payment path** (approval token, ADR-003): [unchanged]

### KPIs

| KPI ID | Name | Baseline | Target | Source | Target file |
|---|---|---|---|---|---|
| [KPI-0x] | [metric from the use-case card] | [value or how measured] | [value] | [system/report] | docs/kpis.yaml |

### Eval Thresholds

<!-- New or changed entries in evals/thresholds.yaml; the Business owner agrees them at G1. Existing thresholds still apply. -->

| Metric | Threshold | New / changed | Why |
|---|---|---|---|
| [metric_name] | [min / max_count] | [new] | [REQ ID it verifies] |

### Ground Truth Needed

<!-- Constitution V: Claude may propose cases; a named person labels them before they count. -->

| Dataset | Cases needed | What they cover | Labelled by |
|---|---|---|---|
| golden | [n] | [scenarios, REQ IDs] | [persona] |
| adversarial | [n] | [injection, fraud, edge cases] | [persona] |

### Open Decisions for G1 Signers

| # | Question | Options | Decided by | Decision |
|---|---|---|---|---|
| 1 | [question from the card or /speckit-clarify] | [A / B] | [Business owner] | [pending] |

### G1 Artifact Updates

- [ ] `docs/requirements/*.yaml` updated with the register above
- [ ] `docs/kpis.yaml` updated
- [ ] `evals/thresholds.yaml` updated (CODEOWNERS: Business owner + AI lead), or no change
- [ ] Routing / `system.yaml` authority keys: no change, or change approved by their owners
- [ ] `uv run python scripts/adlc.py check G1` passes; brief written to `briefs/G1.md`
