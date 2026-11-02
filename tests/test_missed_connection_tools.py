"""0.4.0 missed connection: tool contract changes (T005, T006; REQ-F-034, REQ-F-038; ADR-007)."""
from shared import db
from shared.tool_gateway import ToolPermissionError, call_tool


def _flight(no, date, sched_arr, actual_arr):
    db.upsert("flights", {"flight_no": no, "flight_date": date, "origin": "FRA", "destination": "LHR",
                          "scheduled_dep": f"{date} 08:00", "actual_dep": f"{date} 08:20", "delay_minutes": 20,
                          "status": "departed", "cause": "weather", "cause_announced_at": None,
                          "scheduled_arr": sched_arr, "actual_arr": actual_arr})


def test_flight_status_lookup_returns_arrival_fields(platform):
    _flight("ZZ101", "2026-08-11", "2026-08-11 09:45", "2026-08-11 11:10")
    f = call_tool("verification", "TOOL-FLIGHT-STATUS", "lookup", flight_no="zz 101", flight_date="2026-08-11")
    assert f["scheduled_arr"] == "2026-08-11 09:45" and f["actual_arr"] == "2026-08-11 11:10"
    assert f["arrival_delay_minutes"] == 85


def _claim(cid, trip, direction, ctype, approved, status="closed_paid", fps=()):
    db.upsert("claims", {"claim_id": cid, "trip_id": trip, "journey_direction": direction, "claim_type": ctype,
                         "claim_type_declared": ctype, "approved_usd": approved, "status": status,
                         "lines_json": db.jdump([{"line_id": f"L{i}", "receipt_fingerprint": fp} for i, fp in enumerate(fps, 1)])})


def test_journey_benefits_same_trip_and_direction_only(platform):
    _claim("CLM-JB-1", "TRIP-JB", "outward", "delay", 300.0, fps=["fpA"])
    _claim("CLM-JB-2", "TRIP-JB", None, "missed_connection", 120.0, fps=["fpB"])     # no direction = outward
    _claim("CLM-JB-3", "TRIP-JB", "return", "delay", 400.0, fps=["fpC"])             # other journey
    _claim("CLM-JB-4", "TRIP-JB", "outward", "baggage_loss", 900.0, fps=["fpD"])     # other benefit
    _claim("CLM-JB-5", "TRIP-JB", "outward", "delay", 50.0, status="closed_rejected", fps=["fpE"])  # not approved
    _claim("CLM-JB-6", "TRIP-OTHER", "outward", "delay", 70.0, fps=["fpF"])          # other trip
    r = call_tool("orchestrator", "TOOL-CLAIMS-SYSTEM", "journey_benefits", claim_id="CLM-JB-NEW",
                  trip_id="TRIP-JB", direction="outward", exclude_claim_id="CLM-JB-NEW")
    assert r == {"paid_delay_usd": 300.0, "paid_missed_connection_usd": 120.0, "paid_fingerprints": ["fpA", "fpB"]}


def test_journey_benefits_is_read_only_and_orchestrator_only(platform):
    import pytest
    with pytest.raises(ToolPermissionError):
        call_tool("assessment", "TOOL-CLAIMS-SYSTEM", "journey_benefits", trip_id="T", direction="outward",
                  exclude_claim_id="X")
