# ADR-002: Confidence is a combined, calibrated score

- Status: Accepted
- Date: 2026-11-10
- Deciders: AI lead, Architecture, Security, Head of Claims

## Context
LLM self-reported confidence is poorly calibrated.

## Decision
Compute confidence from: extraction field certainty, clause-match strength, verification success, cross-document consistency and agent agreement. Calibrate the 0.7 cut-off on historical adjuster decisions.

## Consequences
Needs a calibration dataset and periodic recalibration (G7).

## Linked requirements
See `docs/requirements/` (REQ-B-001..REQ-B-005).
