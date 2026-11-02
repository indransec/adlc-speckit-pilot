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


SCHEDULE_TOLERANCE_MIN = 15   # itinerary vs flight-status scheduled arrival (threat T13)


def verify_missed_connection(facts: dict, lookup, ref: dict | None = None) -> dict:
    """Facts for a missed connection claim (research R8). lookup(flight_no=, flight_date=) -> flight | None.

    verification_success is 1.0 only when the inbound delay is verified (source found, arrival delay > 0
    with a cause, consistent with the documents, and the delay really made the connection impossible);
    otherwise 0.0, which caps confidence at 0.35 -> investigation (row 6). A short connection or separate
    tickets are eligibility outcomes (rejection, row 5), not verification failures.
    """
    ref = ref or load_mct()
    out = {"inbound_leg": None, "onward_leg": None, "connection": None, "inbound_delay_verified": False,
           "inbound_arrival_delay_min": None, "inbound_cause": None, "strike_announced_before_booking": False,
           "flight_found": False, "discrepancies": [], "meets_threshold": None}
    inbound, onward, reason = match_inbound(facts.get("legs") or [], facts.get("flight_no"))
    if reason:
        out["discrepancies"].append(reason)
    if onward:
        out["onward_leg"] = onward["flight_no"]
    if not inbound:
        out["verification_success"] = 0.0
        return out
    out["inbound_leg"] = inbound["flight_no"]
    c = check_connection(inbound, onward, ref)
    out["connection"] = c
    f = lookup(flight_no=inbound["flight_no"], flight_date=inbound["flight_date"])
    if not f:
        out["discrepancies"].append(f"inbound flight {inbound['flight_no']} not found in the flight-status source")
        out["verification_success"] = 0.0
        return out
    out["flight_found"] = True
    delay, cause = f.get("arrival_delay_minutes"), f.get("cause")
    out.update(inbound_arrival_delay_min=delay, inbound_cause=cause,
               inbound_scheduled_arr=f.get("scheduled_arr"), inbound_actual_arr=f.get("actual_arr"))
    src_arr, doc_arr = _t(f.get("scheduled_arr")), _t(inbound.get("scheduled_arr"))
    if src_arr and doc_arr and abs((src_arr - doc_arr).total_seconds()) / 60 > SCHEDULE_TOLERANCE_MIN:
        out["discrepancies"].append(f"itinerary arrival {inbound['scheduled_arr']} differs from flight-status "
                                    f"scheduled arrival {f['scheduled_arr']}")
    if not delay or delay <= 0:
        out["discrepancies"].append(f"flight-status source shows {inbound['flight_no']} arrived on time")
    elif not cause:
        out["discrepancies"].append("flight-status source gives no cause for the inbound delay")
    on = lookup(flight_no=onward["flight_no"], flight_date=onward["flight_date"]) or {}
    onward_dep = _t(on.get("actual_dep") or on.get("scheduled_dep") or onward.get("scheduled_dep"))
    actual_arr = _t(f.get("actual_arr"))
    if delay and delay > 0 and actual_arr and onward_dep:
        left = int((onward_dep - actual_arr).total_seconds() // 60)
        out["time_left_for_connection_min"] = left
        if left >= c["mct_min"]:
            out["discrepancies"].append(f"inbound arrived in time for the onward flight ({left} min left, "
                                        f"minimum connection time {c['mct_min']} min)")
    if cause == "industrial_action" and f.get("cause_announced_at") and facts.get("booking_date") \
            and f["cause_announced_at"][:10] < facts["booking_date"]:
        out["strike_announced_before_booking"] = True
    out["inbound_delay_verified"] = bool(delay and delay > 0 and cause and not out["discrepancies"])
    out["verification_success"] = 1.0 if out["inbound_delay_verified"] else 0.0
    return out
