# ADR-001: Routing and authority limits implemented as deterministic code

- Status: Accepted
- Date: 2026-11-10
- Deciders: AI lead, Architecture, Security, Head of Claims

## Context
Routing decides how much human oversight a claim receives. If it lived in a prompt, a model error or a prompt injection could bypass human review.

## Decision
Implement the routing matrix in `agents/orchestrator/src/routing.py` with unit tests for every row. Agents supply inputs (amounts, deduction labels, confidence); code decides the route.

## Consequences
Control is testable and auditable. Changing thresholds requires a code change reviewed by the business owner (CODEOWNERS).

## Linked requirements
See `docs/requirements/` (REQ-B-001..REQ-B-005).
