# ADR-004: Human review in the existing adjuster workbench

- Status: Accepted
- Date: 2026-11-10
- Deciders: AI lead, Architecture, Security, Head of Claims

## Context
Adjusters already work in the claims workbench.

## Decision
Show the decision package and capture L1/L2 decisions there via its task API, rather than building a new UI.

## Consequences
Faster adoption; depends on workbench API capability (G0 condition).

## Linked requirements
See `docs/requirements/` (REQ-B-001..REQ-B-005).
