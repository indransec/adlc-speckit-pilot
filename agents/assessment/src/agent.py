"""Assessment agent (AGT-TRV-ASSESS): line judgements (model), amounts (rules engine),
deduction labels A/B with clause + evidence, and the decision package.

REQ-F-012, REQ-F-020, REQ-F-021, REQ-B-003, REQ-B-004. Recommend-only: routing is code.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

from shared import rag

from .calculator import TYPE_B_CLAUSES, Judgement, calculate

NON_ESSENTIAL = ("spa", "massage", "wellness", "alcohol", "bar ", "wine", "beer", "cinema", "entertainment",
                 "shopping", "souvenir", "upgrade", "casino")
EXCLUDED_ITEMS = ("cash", "passport", "ticket", "perishable")


class LineJudgement(BaseModel):
    line_id: str
    decision: Literal["eligible", "ineligible", "depreciate"]
    clause: Optional[str] = Field(None, description="Policy clause id justifying an ineligible/depreciate decision")
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list,
                                     description="Document ids (e.g. CLM-...-D01) or check ids supporting the decision")
    depreciation_pct: Optional[float] = None


class AssessmentJudgement(BaseModel):
    line_judgements: list[LineJudgement]
    recommendation: Literal["approve", "partial", "reject"]
    rationale: str = Field(description="Plain-language reasoning for the adjuster (3-5 sentences)")


def heuristic_judgements(claim_type: str, lines: list[dict], loss_date: str | None, form_doc: str | None) -> list[LineJudgement]:
    out = []
    for ln in lines:
        desc, cat, lid = ln["description"].lower() + " ", ln["category"], ln["line_id"]
        refs = [r for r in [form_doc] if r]
        if claim_type == "delay" and (cat == "non_essential" or any(w in desc for w in NON_ESSENTIAL)):
            out.append(LineJudgement(line_id=lid, decision="ineligible", clause="3.5", evidence_refs=refs,
                                     reason=f"'{ln['description']}' is a non-essential expense, not a necessary cost of the delay"))
        elif cat in ("cash", "documents") or any(w in desc for w in EXCLUDED_ITEMS):
            out.append(LineJudgement(line_id=lid, decision="ineligible", clause="6.3", evidence_refs=refs,
                                     reason=f"'{ln['description']}' is an excluded item"))
        elif ln["claimed_usd"] > 150 and not ln.get("receipt_doc_id"):
            out.append(LineJudgement(line_id=lid, decision="ineligible", clause="6.1",
                                     evidence_refs=refs + [f"check:no-receipt:{lid}"],
                                     reason=f"no readable receipt or proof of purchase for '{ln['description']}' (USD {ln['claimed_usd']:.2f} > 150)"))
        elif claim_type == "baggage_loss" and ln.get("purchase_date") and loss_date:
            years = (date.fromisoformat(loss_date) - date.fromisoformat(ln["purchase_date"])).days // 365
            if years >= 1:
                pct = min(10 * years, 50)
                out.append(LineJudgement(line_id=lid, decision="depreciate", clause="5.4", depreciation_pct=pct,
                                         evidence_refs=refs + [r for r in [ln.get("receipt_doc_id")] if r],
                                         reason=f"item bought {ln['purchase_date']}, {years} full year(s) old: {pct}% wear and tear"))
            else:
                out.append(LineJudgement(line_id=lid, decision="eligible"))
        else:
            out.append(LineJudgement(line_id=lid, decision="eligible"))
    return out


def rejection_reasons(claim_type: str, coverage: dict, verification: dict, refs: dict) -> list[dict]:
    r = []
    if not coverage.get("policy_found") or not coverage.get("policy_active"):
        r.append(("2.1", f"travel date is outside the period of cover ({coverage.get('period', 'policy not found')})", ["tool:policy-admin"]))
    elif not coverage.get("claimant_insured"):
        r.append(("2.2", "claimant is not named as an insured person", ["tool:policy-admin"]))
    if coverage.get("duplicate_of"):
        r.append(("7.2", f"the same event was already claimed under {coverage['duplicate_of']}", [f"claim:{coverage['duplicate_of']}"]))
    if claim_type == "delay" and verification.get("flight_found"):
        if verification.get("meets_threshold") is False:
            r.append(("3.3", f"verified delay was {verification['delay_hours_verified']:g} hours, less than 6 hours",
                      ["tool:flight-status", *refs.get("delay_letter", [])]))
        if verification.get("strike_announced_before_booking"):
            r.append(("3.3", "the strike causing the delay was publicly announced before the trip was booked",
                      ["tool:flight-status"]))
    if claim_type == "baggage_delay" and verification.get("meets_threshold") is False and verification.get("bag_delay_hours") is not None:
        r.append(("4.1", f"baggage was returned after {verification['bag_delay_hours']:g} hours, not more than 12 hours", refs.get("pir", [])))
    for e in coverage.get("exclusions", []):
        r.append((e["clause"], e["reason"], ["policy:" + e["clause"]]))
    seen, out = set(), []
    for clause, reason, ev in r:
        if (clause, reason) not in seen:
            seen.add((clause, reason))
            c = rag.clause(clause) or {}
            out.append({"clause": clause, "clause_title": c.get("title", ""), "reason": reason, "evidence_refs": ev})
    return out


def run(ctx, *, claim_type: str, document: dict, coverage: dict, verification: dict) -> dict:
    lines, facts = document["lines"], document["facts"]
    form_doc = document.get("claim_form_doc_id")
    doc_ids = {d["doc_id"] for d in document["documents"]}
    by_type = {}
    for d in document["documents"]:
        by_type.setdefault(d["doc_type"], []).append(d["doc_id"])
    refs = {"delay_letter": by_type.get("airline_delay_letter", []), "pir": by_type.get("pir", []),
            "compensation": by_type.get("airline_delay_letter", []) + ([form_doc] if form_doc else [])}
    limits = coverage.get("limits") or {}
    reasons = rejection_reasons(claim_type, coverage, verification, refs)

    clause_ctx = "\n".join(f"[{k}] {v}" for k, v in sorted(coverage.get("clause_texts", {}).items()))
    prompt = (
        f"Claim type: {claim_type}. Plan: {coverage.get('plan')}. Loss/flight date: {facts.get('flight_date')}.\n"
        f"Available evidence document ids: {sorted(doc_ids)}\n"
        f"Claim lines (receipt_doc_id null means no readable receipt):\n"
        + "\n".join(f"- {l['line_id']}: {l['description']} | {l['category']} | {l['amount_orig']} {l['currency']} | "
                    f"claimed USD {l['claimed_usd']} | purchased {l['purchase_date']} | receipt_doc_id {l.get('receipt_doc_id')}"
                    for l in lines)
        + f"\nCoverage findings: active={coverage.get('policy_active')}, insured={coverage.get('claimant_insured')}, "
          f"duplicate_of={coverage.get('duplicate_of')}, exclusions={coverage.get('exclusions')}\n"
          f"Verification: {verification.get('summary')} discrepancies={verification.get('discrepancies')}\n"
          f"Policy wording:\n{clause_ctx}\n\n"
          "Judge each line: eligible, ineligible (cite 3.5, 6.1 or 6.3) or depreciate (cite 5.4 with percent). "
          "Do NOT apply limits, excess, currency conversion or compensation offsets: the rules engine does that. "
          "Every ineligible/depreciate decision must cite evidence_refs. Then give your overall recommendation."
    )
    loss_date = facts.get("flight_date")

    def fallback():
        js = heuristic_judgements(claim_type, lines, loss_date, form_doc)
        rec = "reject" if reasons else "partial" if any(j.decision != "eligible" for j in js) else "approve"
        return AssessmentJudgement(line_judgements=js, recommendation=rec,
                                   rationale="Rule-based assessment (offline mode).")

    judged, _ = ctx.llm("judge_lines", AssessmentJudgement, prompt, fallback=fallback)

    judgements, relabelled = {}, []
    valid_lines = {l["line_id"] for l in lines}
    for j in judged.line_judgements:
        if j.line_id not in valid_lines or j.decision == "eligible":
            continue
        if j.clause not in TYPE_B_CLAUSES:
            relabelled.append({"line_id": j.line_id, "cited": j.clause})
        refs_ok = [r for r in j.evidence_refs if r in doc_ids or r.split(":")[0] in ("check", "policy", "tool")]
        pct = max(0.0, min(float(j.depreciation_pct or 0), 50.0))
        judgements[j.line_id] = Judgement(j.line_id, j.decision, j.clause, j.reason, refs_ok, pct)

    calc = calculate(claim_type, lines, limits, coverage.get("fx_rates") or {}, judgements,
                     float(facts.get("compensation_usd") or 0.0), refs) if limits else \
        {"claimed_usd": sum(l["claimed_usd"] for l in lines), "payable_usd": 0.0, "deductions": [], "reduction_pct": 100.0}

    if reasons or calc["payable_usd"] <= 0:
        recommendation, payable, deductions = "reject", 0.0, []
    else:
        payable, deductions = calc["payable_usd"], calc["deductions"]
        recommendation = "approve" if payable >= calc["claimed_usd"] - 0.005 else "partial"
    for d in deductions:
        c = rag.clause(d["clause"]) or {}
        d["clause_title"] = c.get("title", "")

    llm_rec = judged.recommendation
    agreement = (llm_rec == "reject") == (recommendation == "reject")   # model and rules agree on pay / don't pay
    cited = sorted({d["clause"] for d in deductions} | {r["clause"] for r in reasons} | {coverage.get("benefit_section", "")} - {""})
    package = {
        "recommended_outcome": recommendation, "claim_type": claim_type, "plan": coverage.get("plan"),
        "claimed_usd": calc["claimed_usd"], "recommended_usd": payable, "reduction_pct": calc["reduction_pct"],
        "deductions": deductions, "rejection_reasons": reasons,
        "line_judgements": [j.model_dump() for j in judged.line_judgements],
        "clauses_cited": {c: (rag.clause(c) or {}).get("text", "") for c in cited},
        "evidence_documents": sorted(doc_ids), "rationale": judged.rationale,
        "calculation_order": "FX(6.2) > judgements(B) > single item(5.2) > valuables(5.3) > offset(3.4) > excess(5.6) > section limit",
    }
    return {"recommendation": recommendation, "recommended_usd": payable, "claimed_usd": calc["claimed_usd"],
            "deductions": deductions, "rejection_reasons": reasons, "llm_recommendation": llm_rec,
            "agent_agreement": agreement, "relabelled_to_type_b": relabelled, "package": package}
