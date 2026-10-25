"""Combined confidence score (ADR-002). Never the model's self-reported confidence.

score = min(weighted mean of signals, weakest signal + 0.35)
The cap stops one failed signal (e.g. documents that contradict each other) from being
averaged away by four good ones. Calibrate weights and the 0.7 cut-off at G7.
"""
from __future__ import annotations

WEIGHTS = {"extraction": 0.25, "consistency": 0.30, "verification": 0.20, "clause_match": 0.10, "agreement": 0.15}
WEAKEST_SIGNAL_MARGIN = 0.35
DEGRADED_CAP = 0.65   # an agent fell back after a model failure: force human judgement


def compute(document: dict, coverage: dict, verification: dict, assessment: dict, degraded: bool = False) -> dict:
    comps = {
        "extraction": float(document.get("extraction_certainty", 0.0)),
        "consistency": 1.0 if document.get("consistent", False) else 0.0,
        "verification": float(verification.get("verification_success", 0.5)),
        "clause_match": float(coverage.get("clause_match_strength", 0.5)),
        "agreement": 1.0 if assessment.get("agent_agreement", False) else 0.5,
    }
    weighted = sum(WEIGHTS[k] * v for k, v in comps.items())
    cap = min(comps.values()) + WEAKEST_SIGNAL_MARGIN
    score = min(weighted, cap)
    if degraded:
        score = min(score, DEGRADED_CAP)
    return {"score": round(score, 3), "components": comps, "weighted_mean": round(weighted, 3),
            "cap": round(cap, 3), "degraded": degraded, "weights": WEIGHTS}
