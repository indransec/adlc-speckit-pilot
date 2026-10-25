"""Mock licensed flight-status source (TOOL-FLIGHT-STATUS)."""
from __future__ import annotations

from shared import db

OPERATIONS = {"lookup": "read"}


def lookup(flight_no: str, flight_date: str) -> dict | None:
    return db.one("SELECT * FROM flights WHERE flight_no = ? AND flight_date = ?",
                  [(flight_no or "").upper().replace(" ", ""), flight_date])
