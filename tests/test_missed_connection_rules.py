"""0.4.0 missed connection rules in code (T007; REQ-F-033, REQ-F-035, REQ-B-013; ADR-006)."""
import importlib

from shared.registry import load_agent_package

conn = importlib.import_module(f"{load_agent_package('verification')}.connection")
REF = {"as_of": "2026-11-02", "source": "test", "fallback_minutes": 60, "airports": {"LHR": 90, "FRA": 45}}


def leg(no, o, d, dep, arr, ref="ABC123", date="2026-08-10"):
    return {"flight_no": no, "flight_date": date, "origin": o, "destination": d,
            "scheduled_dep": f"{date} {dep}", "scheduled_arr": f"{date} {arr}", "booking_ref": ref}


def test_unique_inbound_match():
    legs = [leg("LH903", "FRA", "LHR", "08:00", "09:45"), leg("BA175", "LHR", "JFK", "11:30", "19:30")]
    inbound, onward, reason = conn.match_inbound(legs, "BA 175")
    assert reason is None and inbound["flight_no"] == "LH903" and onward["flight_no"] == "BA175"


def test_three_legs_picks_the_leg_into_the_connection_airport():
    legs = [leg("UA901", "SFO", "FRA", "06:00", "07:00"), leg("LH903", "FRA", "LHR", "08:00", "09:45"),
            leg("BA175", "LHR", "JFK", "11:30", "19:30")]
    inbound, _, reason = conn.match_inbound(legs, "BA175")
    assert reason is None and inbound["flight_no"] == "LH903"   # not the first leg (REQ-B-013)


def test_ambiguous_inbound_returns_reason():
    legs = [leg("LH903", "FRA", "LHR", "07:00", "08:45"), leg("AF1080", "CDG", "LHR", "08:00", "09:15"),
            leg("BA175", "LHR", "JFK", "11:30", "19:30")]
    inbound, onward, reason = conn.match_inbound(legs, "BA175")
    assert inbound is None and onward is not None and "ambiguous" in reason


def test_onward_not_in_itinerary():
    inbound, onward, reason = conn.match_inbound([leg("LH903", "FRA", "LHR", "08:00", "09:45")], "BA175")
    assert inbound is None and onward is None and "not found" in reason


def test_connection_time_equal_passes_and_short_fails():
    inbound = leg("LH903", "FRA", "LHR", "08:00", "09:45")
    ok = conn.check_connection(inbound, leg("BA175", "LHR", "JFK", "11:15", "19:30"), REF)   # 90 = MCT
    short = conn.check_connection(inbound, leg("BA175", "LHR", "JFK", "11:00", "19:30"), REF)  # 75 < 90
    assert ok["booked_connection_min"] == 90 and ok["connection_time_ok"] and ok["mct_source"] == "test"
    assert short["booked_connection_min"] == 75 and not short["connection_time_ok"]


def test_fallback_mct_when_airport_not_listed():
    inbound = leg("XX1", "AAA", "ZRH", "08:00", "09:00")
    c = conn.check_connection(inbound, leg("XX2", "ZRH", "BBB", "10:00", "12:00"), REF)
    assert c["mct_min"] == 60 and c["mct_source"] == "fallback" and c["connection_time_ok"]


def test_single_ticket_requires_one_booking_reference():
    inbound = leg("LH903", "FRA", "LHR", "08:00", "09:45", ref="ABC123")
    assert conn.check_connection(inbound, leg("BA175", "LHR", "JFK", "12:00", "19:30", ref="ABC123"), REF)["single_ticket"]
    assert not conn.check_connection(inbound, leg("BA175", "LHR", "JFK", "12:00", "19:30", ref="ZZZ999"), REF)["single_ticket"]


def test_repo_reference_file_loads():
    ref = conn.load_mct()
    assert ref["fallback_minutes"] == 60 and ref["confirmed_by"] == "po" and "LHR" in ref["airports"]
