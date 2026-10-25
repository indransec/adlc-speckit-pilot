"""Orchestrator (AGT-TRV-ORCH): the claim flow as a LangGraph state machine.

  intake -> documents -> {coverage | verification | fraud} (parallel) -> assessment -> route
  route (deterministic, routing.py) -> straight-through | L1 | investigation | SIU | shadow | manual
  L1 / L2 / SIU are human-in-the-loop interrupts (checkpointed in SQLite, resumed by the workbench)
  finalize (approval token, ADR-003) -> settlement -> communication -> closed

The graph decides flow; agents recommend; routing.py decides oversight; humans or row 1
authorise payment. Rollout mode, kill switch and budgets are applied here.
"""
from __future__ import annotations

import operator
import sqlite3
from datetime import datetime
from typing import Annotated, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

import confidence as conf
from routing import (CONFIDENCE_CUTOFF, ClaimDecisionInput, Deduction, DeductionType, Recommendation, Route,
                     RoutingDecision, route_claim)
from shared import db, rag, registry
from shared.audit import audit
from shared.config import checkpoint_db_path, release
from shared.hitl import approval_token
from shared.runtime import get_runtime
from shared.telemetry import agent_span, set_routing_row
from shared.tool_gateway import call_tool

ORCH = "AGT-TRV-ORCH"
AGENTS = {"intake": "AGT-TRV-INTAKE", "document": "AGT-TRV-DOC", "coverage": "AGT-TRV-COV",
          "verification": "AGT-TRV-VER", "fraud": "AGT-TRV-FRAUD", "assessment": "AGT-TRV-ASSESS",
          "settlement": "AGT-TRV-SETTLE", "communication": "AGT-TRV-COMMS"}
ROW_OF = {r: int(r.value[3]) for r in Route}


class ClaimState(TypedDict, total=False):
    claim_id: str
    run_no: int
    submission: dict
    intake: dict
    document: dict
    coverage: dict
    verification: dict
    fraud: dict
    assessment: dict
    confidence: dict
    routing: dict
    overlay: str
    case_note: dict
    siu: dict
    l1: dict
    l2: dict
    final: dict
    token: Optional[str]
    payment: dict
    letter: dict
    halted: Optional[str]
    steps: Annotated[int, operator.add]
    cost_usd: Annotated[float, operator.add]


class CaseNote(BaseModel):
    summary: str = Field(description="3-4 sentence handover note for the adjuster: what was claimed, what was found, why it needs a human")
    focus_points: list[str] = Field(default_factory=list, description="What the adjuster should check first")


# ---------------------------------------------------------------------------------------------- helpers
def _claims(op: str, claim_id: str, **kw):
    return call_tool("orchestrator", "TOOL-CLAIMS-SYSTEM", op, claim_id=claim_id, **kw)


def _update(s: ClaimState, **fields):
    _claims("update_status", s["claim_id"], **fields)


def _delegate(s: ClaimState, key: str, entry: str = "run", **inputs) -> dict:
    return registry.invoke(AGENTS[key], entry, claim_id=s["claim_id"], run_no=s["run_no"], **inputs)


def _cost(out: dict) -> float:
    return out.get("_meta", {}).get("cost_usd", 0.0)


def _enter_queue(s: ClaimState, queue: str, status: str):
    c = _claims("get_claim", s["claim_id"])
    if c.get("queue") != queue or c.get("status") != status:
        _update(s, queue=queue, status=status, queue_since=db.now())
        audit(s["claim_id"], f"queued:{queue}", {"status": status}, actor="orchestrator", agent_id=ORCH, run_no=s["run_no"])
    return c


def _decision_input(s: ClaimState, **over) -> ClaimDecisionInput:
    a = s["assessment"]
    deds = [Deduction(d["amount_usd"], DeductionType(d["type"]), d["clause"], list(d.get("evidence_refs", [])))
            for d in a["deductions"]]
    base = dict(claim_id=s["claim_id"], claimed_usd=a["claimed_usd"], recommendation=Recommendation(a["recommendation"]),
                approved_usd=a["recommended_usd"], prior_approved_linked_usd=s["routing"]["prior_linked_usd"],
                deductions=deds, confidence=s["confidence"]["score"],
                fraud_risk="low" if (s.get("siu") or {}).get("decision") == "clear" else s["fraud"]["risk_level"])
    base.update(over)
    return ClaimDecisionInput(**base)


