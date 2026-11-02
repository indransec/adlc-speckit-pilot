"""eval:verification (unit) - missed connection facts checked in code (0.4.0; T017, T030; REQ-F-034, REQ-F-035,
REQ-F-036, REQ-B-012, REQ-B-013)."""
import importlib

from shared.registry import load_agent_package

conn = importlib.import_module(f"{load_agent_package('verification')}.connection")
REF = {"as_of": "2026-11-02", "source": "test", "fallback_minutes": 60, "airports": {"LHR": 90}}
D = "2026-08-10"


def leg(no, o, d, dep, arr, ref="ABC123"):
    return {"flight_no": no, "flight_date": D, "origin": o, "destination": d,
            "scheduled_dep": f"{D} {dep}", "scheduled_arr": f"{D} {arr}", "booking_ref": ref}


def facts(onward_dep="12:00", ref2="ABC123", legs=None):
    return {"flight_no": "BA175", "flight_date": D, "booking_date": "2026-06-01",
            "legs": legs or [leg("LH903", "FRA", "LHR", "08:00", "09:45"), leg("BA175", "LHR", "JFK", onward_dep, "20:00", ref2)]}


def source(arr_delay=150, cause="weather", sched_arr="09:45", announced=None, onward_dep="12:00"):
    def lookup(flight_no, flight_date):
        if flight_no == "LH903":
            h, m = map(int, sched_arr.split(":"))
            a = h * 60 + m + arr_delay
            return {"scheduled_arr": f"{D} {sched_arr}", "actual_arr": f"{D} {a // 60:02d}:{a % 60:02d}",
                    "arrival_delay_minutes": arr_delay, "cause": cause, "cause_announced_at": announced}
        if flight_no == "BA175":
            return {"scheduled_dep": f"{D} {onward_dep}", "actual_dep": f"{D} {onward_dep}"}
        return None
    return lookup


def test_verified_inbound_delay_and_valid_connection():
    v = conn.verify_missed_connection(facts(), source(), REF)
    assert v["inbound_delay_verified"] and v["connection"]["connection_time_ok"] and v["connection"]["single_ticket"]
    assert v["verification_success"] == 1.0 and v["inbound_leg"] == "LH903" and v["discrepancies"] == []


def test_short_covered_delay_qualifies_no_minimum():
    # booked connection 135 min (>= 90); inbound 50 min late (weather) -> 45 min left < 90 MCT -> connection missed
    v = conn.verify_missed_connection(facts(), source(arr_delay=50), REF)
    assert v["inbound_delay_verified"] and v["verification_success"] == 1.0


def test_short_connection_is_an_eligibility_outcome_not_a_verification_failure():
    v = conn.verify_missed_connection(facts(onward_dep="11:00"), source(), REF)   # 75 < 90
    assert not v["connection"]["connection_time_ok"] and v["verification_success"] == 1.0   # -> rejection, row 5


def test_self_transfer_is_an_eligibility_outcome():
    v = conn.verify_missed_connection(facts(ref2="ZZZ999"), source(), REF)
    assert not v["connection"]["single_ticket"] and v["verification_success"] == 1.0


def test_inbound_on_time_per_source_is_not_verified():
    v = conn.verify_missed_connection(facts(), source(arr_delay=0), REF)
    assert not v["inbound_delay_verified"] and v["verification_success"] == 0.0 and v["discrepancies"]


def test_inbound_arrived_in_time_for_the_onward_flight_is_not_verified():
    v = conn.verify_missed_connection(facts(), source(arr_delay=20), REF)   # 115 min left >= 90 MCT
    assert not v["inbound_delay_verified"] and any("in time" in d for d in v["discrepancies"])


def test_itinerary_time_differs_from_source_is_a_discrepancy():
    v = conn.verify_missed_connection(facts(), source(sched_arr="09:10"), REF)   # doc says 09:45
    assert not v["inbound_delay_verified"] and any("differs" in d for d in v["discrepancies"])


def test_ambiguous_legs_route_to_a_person():
    legs = [leg("LH903", "FRA", "LHR", "07:00", "08:45"), leg("AF1080", "CDG", "LHR", "08:00", "09:15"),
            leg("BA175", "LHR", "JFK", "12:00", "20:00")]
    v = conn.verify_missed_connection(facts(legs=legs), source(), REF)
    assert v["verification_success"] == 0.0 and v["inbound_leg"] is None and any("ambiguous" in d for d in v["discrepancies"])


def test_source_unavailable_is_not_verified():
    v = conn.verify_missed_connection(facts(), lambda **_: None, REF)
    assert v["verification_success"] == 0.0 and not v["inbound_delay_verified"]


def test_strike_announced_before_booking_flagged():
    v = conn.verify_missed_connection(facts(), source(cause="industrial_action", announced="2026-05-20T09:00:00"), REF)
    assert v["strike_announced_before_booking"]
