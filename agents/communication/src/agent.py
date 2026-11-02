"""Communication agent (AGT-TRV-COMMS): customer messages from approved templates only.

REQ-F-002 acknowledgement, REQ-F-006 document chasing, REQ-F-031 / REQ-B-008 decision
letters with clause citations and appeal rights. The model may only fill two template
slots (an opening sentence and a plain-language explanation per deduction); slot text
is checked against the output policy and replaced by approved fallback text on failure.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from shared.audit import guardrail_event
from shared.guardrails.output_policy import check_slot_text

TOOL = "TOOL-NOTIFY"
LABEL = {"delay": "travel delay", "baggage_delay": "baggage delay", "baggage_loss": "baggage loss",
         "missed_connection": "missed connection"}
MC_TEMPLATE = {"approve": "missed_connection_paid", "partial": "missed_connection_partial",
               "reject": "missed_connection_rejected"}   # 0.4.0, used once Compliance has approved them (G4)


class Explanation(BaseModel):
    clause: str
    text: str = Field(description="One plain-language sentence explaining this item to the customer")


class LetterSlots(BaseModel):
    opening: str = Field(description="One warm, plain sentence thanking the customer; no amounts, no internal terms")
    explanations: list[Explanation] = Field(default_factory=list)


def _send(ctx, template: str, context: dict) -> dict:
    res = ctx.tool(TOOL, "send", template=template, context=context)
    ctx.audit("message_sent" if res["sent"] else "message_blocked", {"template": template, "violations": res["violations"]})
    return res


def acknowledge(ctx, *, claimant_name: str, claim_type: str) -> dict:
    return _send(ctx, "acknowledgement", {"claimant_name": claimant_name, "claim_id": ctx.claim_id,
                                          "claim_label": LABEL.get(claim_type, "travel")})


def request_documents(ctx, *, claimant_name: str, missing_labels: list[str]) -> dict:
    return _send(ctx, "documents_request", {"claimant_name": claimant_name, "claim_id": ctx.claim_id,
                                            "missing": missing_labels})


def reminder(ctx, *, claimant_name: str, missing_labels: list[str], day: int) -> dict:
    return _send(ctx, "documents_reminder", {"claimant_name": claimant_name, "claim_id": ctx.claim_id,
                                             "missing": missing_labels, "day": day})


def closed_incomplete(ctx, *, claimant_name: str) -> dict:
    return _send(ctx, "closed_incomplete", {"claimant_name": claimant_name, "claim_id": ctx.claim_id})


def _fallback_text(item: dict) -> str:
    return item["reason"][:1].upper() + item["reason"][1:] + "."


def _approved(template_id: str) -> bool:
    from tools.notify.impl import approved
    return (approved().get(template_id) or {}).get("status") == "approved"


def decision_letter(ctx, *, claimant_name: str, outcome: str, claimed_usd: float, approved_usd: float,
                    deductions: list[dict], reasons: list[dict], payment: dict | None,
                    claim_type: str | None = None, connection: dict | None = None) -> dict:
    items = reasons if outcome == "reject" else deductions
    prompt = ("Write the customer-facing slots for a travel insurance decision letter. The template already states "
              "the amounts, clause numbers and appeal rights. Do not mention internal processes, scores or reviews. "
              "Read a clause with your policy wording tool if you need its exact meaning.\n"
              f"Outcome: {outcome}\nItems to explain:\n"
              + "\n".join(f"- clause {i['clause']} ({i.get('clause_title', '')}): {i['reason']}" for i in items))
    fallback = LetterSlots(opening="Thank you for your patience while we reviewed your claim.",
                           explanations=[Explanation(clause=i["clause"], text=_fallback_text(i)) for i in items])
    clauses = sorted({i["clause"] for i in items})
    slots = ctx.agent("letter_slots", LetterSlots, prompt, fallback=lambda: fallback,
                      mock_plan=[[("policy_wording_get_clause", {"clause_id": c}) for c in clauses]] if clauses else [])
    problems = check_slot_text(slots.opening) + [p for e in slots.explanations for p in check_slot_text(e.text)]
    if problems:
        guardrail_event(ctx.claim_id, "output-policy", "slot-text-replaced", "; ".join(problems), ctx.agent_id)
        slots = fallback
    expl = {e.clause: e.text for e in slots.explanations}

    def explain(i: dict) -> str:
        return expl.get(i["clause"]) or _fallback_text(i)
    if payment and payment.get("status") == "instructed":
        note = f"We have instructed payment of USD {approved_usd:.2f} to your account ending {payment['account_masked'][-4:]}."
    else:
        note = "We will confirm the payment to you separately."
    template = {"approve": "decision_approved", "partial": "decision_partial", "reject": "decision_rejected"}[outcome]
    if claim_type == "missed_connection" and connection and _approved(MC_TEMPLATE[outcome]):
        template = MC_TEMPLATE[outcome]
    ctx_vars = {"claimant_name": claimant_name, "claim_id": ctx.claim_id, "outcome": outcome, "opening": slots.opening,
                "claimed_usd": claimed_usd, "approved_usd": approved_usd, "payment_note": note,
                "deductions": [{"clause": d["clause"], "clause_title": d.get("clause_title", ""),
                                "amount_usd": d["amount_usd"], "explanation": explain(d)} for d in deductions]
                if outcome == "partial" else [],
                "reasons": [{"clause": r["clause"], "clause_title": r.get("clause_title", ""),
                             "explanation": explain(r)} for r in reasons] if outcome == "reject" else [],
                "inbound_leg": (connection or {}).get("inbound_leg"), "onward_leg": (connection or {}).get("onward_leg"),
                "connection_airport": (connection or {}).get("airport")}
    res = _send(ctx, template, ctx_vars)
    return {"template": template, "sent": res["sent"], "violations": res["violations"], "subject": res["subject"],
            "body": res["body"]}