def _routing_dict(d: RoutingDecision, prior_linked: float, **extra) -> dict:
    return {"route": d.route.value, "row": ROW_OF[d.route], "approvals_required": d.approvals_required,
            "reasons": list(d.reasons), "prior_linked_usd": prior_linked, **extra}


def _approval(s: ClaimState, level: str, p: dict, outcome: str, amount: float, queued_at: str | None):
    rec = s["assessment"]
    override = 0 if level == "SIU" else int(p["decision"] not in ("accept", "accept_recommendation", "accept_l1")
                                            or abs(amount - rec["recommended_usd"]) > 0.005)
    db.insert("approvals", {"claim_id": s["claim_id"], "level": level, "user_id": p["user_id"], "decision": p["decision"],
                            "outcome": outcome, "amount_usd": amount, "override": override, "notes": p.get("notes", ""),
                            "queued_at": queued_at, "decided_at": db.now(), "release": release()})
    audit(s["claim_id"], f"human_decision:{level}", {"user": p["user_id"], "decision": p["decision"], "outcome": outcome,
                                                     "amount_usd": amount, "override": bool(override)},
          actor=p["user_id"], run_no=s["run_no"])


def _outcome_from_amount(amount: float, claimed: float) -> str:
    if amount <= 0.005:
        return "reject"
    return "approve" if amount >= claimed - 0.005 else "partial"


# ---------------------------------------------------------------------------------------------- nodes
def intake(s: ClaimState) -> dict:
    claim = _claims("get_claim", s["claim_id"])
    rt = get_runtime()
    _update(s, status="processing", processing_started_at=db.now(), run_no=s["run_no"], mode=rt["mode"],
            release=release(), thread_id=f"{s['claim_id']}:r{s['run_no']}", queue=None, error=None)
    out = _delegate(s, "intake", submission=claim)
    cost = _cost(out)
    if s["run_no"] == 1:
        cost += _cost(_delegate(s, "communication", "acknowledge", claimant_name=claim["claimant_name"],
                                claim_type=out["claim_type"]))
    return {"submission": claim, "intake": out, "steps": 2 if s["run_no"] == 1 else 1, "cost_usd": cost}


def documents(s: ClaimState) -> dict:
    out = _delegate(s, "document", claim_type=s["intake"]["claim_type"], submission=s["submission"])
    upd = {"document": out, "steps": 1, "cost_usd": _cost(out), "halted": None}
    if not out["complete"]:
        _update(s, status="pending_documents", docs_requested_at=db.now(), reminders_sent=0, queue=None)
        msg = _delegate(s, "communication", "request_documents", claimant_name=s["submission"]["claimant_name"],
                        missing_labels=out["missing_labels"])
        upd.update(halted="pending_documents", steps=2, cost_usd=_cost(out) + _cost(msg))
    return upd


def coverage(s: ClaimState) -> dict:
    d = s["document"]
    out = _delegate(s, "coverage", claim_type=s["intake"]["claim_type"], facts=d["facts"], lines=d["lines"])
    return {"coverage": out, "steps": 1, "cost_usd": _cost(out)}


def verification(s: ClaimState) -> dict:
    out = _delegate(s, "verification", claim_type=s["intake"]["claim_type"], facts=s["document"]["facts"])
    return {"verification": out, "steps": 1, "cost_usd": _cost(out)}


def fraud(s: ClaimState) -> dict:
    out = _delegate(s, "fraud", document=s["document"])
    return {"fraud": out, "steps": 1, "cost_usd": _cost(out)}


def assessment(s: ClaimState) -> dict:
    out = _delegate(s, "assessment", claim_type=s["intake"]["claim_type"], document=s["document"],
                    coverage=s["coverage"], verification=s["verification"])
    return {"assessment": out, "steps": 1, "cost_usd": _cost(out)}


