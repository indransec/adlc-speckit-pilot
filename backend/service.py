"""Claims service: the one place that drives the orchestrator graph.

Used by the FastAPI backend, the eval runner and the lifecycle simulator, so every path
exercises the same controls.
"""
from __future__ import annotations

import importlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from langgraph.types import Command

from shared import db, registry
from shared.audit import audit
from shared.config import release
from shared.hitl import approvers
from shared.telemetry import claim_context, init_telemetry, tracer

log = logging.getLogger(__name__)

IN_FLIGHT = {"processing", "awaiting_l1", "awaiting_l2", "awaiting_investigation", "awaiting_siu"}
REPROCESSABLE = {"new", "pending_documents", "error", "shadow_complete", "manual_queue", "registered"}
HIDDEN = {"bank_account_no", "bank_code", "passport_no"}

_graph = None
_graph_lock = threading.Lock()
_running: set[str] = set()
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="claim")


class ServiceError(ValueError):
    pass


def bootstrap() -> None:
    db.init_db()
    init_telemetry()


def graph():
    global _graph
    with _graph_lock:
        if _graph is None:
            _graph = importlib.import_module("claim_graph").build_graph()
        return _graph


def reset_graph() -> None:
    global _graph
    with _graph_lock:
        _graph = None


def _claim(claim_id: str) -> dict:
    c = db.one("SELECT * FROM claims WHERE claim_id = ?", [claim_id])
    if not c:
        raise ServiceError(f"unknown claim {claim_id}")
    return c


def _config(c: dict) -> dict:
    return {"configurable": {"thread_id": c["thread_id"] or f"{c['claim_id']}:r{c['run_no']}"}, "recursion_limit": 60}


# ------------------------------------------------------------------------------------------- processing
def process(claim_id: str) -> dict:
    c = _claim(claim_id)
    if c["historical"]:
        raise ServiceError("historical claims are read-only")
    if c["status"] in IN_FLIGHT or claim_id in _running:
        raise ServiceError(f"claim {claim_id} is already {c['status']}")
    if c["status"] not in REPROCESSABLE:
        raise ServiceError(f"claim {claim_id} is closed ({c['status']})")
    run_no = (c["run_no"] or 0) + 1
    _running.add(claim_id)
    try:
        cfg = {"configurable": {"thread_id": f"{claim_id}:r{run_no}"}, "recursion_limit": 60}
        with claim_context(claim_id), tracer().start_as_current_span("claim.process") as span:
            span.set_attribute("claim.run_no", run_no)
            try:
                graph().invoke({"claim_id": claim_id, "run_no": run_no, "steps": 0, "cost_usd": 0.0}, cfg)
            except Exception as e:
                log.exception("claim %s failed", claim_id)
                db.update("claims", "claim_id", claim_id, {"status": "error", "error": f"{type(e).__name__}: {e}"[:500],
                                                            "updated_at": db.now()})
                audit(claim_id, "processing_error", {"error": str(e)[:300]}, actor="orchestrator")
                raise
    finally:
        _running.discard(claim_id)
    return summary(claim_id)


def process_async(claim_id: str) -> None:
    c = _claim(claim_id)
    if c["status"] in IN_FLIGHT or claim_id in _running or c["status"] not in REPROCESSABLE:
        raise ServiceError(f"claim {claim_id} cannot be processed now ({c['status']})")
    _executor.submit(_safe_process, claim_id)


def _safe_process(claim_id: str):
    try:
        process(claim_id)
    except Exception:
        pass


def pending_interrupt(claim_id: str) -> dict | None:
    c = _claim(claim_id)
    if c["status"] not in IN_FLIGHT - {"processing"}:
        return None
    st = graph().get_state(_config(c))
    for t in st.tasks:
        for it in t.interrupts:
            return it.value
    return None


