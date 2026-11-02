"""Mock claims system of record (TOOL-CLAIMS-SYSTEM). No payment fields can be written here."""
from __future__ import annotations

from shared import db

OPERATIONS = {"register": "create", "update_status": "update-status", "get_claim": "read",
              "linked_approved_total": "read", "journey_benefits": "read"}

APPROVED_STATES = ("approved", "closed_paid", "payment_on_hold")
UPDATABLE = {"status", "queue", "queue_since", "route", "route_row", "approvals_required", "recommendation",
             "recommended_usd", "approved_usd", "confidence", "fraud_risk", "mode", "package_at",
             "package_latency_s", "closed_at", "docs_requested_at", "reminders_sent", "decided_by",
             "claim_type", "category", "error", "processing_started_at", "thread_id", "run_no", "release"}
HIDDEN = {"bank_account_no", "bank_code", "bank_account_holder", "passport_no", "ground_truth", "scenario"}


def register(claim_id: str, claim_type: str, category: str) -> dict:
    update_status(claim_id, status="registered", claim_type=claim_type, category=category)
    return {"claim_id": claim_id, "status": "registered"}


def update_status(claim_id: str, **fields) -> None:
    bad = set(fields) - UPDATABLE
    if bad:
        raise PermissionError(f"claims system: fields not updatable via update-status: {sorted(bad)}")
    db.update("claims", "claim_id", claim_id, {**fields, "updated_at": db.now()})


def get_claim(claim_id: str) -> dict | None:
    c = db.one("SELECT * FROM claims WHERE claim_id = ?", [claim_id])
    return {k: v for k, v in c.items() if k not in HIDDEN} if c else None


def linked_approved_total(trip_id: str, exclude_claim_id: str) -> float:
    marks = ",".join("?" for _ in APPROVED_STATES)
    v = db.scalar(f"SELECT COALESCE(SUM(approved_usd), 0) FROM claims WHERE trip_id = ? AND claim_id != ? "
                  f"AND status IN ({marks})", [trip_id, exclude_claim_id, *APPROVED_STATES])
    return float(v or 0)


def journey_benefits(trip_id: str, direction: str | None, exclude_claim_id: str) -> dict:
    """Approved travel delay / missed connection money on OTHER claims of the same journey (ADR-007).

    A journey is trip_id + direction; a claim without a recorded direction counts as outward.
    Fingerprints are those of the lines of approved claims (exact-match duplicate check, clause 7.2).
    """
    marks = ",".join("?" for _ in APPROVED_STATES)
    rows = db.query(
        f"SELECT claim_type, claim_type_declared, approved_usd, lines_json FROM claims WHERE trip_id = ? "
        f"AND claim_id != ? AND COALESCE(journey_direction, 'outward') = ? AND status IN ({marks})",
        [trip_id, exclude_claim_id, direction or "outward", *APPROVED_STATES])
    out = {"paid_delay_usd": 0.0, "paid_missed_connection_usd": 0.0, "paid_fingerprints": []}
    for r in rows:
        ctype = r["claim_type"] or r["claim_type_declared"]
        if ctype == "delay":
            out["paid_delay_usd"] += float(r["approved_usd"] or 0)
        elif ctype == "missed_connection":
            out["paid_missed_connection_usd"] += float(r["approved_usd"] or 0)
        else:
            continue
        for ln in db.jload(r["lines_json"]) or []:
            if ln.get("receipt_fingerprint"):
                out["paid_fingerprints"].append(ln["receipt_fingerprint"])
    out["paid_delay_usd"] = round(out["paid_delay_usd"], 2)
    out["paid_missed_connection_usd"] = round(out["paid_missed_connection_usd"], 2)
    return out
