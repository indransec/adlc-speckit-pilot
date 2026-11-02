"""Missed connection rules in code (release 0.4.0; REQ-F-033, REQ-F-035, REQ-B-013; ADR-006).

Which leg fed the missed connection, whether the booked connection met the minimum connection time
(MCT) and whether both legs were on one ticket are decided here, never by a model.
A leg is a dict with flight_no, flight_date, origin, destination, scheduled_dep, scheduled_arr
('YYYY-MM-DD HH:MM') and booking_ref.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path

import yaml

from shared.config import project_root

MCT_FILE = Path("reference") / "minimum-connection-times.yaml"
FMT = "%Y-%m-%d %H:%M"


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text())


def load_mct() -> dict:
    return _load(str(project_root() / MCT_FILE))


def mct_for(airport: str | None, ref: dict | None = None) -> dict:
    ref = ref or load_mct()
    minutes = (ref.get("airports") or {}).get((airport or "").upper())
    if minutes is None:
        return {"mct_min": int(ref.get("fallback_minutes", 60)), "mct_source": "fallback", "mct_as_of": ref.get("as_of")}
    return {"mct_min": int(minutes), "mct_source": ref.get("source"), "mct_as_of": str(ref.get("as_of"))}


def _t(s: str | None) -> datetime | None:
    try:
        return datetime.strptime(s, FMT) if s else None
    except ValueError:
        return None


def match_inbound(legs: list[dict], onward_flight_no: str | None) -> tuple[dict | None, dict | None, str | None]:
    """Return (inbound, onward, reason). reason is set (and inbound None) when the match is not unique."""
    norm = lambda f: (f or "").upper().replace(" ", "")
    onward = [l for l in legs if norm(l.get("flight_no")) == norm(onward_flight_no)]
    if len(onward) != 1:
        return None, None, f"onward flight {onward_flight_no} not found exactly once in the itinerary"
    onward = onward[0]
    dep = _t(onward.get("scheduled_dep"))
    candidates = [l for l in legs if l is not onward and (l.get("destination") or "").upper() == (onward.get("origin") or "").upper()
                  and _t(l.get("scheduled_arr")) and dep and _t(l["scheduled_arr"]) <= dep]
    if not candidates:
        return None, onward, f"no leg arrives at {onward.get('origin')} before the onward departure"
    if len(candidates) > 1:
        return None, onward, f"{len(candidates)} legs arrive at {onward.get('origin')} before the onward departure (ambiguous)"
    return candidates[0], onward, None


def check_connection(inbound: dict, onward: dict, ref: dict | None = None) -> dict:
    """Booked connection time vs MCT at the connection airport; equal passes (data-model.md)."""
    booked = None
    arr, dep = _t(inbound.get("scheduled_arr")), _t(onward.get("scheduled_dep"))
    if arr and dep:
        booked = int((dep - arr).total_seconds() // 60)
    m = mct_for(onward.get("origin"), ref)
    refs = {inbound.get("booking_ref"), onward.get("booking_ref")}
    return {"airport": (onward.get("origin") or "").upper(), "booked_connection_min": booked, **m,
            "connection_time_ok": booked is not None and booked >= m["mct_min"],
            "single_ticket": None not in refs and len(refs) == 1}
