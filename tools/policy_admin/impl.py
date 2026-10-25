"""Mock policy administration system (TOOL-POLICY-ADMIN): policies, schedules, FX table, claims history."""
from __future__ import annotations

from shared import db

OPERATIONS = {"get_policy": "read", "claims_history": "read"}

PLAN_LIMITS = {
    "STANDARD": {"delay_limit": 500, "bag_delay_limit": 300, "bag_loss_limit": 2000, "single_item_limit": 500,
                 "valuables_limit": 400, "baggage_excess": 50},
    "PREMIER": {"delay_limit": 1000, "bag_delay_limit": 500, "bag_loss_limit": 3000, "single_item_limit": 750,
                "valuables_limit": 800, "baggage_excess": 0},
}
# Policy exchange rates (USD per unit) used under clause 6.2
FX_RATES = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "SGD": 0.74, "INR": 0.012, "JPY": 0.0067, "AED": 0.27}


def get_policy(policy_no: str) -> dict | None:
    p = db.one("SELECT * FROM policies WHERE policy_no = ?", [policy_no])
    if not p:
        return None
    p["insured_persons"] = db.jload(p["insured_persons"], [])
    p["limits"] = PLAN_LIMITS[p["plan"]]
    p["fx_rates"] = FX_RATES
    return p


def claims_history(policy_no: str, exclude_claim_id: str | None = None) -> list[dict]:
    rows = db.query("SELECT claim_id, claim_type, claim_type_declared, flight_no, flight_date, submitted_at, status, "
                    "claimed_usd, approved_usd, lines_json, trip_id FROM claims WHERE policy_no = ? AND claim_id != ?",
                    [policy_no, exclude_claim_id or ""])
    for r in rows:
        r["items"] = [l["description"] for l in db.jload(r.pop("lines_json"), [])]
    return rows
