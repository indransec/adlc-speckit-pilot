
## ADLC: Constitution Check by Principle

<!-- Added by the adlc preset. Fill this as the Constitution Check above; repeat after Phase 1 design. -->

| Principle | Status | Evidence / note |
|---|---|---|
| I. People sign every gate | [pass] | [no step in this plan records a gate or persona decision] |
| II. Gates run in order | [pass] | [G1 approved on <date>; no change to G0/G1 artifacts, or rework listed below] |
| III. Authority is code | [pass / change] | [routing, limits, payment path unchanged, or owners named] |
| IV. Traceable, eval-gated release | [pass] | [every REQ ID has a verification; thresholds unchanged or agreed at G1] |
| V. Human ground truth, generated evidence | [pass] | [datasets to be labelled by <persona>; no hand-written evidence] |
| VI. Least privilege, data protection | [pass / change] | [tool matrix, redaction, audit, threat model updates below] |
| VII. Simplicity | [pass] | [no new dependency / data category / model, or G2 decision below] |

## ADLC: G2 Design Record *(mandatory)*

<!--
  The G2 gate checks read the files in the right-hand column, not this plan. Each row says what
  changes there. Signers: Architecture and Security.
-->

| G2 artifact | Change for this release | File |
|---|---|---|
| Architecture and topology | [none / what changes] | docs/design/architecture.md, system.yaml#topology |
| ADRs | [ADR-00x title, or none] | docs/design/adr/ |
| Tool permission matrix | [agent -> tool -> operations added or changed, or none] | docs/design/tool-permission-matrix.yaml (Security) |
| Threat model | [new threats: OWASP agentic, document injection, new failure modes] | docs/design/threat-model.md |
| Data flows and redaction points | [new fields or documents, redaction point] | docs/design/data-flows.md |
| Change control | [CODEOWNERS entries for new paths, or none] | CODEOWNERS |
| Manifests | [agent.yaml / system.yaml changes] | `make validate` |

### Rework of Earlier Gates

<!-- Constitution II: anything approved at G0/G1 that this plan changes goes back through that gate. -->

- [None | artifact, gate, reason]

### Decisions for G2 Signers

| # | Decision | Options | Recommended | Signer |
|---|---|---|---|---|
| 1 | [design choice] | [A / B] | [A, because ...] | [Architecture / Security] |
