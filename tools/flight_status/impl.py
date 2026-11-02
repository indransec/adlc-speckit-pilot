"""Mock licensed flight-status source (TOOL-FLIGHT-STATUS)."""
from __future__ import annotations

from datetime import datetime

from shared import db

OPERATIONS = {"lookup": "read"}


def _minutes(later: str | None, earlier: str | None) -> int | None:
    if not later or not earlier:
        return None
    fmt = "%Y-%m-%d %H:%M"
    return int((datetime.strptime(later, fmt) - datetime.strptime(earlier, fmt)).total_seconds() // 60)


def lookup(flight_no: str, flight_date: str) -> dict | None:
    f = db.one("SELECT * FROM flights WHERE flight_no = ? AND flight_date = ?",
               [(flight_no or "").upper().replace(" ", ""), flight_date])
    if f:  # arrival fields added at 0.4.0 for the missed connection inbound leg (contracts/tool-contracts.md)
        f["arrival_delay_minutes"] = _minutes(f.get("actual_arr"), f.get("scheduled_arr"))
    return f