def decide(claim_id: str, user_id: str, decision: str, amount_usd: float | None = None, notes: str = "") -> dict:
    c = _claim(claim_id)
    pending = pending_interrupt(claim_id)
    if not pending:
        raise ServiceError(f"claim {claim_id} is not waiting for a human decision")
    if decision not in pending["options"]:
        raise ServiceError(f"decision must be one of {pending['options']}")
    if decision == "set_amount" and (amount_usd is None or amount_usd < 0):
        raise ServiceError("set_amount needs a non-negative amount_usd")
    prior_l1 = db.scalar("SELECT user_id FROM approvals WHERE claim_id = ? AND level = 'L1' ORDER BY id DESC LIMIT 1",
                         [claim_id]) if pending["queue"] == "L2" else None
    try:
        approvers.validate(pending["queue"], user_id, prior_l1)
    except approvers.ApproverError as e:
        audit(claim_id, "approval_rejected", {"user": user_id, "reason": str(e)}, actor=user_id)
        raise ServiceError(str(e))
    payload = {"user_id": user_id, "decision": decision, "amount_usd": amount_usd, "notes": notes}
    _running.add(claim_id)
    try:
        with claim_context(claim_id), tracer().start_as_current_span("claim.human_decision") as span:
            span.set_attribute("hitl.queue", pending["queue"])
            graph().invoke(Command(resume=payload), _config(c))
    finally:
        _running.discard(claim_id)
    return summary(claim_id)


# ------------------------------------------------------------------------------------------- documents
def chase_documents(days: int) -> list[dict]:
    """Advance the simulated clock for claims waiting on documents (REQ-F-006)."""
    from shared.registry import load_agent_package
    chasing = importlib.import_module(f"{load_agent_package('document')}.chasing")
    results = []
    for c in db.query("SELECT * FROM claims WHERE status = 'pending_documents'"):
        waited = (c["sim_days_waiting"] or 0) + days
        pkg = db.one("SELECT output FROM agent_outputs WHERE claim_id = ? AND agent_id = 'AGT-TRV-DOC' "
                     "ORDER BY run_no DESC LIMIT 1", [c["claim_id"]])
        missing = db.jload(pkg["output"])["missing_labels"] if pkg else ["Missing documents"]
        sent = c["reminders_sent"] or 0
        fields = {"sim_days_waiting": waited}
        for action, day in chasing.due_actions(waited, sent):
            if action == "reminder":
                registry.invoke("AGT-TRV-COMMS", "reminder", claim_id=c["claim_id"], run_no=c["run_no"],
                                claimant_name=c["claimant_name"], missing_labels=missing, day=day)
                sent += 1
                fields["reminders_sent"] = sent
            else:
                registry.invoke("AGT-TRV-COMMS", "closed_incomplete", claim_id=c["claim_id"], run_no=c["run_no"],
                                claimant_name=c["claimant_name"])
                fields.update(status="closed_incomplete", closed_at=db.now())
            results.append({"claim_id": c["claim_id"], "action": action, "day": day})
        db.update("claims", "claim_id", c["claim_id"], fields)
    return results


def upload_missing_documents(claim_id: str, process_after: bool = True) -> dict:
    from synthetic.materialize import add_document
    c = _claim(claim_id)
    if c["status"] != "pending_documents":
        raise ServiceError("claim is not waiting for documents")
    have = {d["doc_type"] for d in db.query("SELECT doc_type FROM documents WHERE claim_id = ?", [claim_id])}
    f = db.one("SELECT * FROM flights WHERE flight_no = ? AND flight_date = ?", [c["flight_no"], c["flight_date"]]) or {}
    added = []
    if c["claim_type_declared"] in ("baggage_delay", "baggage_loss") and "pir" not in have:
        added.append(add_document(claim_id, "pir", {
            "pir_ref": f"{(f.get('origin') or 'XXX')}{c['flight_no'][:2]}{abs(hash(claim_id)) % 90000 + 10000}",
            "passenger_name": c["claimant_name"], "flight_no": c["flight_no"], "flight_date": c["flight_date"],
            "report_type": "Delayed baggage" if c["claim_type_declared"] == "baggage_delay" else "Lost baggage",
            "bag_delay_hours": 24}, "pir.pdf"))
    if c["claim_type_declared"] == "delay" and "airline_delay_letter" not in have:
        added.append(add_document(claim_id, "airline_delay_letter", {
            "airline": "Airline", "passenger_name": c["claimant_name"], "flight_no": c["flight_no"],
            "flight_date": c["flight_date"], "scheduled_dep": f.get("scheduled_dep", ""), "actual_dep": f.get("actual_dep", ""),
            "delay_hours": (f.get("delay_minutes") or 0) / 60, "cause_text": f.get("cause") or "operational reasons",
            "compensation_usd": c["compensation_received_usd"] or 0.0}, "airline_delay_letter.pdf"))
    audit(claim_id, "documents_uploaded", {"documents": added}, actor="customer")
    return process(claim_id) if process_after else {"added": added}