def route(s: ClaimState) -> dict:
    rt = get_runtime()
    degraded = any(s[k].get("_meta", {}).get("degraded") for k in ("intake", "document", "coverage", "verification",
                                                                       "fraud", "assessment"))
    c = conf.compute(s["document"], s["coverage"], s["verification"], s["assessment"], degraded)
    prior = _claims("linked_approved_total", s["claim_id"], trip_id=s["submission"]["trip_id"],
                    exclude_claim_id=s["claim_id"])
    s = {**s, "confidence": c, "routing": {"prior_linked_usd": prior}}
    decision = route_claim(_decision_input(s))
    budget_reasons = []
    if s.get("steps", 0) > rt["max_agent_steps"]:
        budget_reasons.append(f"step budget exceeded ({s['steps']} > {rt['max_agent_steps']})")
    if s.get("cost_usd", 0.0) > rt["max_cost_per_claim_usd"]:
        budget_reasons.append(f"cost budget exceeded (USD {s['cost_usd']:.4f})")
    if budget_reasons and decision.route not in (Route.SIU, Route.INVESTIGATION):
        decision = RoutingDecision(Route.INVESTIGATION, 1, budget_reasons)
    routing = _routing_dict(decision, prior, aggregate_usd=round(prior + s["assessment"]["recommended_usd"], 2),
                            computed_route=decision.route.value)

    overlay = "normal"
    if rt["kill_switch"]:
        overlay = "manual"
    elif rt["mode"] == "shadow":
        overlay = "shadow"
    elif routing["row"] == 1 and (rt["mode"] == "assisted" or not rt["straight_through_enabled"]):
        routing["approvals_required"] = 1
        routing["reasons"].append(f"straight-through disabled ({rt['mode']} mode): L1 approves")
    set_routing_row(routing["row"])

    note, note_cost = None, 0.0
    if overlay == "normal" and routing["approvals_required"] > 0:
        ctx = registry.AgentContext(registry.manifest(ORCH), s["claim_id"], s["run_no"])
        with agent_span(ORCH, ctx.manifest["version"], s["claim_id"], "agent orchestrator.case_note"):
            a = s["assessment"]
            fb = CaseNote(summary=(f"{s['intake']['claim_type'].replace('_', ' ').title()} claim for USD {a['claimed_usd']:.2f}; "
                                   f"recommended {a['recommendation']} at USD {a['recommended_usd']:.2f}. "
                                   f"Routed to {routing['route']} because: {'; '.join(routing['reasons'])}."),
                          focus_points=s["document"]["mismatches"] + s["verification"].get("discrepancies", [])
                          + [f"{d['clause']}: {d['reason']}" for d in a["deductions"] if d["type"] == "B"])
            prompt = (f"Routing decision (made by rules, do not change it): {routing}\nAssessment: {a['package']}\n"
                      f"Verification: {s['verification'].get('summary')}\nDocument issues: {s['document']['mismatches']}\n"
                      f"Fraud signals: {s['fraud']['reasons'] + [f['description'] for f in s['fraud']['ai_flags']]}\n"
                      "Write the adjuster handover note.")
            n, meta = ctx.llm("case_note", CaseNote, prompt, fallback=lambda: fb)
            note, note_cost = n.model_dump(), meta.cost_usd

    started = _claims("get_claim", s["claim_id"]).get("processing_started_at")
    latency = (datetime.fromisoformat(db.now()) - datetime.fromisoformat(started)).total_seconds() if started else None
    package = {**s["assessment"]["package"], "claim_id": s["claim_id"], "routing": routing, "confidence": c,
               "fraud": {k: s["fraud"][k] for k in ("risk_level", "score", "reasons", "ai_flags")},
               "verification": {k: s["verification"].get(k) for k in ("summary", "discrepancies", "delay_hours_verified", "cause")},
               "documents": {k: s["document"][k] for k in ("documents", "mismatches", "unreadable_documents", "injection_docs")},
               "coverage": {k: s["coverage"].get(k) for k in ("policy_active", "claimant_insured", "plan", "benefit_section",
                                                               "duplicate_of", "retrieved", "rationale")},
               "case_note": note, "mode": rt["mode"], "overlay": overlay, "release": release(),
               "agent_versions": {k: s[k]["_meta"]["agent_version"] for k in
                                  ("intake", "document", "coverage", "verification", "fraud", "assessment")},
               "llm_mode": s["assessment"]["_meta"]["llm_mode"]}
    db.upsert("decision_packages", {"claim_id": s["claim_id"], "run_no": s["run_no"], "package": db.jdump(package),
                                    "created_at": db.now()})
    a = s["assessment"]
    _update(s, route=routing["route"], route_row=routing["row"], approvals_required=routing["approvals_required"],
            recommendation=a["recommendation"], recommended_usd=a["recommended_usd"], confidence=c["score"],
            fraud_risk=s["fraud"]["risk_level"], package_at=db.now(),
            package_latency_s=round(latency, 2) if latency is not None else None)
    audit(s["claim_id"], "routed", {"route": routing["route"], "approvals_required": routing["approvals_required"],
                                    "reasons": routing["reasons"], "confidence": c["score"], "overlay": overlay},
          actor="orchestrator", agent_id=ORCH, run_no=s["run_no"])

    upd = {"confidence": c, "routing": routing, "overlay": overlay, "case_note": note, "steps": 1, "cost_usd": note_cost}
    if overlay == "manual":
        _update(s, status="manual_queue", queue="MANUAL", queue_since=db.now())
    elif overlay == "shadow":
        _update(s, status="shadow_complete", queue=None)
    elif routing["approvals_required"] == 0:
        upd["final"] = {"outcome": a["recommendation"], "amount_usd": a["recommended_usd"], "decided_by": ORCH,
                        "route_row": routing["row"], "approvers": [{"level": "agent", "user_id": ORCH}]}
    return upd


