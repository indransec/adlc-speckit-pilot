"""Intake agent (AGT-TRV-INTAKE): classify and register the claim (REQ-F-001).

Release 0.4.0 adds missed_connection (category delay); a claim with both travel delay and missed
connection expenses for the same journey stays one missed_connection claim, lines tagged by benefit.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from shared.guardrails.injection import wrap_as_data
from shared.guardrails.pii import redact_for_model

CATEGORY = {"delay": "delay", "baggage_delay": "baggage", "baggage_loss": "baggage", "missed_connection": "delay"}


class IntakeClassification(BaseModel):
    claim_type: Literal["delay", "baggage_delay", "baggage_loss", "missed_connection"] = Field(
        description="delay = flight departure delay expenses; baggage_delay = bag arrived late; baggage_loss = bag "
                    "lost/stolen/damaged; missed_connection = a late arriving flight made the customer miss a booked "
                    "onward flight (also when the claim includes delay expenses for the same journey)")
    rationale: str = Field(description="One sentence, no personal data")


def _heuristic(submission: dict) -> IntakeClassification:
    declared = submission.get("claim_type_declared")
    if declared in CATEGORY:
        return IntakeClassification(claim_type=declared, rationale=f"Declared claim type '{declared}' matches the items claimed.")
    text = (submission.get("description") or "").lower()
    t = ("missed_connection" if "connection" in text or "missed my" in text else
         "baggage_loss" if "lost" in text else "baggage_delay" if "bag" in text else "delay")
    return IntakeClassification(claim_type=t, rationale="Classified from the claim description.")


def run(ctx, *, submission: dict) -> dict:
    payload = {"declared_claim_type": submission.get("claim_type_declared"),
               "items": [{"description": l["description"], "category": l["category"]}
                         for l in json.loads(submission.get("lines_json") or "[]")],
               "description": submission.get("description")}
    ctx.known_pii = {k: submission.get(k) for k in ("claimant_email", "claimant_phone", "claimant_address")}
    text = redact_for_model(json.dumps(payload, indent=1), claim_id=ctx.claim_id, agent_id=ctx.agent_id)
    value = ctx.agent("classify", IntakeClassification,
                      "Classify this travel claim. The full claim record is available through your claims tool.\n"
                      + wrap_as_data(text, ctx.claim_id, "claim_submission"),
                      fallback=lambda: _heuristic(submission), mock_plan=[("claims_system_get_claim", {})])
    category = CATEGORY[value.claim_type]
    ctx.tool("TOOL-CLAIMS-SYSTEM", "register", claim_id=ctx.claim_id, claim_type=value.claim_type, category=category)
    return {"claim_type": value.claim_type, "category": category, "rationale": value.rationale,
            "declared": submission.get("claim_type_declared"),
            "agrees_with_declared": value.claim_type == submission.get("claim_type_declared")}
