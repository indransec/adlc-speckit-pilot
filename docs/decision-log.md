# Decision log

| ID | Decision | Rationale | Decided by | Date |
|---|---|---|---|---|
| D-01 | Scope: trip delay and baggage, USD | Simplest claim types for MVP | Head of Claims | 2026-10-25 |
| D-02 | Agent authority USD 1,000 claim-level aggregate (incl. linked trip/event claims) | Mirrors adjuster authority matrix practice | Head of Claims | 2026-10-25 |
| D-03 | Every full or partial rejection needs human approval | Customer and regulatory protection | Head of Claims | 2026-10-25 |
| D-04 | Type A deductions -> 1 human level; type B or > 25% reduction -> 2 levels | Safe start; builds data for later widening | Head of Claims | 2026-10-25 |
| D-05 | Confidence cut-off 0.7 (provisional) | To be calibrated on historical decisions | AI lead | 2026-10-25 |
| D-06 | Start the safe way: every deduction goes to at least one human | Low STP at launch accepted | Head of Claims | 2026-10-25 |
| D-07 | Orchestrator gets read access to the claims system (linked-claim aggregate) | REQ-F-015 needs prior approved amounts on the same trip/event | Security architect, AI lead | 2026-10-25 |
| D-08 | Pilot stack: LangGraph orchestrator, OpenRouter gateway (Claude Haiku 5.5 fast tier / Sonnet 5.5 reasoning tier), FAISS clause retrieval, SQLite, local object store, FastAPI, Streamlit workbench, Arize Phoenix (OTel), Presidio | Open-source, runs locally; mirrors target enterprise components | AI lead, Architecture | 2026-10-25 |
| D-09 | Settlement agent has no model: payment data never passes through a model | data-flows.md; REQ-B-009 | Security architect | 2026-10-25 |
| D-10 | Deterministic mock-LLM mode when no gateway key: agents fall back to rule-based extraction/judgement | Offline CI evals, reproducible demos; live runs re-evaluated at G3 | AI lead | 2026-10-25 |
| D-11 | Executable component eval metrics added to evals/thresholds.yaml (classification, completeness, letters, red team, counterfactual fairness, audit) | Every suite.yaml check needs a measurable threshold for G3/G4 | Head of Claims, AI lead | 2026-10-25 |
