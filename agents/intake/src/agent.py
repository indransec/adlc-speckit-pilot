"""Intake agent (AGT-TRV-INTAKE): classify and register the claim (REQ-F-001)."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from shared.guardrails.injection import wrap_as_data
from shared.guardrails.pii import redact_for_model

CATEGORY = {"delay": "delay", "baggage_delay": "baggage", "baggage_loss": "baggage"}


class IntakeClassification(BaseModel):
    claim_type: Literal["delay", "baggage_delay", "baggage_loss"] = Field(
        description="delay = flight departure delay expenses; baggage_delay = bag arrived late; baggage_loss = bag lost/stolen/damaged")
    rationale: str = Field(description="One sentence, no personal data")


def _heuristic(submission: dict) -> IntakeClassification:
    declared = submission.get("claim_type_declared")
    if declared in CATEGORY:
        return IntakeClassification(claim_type=declared, rationale=f"Declared claim type '{declared}' matches the items claimed.")
    text = (submission.get("description") or "").lower()
    t = "baggage_loss" if "lost" in text else "baggage_delay" if "bag" in text else "delay"
    return IntakeClassification(claim_type=t, rationale="Classified from the claim description.")


def run(ctx, *, submission: dict) -> dict:
    payload = {"declared_claim_type": submission.get("claim_type_declared"),
               "items": [{"description": l["description"], "category": l["category"]}
                         for l in json.loads(submission.get("lines_json") or "[]")],
               "description": submission.get("description")}
    text = redact_for_model(json.dumps(payload, indent=1), claim_id=ctx.claim_id, agent_id=ctx.agent_id)
    value, _ = ctx.llm("classify", IntakeClassification,
                       "Classify this travel claim.\n" + wrap_as_data(text, ctx.claim_id, "claim_submission"),
                       fallback=lambda: _heuristic(submission))
    category = CATEGORY[value.claim_type]
    ctx.tool("TOOL-CLAIMS-SYSTEM", "register", claim_id=ctx.claim_id, claim_type=value.claim_type, category=category)
    return {"claim_type": value.claim_type, "category": category, "rationale": value.rationale,
            "declared": submission.get("claim_type_declared"),
            "agrees_with_declared": value.claim_type == submission.get("claim_type_declared")}