# ------------------------------------------------------------------------------------------- read models
def summary(claim_id: str) -> dict:
    c = {k: v for k, v in _claim(claim_id).items() if k not in HIDDEN}
    c["lines"] = db.jload(c.pop("lines_json"), [])
    c["ground_truth"] = db.jload(c["ground_truth"], {})
    pkg = db.one("SELECT package FROM decision_packages WHERE claim_id = ? ORDER BY run_no DESC LIMIT 1", [claim_id])
    outs = db.query("SELECT agent_id, agent_version, output, run_no FROM agent_outputs WHERE claim_id = ? AND run_no = ?",
                    [claim_id, c["run_no"] or 0])
    return {
        "claim": c,
        "documents": db.query("SELECT doc_id, doc_type, filename, extraction_certainty FROM documents WHERE claim_id = ? "
                              "ORDER BY doc_id", [claim_id]),
        "package": db.jload(pkg["package"]) if pkg else None,
        "agent_outputs": {o["agent_id"]: db.jload(o["output"]) for o in outs},
        "approvals": db.query("SELECT * FROM approvals WHERE claim_id = ? ORDER BY id", [claim_id]),
        "payments": db.query("SELECT payment_id, amount_usd, payee_name, account_masked, status, created_at, approvers "
                             "FROM payments WHERE claim_id = ?", [claim_id]),
        "notifications": db.query("SELECT template, subject, body, sent_at, policy_ok, violations FROM notifications "
                                  "WHERE claim_id = ? ORDER BY id", [claim_id]),
        "audit": db.query("SELECT ts, actor, agent_id, agent_version, event, payload, duration_ms FROM audit_log "
                          "WHERE claim_id = ? ORDER BY id", [claim_id]),
        "llm": db.one("SELECT COUNT(*) calls, COALESCE(SUM(cost_usd),0) cost_usd, COALESCE(SUM(prompt_tokens),0) "
                      "prompt_tokens, COALESCE(SUM(completion_tokens),0) completion_tokens FROM llm_calls WHERE claim_id = ?",
                      [claim_id]),
        "guardrails": db.query("SELECT ts, agent_id, guardrail, action, detail FROM guardrail_events WHERE claim_id = ?",
                               [claim_id]),
        "pending": pending_interrupt(claim_id) if c["status"] in IN_FLIGHT else None,
        "release": release(),
    }


def list_claims(status: str | None = None) -> list[dict]:
    sql = ("SELECT claim_id, scenario, claimant_name, claim_type_declared, claim_type, claimed_usd, recommended_usd, "
           "approved_usd, status, queue, route, route_row, confidence, fraud_risk, submitted_at, updated_at, mode "
           "FROM claims WHERE historical = 0")
    params = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    return db.query(sql + " ORDER BY claim_id", params)


def queues() -> list[dict]:
    return db.query("SELECT claim_id, claimant_name, claim_type, status, queue, queue_since, route, route_row, "
                    "claimed_usd, recommended_usd, confidence, fraud_risk FROM claims WHERE queue IS NOT NULL "
                    "AND status IN ('awaiting_l1','awaiting_l2','awaiting_investigation','awaiting_siu','manual_queue') "
                    "ORDER BY queue_since")
