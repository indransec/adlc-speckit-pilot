"""Mock SIU fraud model (TOOL-FRAUD-SCORE). Returns a score and level; it never decides a claim."""
from __future__ import annotations

from datetime import date

from shared import db

OPERATIONS = {"score": "read"}

LEVELS = [(0.7, "high"), (0.4, "medium"), (0.0, "low")]


def score(claim_id: str, signals: dict | None = None) -> dict:
    signals = signals or {}
    c = db.one("SELECT * FROM claims WHERE claim_id = ?", [claim_id])
    p = db.one("SELECT * FROM policies WHERE policy_no = ?", [c["policy_no"]]) if c else None
    reasons, s = [], 0.0
    if c and p and c["flight_date"] and p["purchased_at"]:
        days = (date.fromisoformat(c["flight_date"]) - date.fromisoformat(p["purchased_at"][:10])).days
        if days <= 3:
            s += 0.3; reasons.append(f"policy bought {days} day(s) before travel")
    if c:
        prior = db.scalar("SELECT COUNT(*) FROM claims WHERE policy_no = ? AND claim_id != ? AND submitted_at < ? "
                          "AND julianday(?) - julianday(submitted_at) <= 365",
                          [c["policy_no"], claim_id, c["submitted_at"], c["submitted_at"]]) or 0
        if prior >= 3:
            s += 0.3; reasons.append(f"{prior} prior claims in 12 months")
        elif prior == 2:
            s += 0.15; reasons.append("2 prior claims in 12 months")
        lines = db.jload(c["lines_json"], [])
        valuables = sum(l["claimed_usd"] for l in lines if l.get("category") in ("valuables", "electronics"))
        if valuables >= 1000:
            s += 0.15; reasons.append(f"high-value valuables USD {valuables:,.0f}")
        amounts = [l["claimed_usd"] for l in lines]
        if len(amounts) >= 2 and all(a >= 100 and a % 100 == 0 for a in amounts):
            s += 0.1; reasons.append("all amounts are round hundreds")
    if signals.get("injection_detected"):
        s += 0.25; reasons.append("instruction-like text in a document")
    if signals.get("doc_mismatch"):
        s += 0.1; reasons.append("cross-document mismatch")
    n_ai = min(int(signals.get("ai_flags", 0)), 2)
    if n_ai:
        s += 0.1 * n_ai; reasons.append(f"{n_ai} AI anomaly flag(s)")
    s = round(min(s, 1.0), 2)
    level = next(l for t, l in LEVELS if s >= t)
    return {"score": s, "level": level, "reasons": reasons, "model": "siu-synthetic-v1"}
