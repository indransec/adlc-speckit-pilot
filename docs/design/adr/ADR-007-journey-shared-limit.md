# ADR-007: Journey-level shared limit and duplicate expenses

- Status: Proposed (G2, release 0.4.0)
- Date: 2026-11-02
- Deciders: Architecture, Security

## Context

At G1 the Business owner decided that travel delay and missed connection share one travel delay
limit per journey (outward and return are separate journeys), and that no expense is paid twice,
whichever benefit is claimed first (REQ-F-038). The existing linked-claims total (D-07,
REQ-F-015) aggregates approved amounts across the whole trip for routing authority; it does not
separate benefits or directions.

## Decision

- A journey is `trip_id` + `direction` (`outward` | `return`).
- New read operation `TOOL-CLAIMS-SYSTEM.journey_benefits(trip_id, direction, exclude_claim_id)`
  returns approved travel delay and missed connection amounts on other claims of the journey and the
  receipt fingerprints of their paid lines. Called by orchestrator code only (not attached to a
  model) and passed to assessment.
- The calculator caps delay + missed connection lines at `delay_limit − already paid` (clause 3.6,
  type A) and deducts lines whose receipt fingerprint matches a paid line (clause 7.2, type A).
  Fingerprint = document SHA-256, else (merchant, date, currency, amount); exact match only.
- The routing authority total (`linked_approved_total`) is unchanged.

## Consequences

- Shared limit and duplicates work in either claim order without widening any agent's permissions.
- Exact-match fingerprints miss re-photographed receipts with different text extraction; fraud
  screening and the QA sample remain the backstop (threat T12).
- Type A for exact duplicates keeps them on the one-level path when the reduction is small; any
  doubtful overlap stays a type B judgement.