def siu_review(s: ClaimState) -> dict:
    c = _enter_queue(s, "SIU", "awaiting_siu")
    p = interrupt({"queue": "SIU", "claim_id": s["claim_id"], "options": ["clear", "refer"]})
    if p["decision"] == "refer":
        _approval(s, "SIU", p, "siu_referred", 0.0, c.get("queue_since"))
        _update(s, status="siu_referred", queue=None, closed_at=db.now(), decided_by=p["user_id"])
        return {"siu": p, "halted": "siu_referred"}
    _approval(s, "SIU", p, "cleared", s["assessment"]["recommended_usd"], c.get("queue_since"))
    d = route_claim(_decision_input({**s, "siu": p}))
    if d.route == Route.STRAIGHT_THROUGH:  # a human has looked at it: at least L1 signs off
        d = RoutingDecision(Route.L1_AUTHORITY, 1, ["SIU cleared; L1 sign-off required"])
    routing = _routing_dict(d, s["routing"]["prior_linked_usd"], cleared_by_siu=p["user_id"])
    _update(s, route=routing["route"], route_row=routing["row"], approvals_required=routing["approvals_required"])
    return {"siu": p, "routing": routing}


def l1_review(s: ClaimState) -> dict:
    r, a = s["routing"], s["assessment"]
    queue = "INVESTIGATION" if r["row"] == 6 else "L1"
    c = _enter_queue(s, queue, "awaiting_investigation" if queue == "INVESTIGATION" else "awaiting_l1")
    p = interrupt({"queue": queue, "claim_id": s["claim_id"], "route": r["route"],
                   "options": ["accept", "set_amount", "remove_type_b", "reject"]})
    claimed, rec_amt = a["claimed_usd"], a["recommended_usd"]
    deds = a["deductions"]
    if p["decision"] == "accept":
        outcome, amount = a["recommendation"], rec_amt
    elif p["decision"] == "reject":
        outcome, amount = "reject", 0.0
    elif p["decision"] == "set_amount":
        amount = round(float(p["amount_usd"]), 2)
        outcome = _outcome_from_amount(amount, claimed)
        if amount >= claimed - 0.005:
            deds = []
    elif p["decision"] == "remove_type_b":
        deds = [d for d in deds if d["type"] == "A"]
        amount = round(claimed - sum(d["amount_usd"] for d in deds), 2)
        outcome = _outcome_from_amount(amount, claimed)
    else:
        raise ValueError(f"unknown L1 decision {p['decision']}")
    _approval(s, "L1", p, outcome, amount, c.get("queue_since"))

    rerouted = r
    if r["row"] == 6 or p["decision"] in ("remove_type_b", "set_amount"):
        # L1 has investigated: re-run the rules on the human-verified inputs
        ev = [Deduction(d["amount_usd"], DeductionType(d["type"]), d["clause"],
                        list(d.get("evidence_refs") or []) or [f"l1:{p['user_id']}"]) for d in deds]
        inp = _decision_input(s, deductions=ev, approved_usd=amount, confidence=1.0,
                              recommendation=Recommendation("reject" if outcome == "reject" else outcome))
        d = route_claim(inp)
        if d.route == Route.STRAIGHT_THROUGH:
            d = RoutingDecision(Route.L1_AUTHORITY, 1, ["L1 reviewed; no deductions remain"])
        rerouted = _routing_dict(d, r["prior_linked_usd"], rerouted_from=r["route"])
    need_l2 = outcome == "reject" or rerouted["approvals_required"] >= 2 or amount < rec_amt - 0.005
    l1 = {**p, "outcome": outcome, "amount_usd": amount, "deductions": deds, "needs_l2": need_l2}
    if need_l2:
        return {"l1": l1, "routing": rerouted}
    return {"l1": l1, "routing": rerouted, "final": {
        "outcome": outcome, "amount_usd": amount, "decided_by": p["user_id"], "route_row": rerouted["row"],
        "approvers": [{"level": "L1", "user_id": p["user_id"]}], "deductions": deds}}


