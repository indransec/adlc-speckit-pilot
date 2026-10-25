"""Deterministic routing rules for the Travel Claims Assistant (ADR-001).

Source of truth: docs/design/routing-matrix.md. Changes need business-owner review
(CODEOWNERS). Rules are evaluated in order; the first match wins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

STP_LIMIT_USD = 1000.0          # claim-level aggregate authority of the agent
SIGNIFICANCE_NET_PCT = 25.0     # total reduction above this % of claimed -> 2 levels
CONFIDENCE_CUTOFF = 0.7         # provisional; calibrate on historical decisions


class DeductionType(str, Enum):
    A = "A"   # policy-norm: excess, sub-limit, cap, FX, offset
    B = "B"   # judgement: ineligible item, missing receipt, depreciation, partial exclusion


class Recommendation(str, Enum):
    APPROVE = "approve"
    PARTIAL = "partial"
    REJECT = "reject"


class Route(str, Enum):
    STRAIGHT_THROUGH = "row1_straight_through"
    L1_POLICY_NORM = "row2_l1_policy_norm"
    L1_AUTHORITY = "row3_l1_authority"
    L1_L2_SIGNIFICANT = "row4_l1_l2_significant"
    L1_L2_REJECTION = "row5_l1_l2_rejection"
    INVESTIGATION = "row6_investigation"
    SIU = "row7_siu"


@dataclass
class Deduction:
    amount_usd: float
    type: DeductionType
    clause: str
    evidence_refs: List[str] = field(default_factory=list)


@dataclass
class ClaimDecisionInput:
    claim_id: str
    claimed_usd: float
    recommendation: Recommendation
    approved_usd: float                      # agent-recommended payable for this claim
    prior_approved_linked_usd: float = 0.0   # already approved on this claim or linked trip/event claims
    deductions: List[Deduction] = field(default_factory=list)
    fraud_risk: str = "low"                  # low | medium | high
    confidence: float = 1.0


@dataclass
class RoutingDecision:
    route: Route
    approvals_required: int                  # 0 = agent, 1 = L1, 2 = L1 + L2
    reasons: List[str]


def route_claim(c: ClaimDecisionInput) -> RoutingDecision:
    reasons: List[str] = []
    aggregate = c.approved_usd + c.prior_approved_linked_usd
    total_deduction = sum(d.amount_usd for d in c.deductions)
    reduction_pct = (100.0 * (c.claimed_usd - c.approved_usd) / c.claimed_usd) if c.claimed_usd > 0 else 0.0
    has_b = any(d.type == DeductionType.B for d in c.deductions)
    b_without_evidence = any(d.type == DeductionType.B and not d.evidence_refs for d in c.deductions)

    # Row 7: fraud
    if c.fraud_risk in ("medium", "high"):
        return RoutingDecision(Route.SIU, 2, [f"fraud risk {c.fraud_risk}"])

    # Row 6: cannot reach a judgement
    if c.confidence < CONFIDENCE_CUTOFF:
        reasons.append(f"confidence {c.confidence:.2f} < {CONFIDENCE_CUTOFF}")
    if b_without_evidence:
        reasons.append("type B deduction without evidence")
    if reasons:
        return RoutingDecision(Route.INVESTIGATION, 1, reasons)

    # Row 5: full rejection
    if c.recommendation == Recommendation.REJECT:
        return RoutingDecision(Route.L1_L2_REJECTION, 2, ["full rejection recommended"])

    # Row 4: significant deduction
    if has_b:
        reasons.append("type B (judgement) deduction present")
    if reduction_pct > SIGNIFICANCE_NET_PCT:
        reasons.append(f"reduction {reduction_pct:.1f}% > {SIGNIFICANCE_NET_PCT}%")
    if reasons:
        return RoutingDecision(Route.L1_L2_SIGNIFICANT, 2, reasons)

    # Row 2: policy-norm deductions only
    if total_deduction > 0 or c.approved_usd < c.claimed_usd:
        return RoutingDecision(Route.L1_POLICY_NORM, 1, ["type A (policy-norm) deductions only"])

    # Row 3: above agent authority
    if aggregate > STP_LIMIT_USD:
        return RoutingDecision(Route.L1_AUTHORITY, 1, [f"aggregate USD {aggregate:,.2f} > {STP_LIMIT_USD:,.0f}"])

    # Row 1: straight-through
    return RoutingDecision(Route.STRAIGHT_THROUGH, 0, ["within agent authority, no deduction, low risk"])


def l1_outcome_reroute(c: ClaimDecisionInput, l1_removed_all_type_b: bool) -> RoutingDecision:
    """If L1 removes every type B deduction, the claim drops to the 1-level path."""
    if not l1_removed_all_type_b:
        return route_claim(c)
    kept = [d for d in c.deductions if d.type == DeductionType.A]
    approved = c.claimed_usd - sum(d.amount_usd for d in kept)
    c2 = ClaimDecisionInput(**{**c.__dict__, "deductions": kept, "approved_usd": approved})
    d = route_claim(c2)
    if d.route == Route.STRAIGHT_THROUGH:   # a human already reviewed it
        return RoutingDecision(Route.L1_AUTHORITY, 1, ["L1 reviewed; no deductions remain"])
    return d
