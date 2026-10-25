"""Fraud-screening agent (AGT-TRV-FRAUD): SIU model score + AI anomaly flags. Flag only (REQ-B-007).

The output has no recommendation field by design: this agent cannot reject a claim.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AnomalyFlag(BaseModel):
    code: Literal["inconsistent_narrative", "altered_document_suspected", "implausible_items", "other"]
    description: str


class AnomalyReview(BaseModel):
    flags: list[AnomalyFlag] = Field(default_factory=list, description="Only clear anomalies; empty list if none")


def _heuristic(doc: dict) -> AnomalyReview:
    flags = []
    if any("receipt amount" in m for m in doc.get("mismatches", [])):
        flags.append(AnomalyFlag(code="altered_document_suspected", description="Receipt amount differs from the claim form."))
    return AnomalyReview(flags=flags)


def run(ctx, *, document: dict) -> dict:
    prompt = ("Review these extracted claim facts for anomalies a fraud investigator should see. "
              "Do not decide the claim.\n"
              f"Facts: {document.get('facts')}\nLines: {[(l['description'], l['claimed_usd'], bool(l['receipt_doc_id'])) for l in document.get('lines', [])]}\n"
              f"Cross-document mismatches: {document.get('mismatches')}")
    review, _ = ctx.llm("anomaly_flags", AnomalyReview, prompt, fallback=lambda: _heuristic(document))
    signals = {"injection_detected": document.get("injection_detected", False),
               "doc_mismatch": bool(document.get("mismatches")), "ai_flags": len(review.flags)}
    siu = ctx.tool("TOOL-FRAUD-SCORE", "score", claim_id=ctx.claim_id, signals=signals)
    return {"risk_level": siu["level"], "score": siu["score"], "reasons": siu["reasons"], "model": siu["model"],
            "ai_flags": [f.model_dump() for f in review.flags], "signals": signals}
