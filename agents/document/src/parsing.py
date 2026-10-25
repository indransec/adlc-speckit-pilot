"""Deterministic field parser for the synthetic document layouts.

Used as the mock-LLM path (offline / CI) and as the fallback when a live extraction call
fails. Extraction certainty is always computed here from the extracted fields, never
taken from model self-report (ADR-002).
"""
from __future__ import annotations

import re

LINE_RE = re.compile(r"^(\d+) \| (.+?) \| ([a-z_]+) \| ([\d.]+) ([A-Z]{3}) \| claimed USD ([\d.]+) \| purchased (\d{4}-\d{2}-\d{2})\s*$", re.M)
AMOUNT_RE = re.compile(r"([\d,]+\.\d{2})\s*([A-Z]{3})")

REQUIRED = {
    "claim_form": ["passenger_name", "policy_number", "flight_number", "flight_date", "lines", "total_claimed_usd"],
    "boarding_pass": ["passenger_name", "flight_number", "flight_date"],
    "airline_delay_letter": ["flight_number", "flight_date", "delay_hours"],
    "pir": ["passenger_name", "flight_number", "report_type"],
    "receipt": ["receipt_item", "receipt_amount"],
}


def _field(text: str, label: str) -> str | None:
    m = re.search(rf"(?im)^{re.escape(label)}\s*:\s*(.+?)\s*$", text)
    return m.group(1).strip() if m else None


def _num(v: str | None) -> float | None:
    try:
        return float(v.replace(",", "")) if v else None
    except ValueError:
        return None


def _narrative(text: str) -> str | None:
    m = re.search(r"(?s)What happened:\s*(.+?)\n\s*Items / expenses claimed:", text)
    return " ".join(m.group(1).split()) if m else None


def parse(text: str, doc_type: str) -> dict:
    f: dict = {"doc_type": doc_type, "illegible_fields": []}
    f["passenger_name"] = _field(text, "Claimant name") or _field(text, "Passenger name")
    f["policy_number"] = _field(text, "Policy number")
    f["flight_number"] = (_field(text, "Flight number") or "").replace(" ", "").upper() or None
    f["flight_date"] = _field(text, "Flight date")
    f["booking_date"] = _field(text, "Booking date")
    f["claim_type"] = _field(text, "Claim type")
    f["delay_hours"] = _num(_field(text, "Delay claimed (hours)") or _field(text, "Delay (hours)"))
    f["compensation_usd"] = _num(_field(text, "Compensation received from airline (USD)")
                                 or _field(text, "Compensation paid (USD)"))
    f["bag_delay_hours"] = _num(_field(text, "Bag delayed (hours)") or _field(text, "Bag delivered after (hours)"))
    f["pir_ref"] = _field(text, "PIR reference")
    f["report_type"] = _field(text, "Report type")
    f["narrative"] = _narrative(text)
    f["lines"] = [{"line_no": int(m[0]), "description": m[1].strip(), "category": m[2], "amount_orig": float(m[3]),
                   "currency": m[4], "claimed_usd": float(m[5]), "purchase_date": m[6]} for m in LINE_RE.findall(text)]
    f["total_claimed_usd"] = _num(_field(text, "Total claimed (USD)"))
    if doc_type == "receipt":
        item, amount = _field(text, "Item"), _field(text, "Amount")
        f["receipt_date"] = _field(text, "Date")
        f["receipt_item"] = None if not item or "[illegible]" in item else item
        m = AMOUNT_RE.search(amount or "")
        f["receipt_amount"] = _num(m.group(1)) if m else None
        f["receipt_currency"] = m.group(2) if m else None
        if not f["receipt_item"]:
            f["illegible_fields"].append("item")
        if f["receipt_amount"] is None:
            f["illegible_fields"].append("amount")
    return f


def certainty(fields: dict) -> float:
    req = REQUIRED.get(fields.get("doc_type"), [])
    if not req:
        return 1.0
    present = sum(1 for k in req if fields.get(k) not in (None, "", []))
    score = present / len(req)
    if fields.get("illegible_fields"):
        score -= 0.25 * len(fields["illegible_fields"])
    return round(max(score, 0.0), 2)
