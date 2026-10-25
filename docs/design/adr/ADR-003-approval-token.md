# ADR-003: Settlement requires a signed approval token

- Status: Accepted
- Date: 2026-11-10
- Deciders: AI lead, Architecture, Security, Head of Claims

## Context
Payment is the highest-impact action.

## Decision
The orchestrator issues a signed token only after the final required approval (agent row 1, L1 or L2). The payments tool rejects instructions without a valid token.

## Consequences
Prevents payment without approval even if an agent misbehaves.

## Linked requirements
See `docs/requirements/` (REQ-B-001..REQ-B-005).
