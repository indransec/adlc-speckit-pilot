# ADR-006: Minimum connection time from a versioned reference file

- Status: Proposed (G2, release 0.4.0)
- Date: 2026-11-02
- Deciders: Architecture, Security (G2); Business owner and AI lead as owners of the file

## Context

The missed connection benefit (clause 3.7, CARD-101) pays only when the originally booked connection
allowed at least the airport's minimum connection time (MCT). The Business owner decided at G1:
the airport's published MCT, 60 minutes when none is published. A wrong MCT pays or declines the
wrong customers (G0 failure mode; threat T10).

## Decision

Keep MCT values in `reference/minimum-connection-times.yaml`: one value per airport (IATA code),
with `source`, `as_of` and `fallback_minutes: 60`. Verification code reads it; the model never sees
or chooses the value. The decision package records the value, its source and as-of date.
Owner: Product owner (Claims operations); reviewed quarterly; changes by pull request approved by
CODEOWNERS (Business owner, AI lead), since the value decides eligibility.

## Consequences

- Deterministic and auditable; no new external dependency or contract.
- Terminal-specific and domestic/international MCTs are not modelled in 0.4.0; a coarse value can
  favour or disfavour some connections. Revisit with a licensed MCT feed if QA shows errors.
- A stale file is a risk: the `as_of` date is shown in the decision package, and a G6 hypercare check
  flags files older than 120 days.