def l2_review(s: ClaimState) -> dict:
    c = _enter_queue(s, "L2", "awaiting_l2")
    l1, a = s["l1"], s["assessment"]
    p = interrupt({"queue": "L2", "claim_id": s["claim_id"], "l1_view": {k: l1[k] for k in ("user_id", "outcome", "amount_usd")},
                   "options": ["accept_l1", "accept_recommendation", "set_amount", "reject"]})
    if p["decision"] == "accept_l1":
        outcome, amount, deds = l1["outcome"], l1["amount_usd"], l1["deductions"]
    elif p["decision"] == "accept_recommendation":
        outcome, amount, deds = a["recommendation"], a["recommended_usd"], a["deductions"]
    elif p["decision"] == "set_amount":
        amount = round(float(p["amount_usd"]), 2)
        outcome, deds = _outcome_from_amount(amount, a["claimed_usd"]), l1["deductions"]
    elif p["decision"] == "reject":
        outcome, amount, deds = "reject", 0.0, []
    else:
        raise ValueError(f"unknown L2 decision {p['decision']}")
    _approval(s, "L2", p, outcome, amount, c.get("queue_since"))
    row = s["routing"]["row"]
    return {"l2": p, "final": {"outcome": outcome, "amount_usd": amount, "decided_by": p["user_id"],
                               "route_row": row if row != 6 else 4,
                               "approvers": [{"level": "L1", "user_id": l1["user_id"]}, {"level": "L2", "user_id": p["user_id"]}],
                               "deductions": deds}}


def finalize(s: ClaimState) -> dict:
    f = s["final"]
    paying = f["outcome"] in ("approve", "partial") and f["amount_usd"] > 0
    _update(s, approved_usd=f["amount_usd"] if paying else 0.0, decided_by=f["decided_by"], queue=None,
            status="approved" if paying else "closed_rejected", **({} if paying else {"closed_at": db.now()}))
    token = None
    if paying:
        token = approval_token.issue(s["claim_id"], f["amount_usd"], f["route_row"], f["approvers"])
        audit(s["claim_id"], "approval_token_issued", {"route_row": f["route_row"], "amount_usd": f["amount_usd"],
                                                        "approvers": f["approvers"]},
              actor="orchestrator", agent_id=ORCH, run_no=s["run_no"])
    return {"token": token, "steps": 1}


def settlement(s: ClaimState) -> dict:
    f = s["final"]
    out = _delegate(s, "settlement", approval_token=s.get("token"), amount_usd=f["amount_usd"],
                    claimant_name=s["submission"]["claimant_name"])
    _update(s, status="closed_paid" if out["status"] == "instructed" else "payment_on_hold",
            **({"closed_at": db.now()} if out["status"] == "instructed" else {}))
    return {"payment": out, "steps": 1, "token": None}


