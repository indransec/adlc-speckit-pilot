"""Document agent (AGT-TRV-DOC): extract, cross-check and completeness-check claim documents.

REQ-F-004 extraction + cross-document consistency, REQ-F-005 completeness checklist.
Guardrails on every document before any model call: injection scan + wrap-as-data
(REQ-B-010) and PII redaction incl. bank details (REQ-B-009).
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Literal, Optional

from pydantic import BaseModel, Field

from shared.guardrails.injection import guard_document
from shared.guardrails.pii import redact_for_model

from .parsing import certainty, parse

TOOL = "TOOL-DOCUMENT-STORE"
CHECKLIST = {
    "delay": ["claim_form", "boarding_pass", "airline_delay_letter"],
    "baggage_delay": ["claim_form", "boarding_pass", "pir"],
    "baggage_loss": ["claim_form", "boarding_pass", "pir"],
}
DOC_LABEL = {"claim_form": "Completed claim form", "boarding_pass": "Boarding pass",
             "airline_delay_letter": "Airline letter confirming the delay and its cause",
             "pir": "Property Irregularity Report (PIR) from the airline", "receipt": "Receipts"}
VALUABLE = {"valuables", "electronics"}


class ExtractedLine(BaseModel):
    line_no: int
    description: str
    category: str
    amount_orig: float
    currency: str
    claimed_usd: float
    purchase_date: Optional[str] = None


class DocExtraction(BaseModel):
    """Fields extracted from ONE document. Use null for anything not present; list unreadable fields."""
    doc_type: Literal["claim_form", "boarding_pass", "airline_delay_letter", "pir", "receipt", "other"]
    passenger_name: Optional[str] = Field(None, description="Claimant or passenger full name")
    policy_number: Optional[str] = None
    flight_number: Optional[str] = None
    flight_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    booking_date: Optional[str] = None
    claim_type: Optional[str] = None
    delay_hours: Optional[float] = Field(None, description="Delay in hours stated in this document")
    compensation_usd: Optional[float] = None
    bag_delay_hours: Optional[float] = None
    pir_ref: Optional[str] = None
    report_type: Optional[str] = None
    narrative: Optional[str] = None
    lines: list[ExtractedLine] = Field(default_factory=list, description="Claim form expense/item table rows")
    total_claimed_usd: Optional[float] = None
    receipt_item: Optional[str] = None
    receipt_amount: Optional[float] = None
    receipt_currency: Optional[str] = None
    receipt_date: Optional[str] = None
    illegible_fields: list[str] = Field(default_factory=list)


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def run(ctx, *, claim_type: str, submission: dict) -> dict:
    docs = ctx.tool(TOOL, "list_documents", claim_id=ctx.claim_id)
    known_pii = {k: submission.get(k) for k in ("claimant_email", "claimant_phone", "claimant_address")}
    extracted, injection_docs, redactions = [], [], 0
    for d in docs:
        raw = ctx.tool(TOOL, "read_text", doc_id=d["doc_id"])
        safe = redact_for_model(raw, known=known_pii, claim_id=ctx.claim_id, agent_id=ctx.agent_id)
        redactions += safe.count("[REDACTED")
        wrapped, inj = guard_document(safe, doc_id=d["doc_id"], doc_type=d["doc_type"],
                                      claim_id=ctx.claim_id, agent_id=ctx.agent_id)
        if inj.detected:
            injection_docs.append({"doc_id": d["doc_id"], "matches": [m for _, m in inj.matches][:3]})
        prompt = (f"Document type (from the document store): {d['doc_type']}\n"
                  f"Extract the fields for this document type.\n\n{wrapped}")
        value, meta = ctx.llm(f"extract:{d['doc_type']}", DocExtraction, prompt,
                              fallback=lambda s=safe, t=d["doc_type"]: DocExtraction(**parse(s, t)))
        fields = value.model_dump()
        fields["doc_type"] = d["doc_type"]  # store metadata is authoritative for the type
        cert = certainty(fields)
        if meta.degraded:
            cert = round(cert * 0.8, 2)
        ctx.tool(TOOL, "write_extracted", doc_id=d["doc_id"], fields=fields, certainty=cert)
        extracted.append({"doc_id": d["doc_id"], "doc_type": d["doc_type"], "certainty": cert, "fields": fields,
                          "injection_detected": inj.detected})

    by_type: dict[str, list] = {}
    for e in extracted:
        by_type.setdefault(e["doc_type"], []).append(e)
    form = (by_type.get("claim_form") or [{}])[0].get("fields", {})
    form_doc = (by_type.get("claim_form") or [{}])[0].get("doc_id")

    # --- completeness (REQ-F-005)
    missing = [t for t in CHECKLIST[claim_type] if t not in by_type]

    # --- cross-document consistency (REQ-F-004)
    mismatches = []
    for e in extracted:
        f = e["fields"]
        if e["doc_type"] in ("boarding_pass", "pir", "airline_delay_letter"):
            if f.get("passenger_name") and form.get("passenger_name") and _norm(f["passenger_name"]) != _norm(form["passenger_name"]):
                mismatches.append(f"{e['doc_id']}: passenger name '{f['passenger_name']}' differs from claim form")
            if f.get("flight_number") and form.get("flight_number") and f["flight_number"].upper() != form["flight_number"].upper():
                mismatches.append(f"{e['doc_id']}: flight number {f['flight_number']} differs from claim form {form['flight_number']}")
            if f.get("flight_date") and form.get("flight_date") and f["flight_date"] != form["flight_date"]:
                mismatches.append(f"{e['doc_id']}: flight date {f['flight_date']} differs from claim form {form['flight_date']}")
    if form.get("policy_number") and form["policy_number"] != submission.get("policy_no"):
        mismatches.append(f"claim form policy {form['policy_number']} differs from submitted policy {submission.get('policy_no')}")
    letter = (by_type.get("airline_delay_letter") or [{}])[0].get("fields", {})
    if letter.get("compensation_usd") is not None and form.get("compensation_usd") is not None \
            and abs(letter["compensation_usd"] - form["compensation_usd"]) > 0.01:
        mismatches.append("compensation on airline letter differs from claim form")

    # --- lines: link receipts as evidence
    receipts = by_type.get("receipt", [])
    used = set()
    lines = []
    for ln in form.get("lines", []):
        match = None
        for r in receipts:
            rf = r["fields"]
            if r["doc_id"] in used or not rf.get("receipt_item"):
                continue
            if _similar(rf["receipt_item"], ln["description"]) >= 0.85:
                match = r
                if rf.get("receipt_amount") is not None and abs(rf["receipt_amount"] - ln["amount_orig"]) > 0.01:
                    mismatches.append(f"{r['doc_id']}: receipt amount {rf['receipt_amount']:.2f} differs from "
                                      f"claimed {ln['amount_orig']:.2f} for '{ln['description']}'")
                break
        if match:
            used.add(match["doc_id"])
        lines.append({**ln, "line_id": f"L{ln['line_no']}", "valuable": ln["category"] in VALUABLE,
                      "receipt_doc_id": match["doc_id"] if match else None})
    unreadable = [r["doc_id"] for r in receipts if r["fields"].get("illegible_fields")]

    certs = [e["certainty"] for e in extracted] or [0.0]
    pir_f = (by_type.get("pir") or [{}])[0].get("fields", {})
    return {
        "documents": [{k: e[k] for k in ("doc_id", "doc_type", "certainty", "injection_detected")} for e in extracted],
        "claim_form_doc_id": form_doc,
        "lines": lines,
        "facts": {
            "claimant_name": form.get("passenger_name"), "policy_no": form.get("policy_number"),
            "flight_no": form.get("flight_number"), "flight_date": form.get("flight_date"),
            "booking_date": form.get("booking_date"), "delay_hours_claimed": form.get("delay_hours"),
            "delay_hours_letter": letter.get("delay_hours"),
            "compensation_usd": max(form.get("compensation_usd") or 0.0, letter.get("compensation_usd") or 0.0),
            "bag_delay_hours": pir_f.get("bag_delay_hours") or form.get("bag_delay_hours"),
            "pir_ref": pir_f.get("pir_ref"), "report_type": pir_f.get("report_type"),
            "total_claimed_usd": form.get("total_claimed_usd"), "narrative": form.get("narrative"),
        },
        "mismatches": mismatches,
        "unreadable_documents": unreadable,
        "missing_documents": missing,
        "missing_labels": [DOC_LABEL[m] for m in missing],
        "complete": not missing,
        "extraction_certainty": min(certs),
        "consistent": not mismatches,
        "injection_detected": bool(injection_docs),
        "injection_docs": injection_docs,
        "pii_redactions": redactions,
    }