def communicate(s: ClaimState) -> dict:
    f, a = s["final"], s["assessment"]
    deds = f.get("deductions", a["deductions"]) if f["outcome"] == "partial" else []
    if f["outcome"] == "partial" and abs(sum(d["amount_usd"] for d in deds) - (a["claimed_usd"] - f["amount_usd"])) > 0.01:
        bc = s["coverage"].get("benefit_section", "")
        deds = deds + [{"clause": bc, "clause_title": (rag.clause(bc) or {}).get("title", ""), "type": "B", "amount_usd":
                        round(a["claimed_usd"] - f["amount_usd"] - sum(d["amount_usd"] for d in deds), 2),
                        "reason": "a claims specialist adjusted the amount after reviewing your documents"}]
    reasons = a["rejection_reasons"] if f["outcome"] == "reject" else []
    if f["outcome"] == "reject" and not reasons:
        reasons = [{"clause": "6.1", "clause_title": "Receipts and proof of purchase",
                    "reason": "a claims specialist found the documents provided do not support the amounts claimed"}]
    out = _delegate(s, "communication", "decision_letter", claimant_name=s["submission"]["claimant_name"],
                    outcome=f["outcome"], claimed_usd=a["claimed_usd"], approved_usd=f["amount_usd"],
                    deductions=deds, reasons=reasons, payment=s.get("payment"))
    return {"letter": {k: out[k] for k in ("template", "sent", "violations", "subject")}, "steps": 1,
            "cost_usd": _cost(out)}


# ---------------------------------------------------------------------------------------------- edges
def after_documents(s: ClaimState):
    return END if s.get("halted") else ["coverage", "verification", "fraud"]


def after_route(s: ClaimState):
    if s["overlay"] in ("manual", "shadow"):
        return END
    if s.get("final"):
        return "finalize"
    return "siu_review" if s["routing"]["row"] == 7 else "l1_review"


def after_siu(s: ClaimState):
    return END if s.get("halted") == "siu_referred" else "l1_review"


def after_l1(s: ClaimState):
    return "l2_review" if s["l1"]["needs_l2"] else "finalize"


def after_finalize(s: ClaimState):
    return "settlement" if s.get("token") else "communicate"


def build_graph(checkpointer=None):
    g = StateGraph(ClaimState)
    for name, fn in [("intake", intake), ("documents", documents), ("coverage", coverage),
                     ("verification", verification), ("fraud", fraud), ("assessment", assessment), ("route", route),
                     ("siu_review", siu_review), ("l1_review", l1_review), ("l2_review", l2_review),
                     ("finalize", finalize), ("settlement", settlement), ("communicate", communicate)]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_edge("intake", "documents")
    g.add_conditional_edges("documents", after_documents, ["coverage", "verification", "fraud", END])
    g.add_edge(["coverage", "verification", "fraud"], "assessment")
    g.add_edge("assessment", "route")
    g.add_conditional_edges("route", after_route, ["finalize", "siu_review", "l1_review", END])
    g.add_conditional_edges("siu_review", after_siu, ["l1_review", END])
    g.add_conditional_edges("l1_review", after_l1, ["l2_review", "finalize"])
    g.add_edge("l2_review", "finalize")
    g.add_conditional_edges("finalize", after_finalize, ["settlement", "communicate"])
    g.add_edge("settlement", "communicate")
    g.add_edge("communicate", END)
    if checkpointer is None:
        from langgraph.checkpoint.sqlite import SqliteSaver
        checkpoint_db_path().parent.mkdir(parents=True, exist_ok=True)
        checkpointer = SqliteSaver(sqlite3.connect(checkpoint_db_path(), check_same_thread=False))
    return g.compile(checkpointer=checkpointer)


NODE_LABELS = {"intake": "Intake", "documents": "Documents", "coverage": "Coverage", "verification": "Verification",
               "fraud": "Fraud screening", "assessment": "Assessment", "route": "Routing (code)",
               "siu_review": "SIU review", "l1_review": "L1 review", "l2_review": "L2 review",
               "finalize": "Approval token", "settlement": "Settlement", "communicate": "Decision letter"}
_ = CONFIDENCE_CUTOFF  # re-exported for the UI
