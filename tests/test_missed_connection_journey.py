"""0.4.0 missed connection end to end through the claim graph (mock models).

T026 test:routing guard (REQ-B-012): a missed connection is routed rows 1-3 only with a verified inbound delay,
     a valid connection and a single ticket - with routing.py unchanged.
T034 order (REQ-F-038): a travel delay claim after a paid missed connection on the same journey is capped.
T056 (REQ-F-013): the duplicate-claim check does not treat travel delay and missed connection on one journey as
     duplicates; overlapping receipts are handled by 7.2.
Expected outcomes here are the PROPOSED ones in synthetic/scenarios.py (labelled by a person at T047).
"""
import pytest

from shared import db
from synthetic.scenarios import MC_SCENARIOS, build_case

AUTO_ROWS = ("row1_straight_through", "row2_l1_policy_norm", "row3_l1_authority")


def _run(platform, make_claim, scenario, seed):
    cid = make_claim(scenario, seed)
    platform.process(cid)
    s = platform.summary(cid)
    return cid, s["claim"], s["package"] or {}, s["agent_outputs"]


@pytest.mark.parametrize("seed,scenario", list(enumerate(sorted(MC_SCENARIOS), start=101)))
def test_routes_as_proposed_and_guard_holds(platform, make_claim, seed, scenario):
    # distinct seeds: the same seed gives the same trip id, and earlier paid cases would share the journey limit
    cid, claim, pkg, outs = _run(platform, make_claim, scenario, seed=seed)
    exp = build_case(scenario, __import__("random").Random(seed), "CLM-X", used_flights=set())["expected"]
    assert claim["route"] == exp["route"], (scenario, claim["route"], pkg.get("rejection_reasons"))
    if claim["claim_type"] == "missed_connection" and claim["route"] in AUTO_ROWS:
        v = next(o for k, o in outs.items() if k.startswith("AGT-TRV-VER"))
        c = v.get("connection") or {}
        assert v["inbound_delay_verified"] and c.get("connection_time_ok") and c.get("single_ticket")


def test_package_carries_connection_and_journey(platform, make_claim):
    _, _, pkg, _ = _run(platform, make_claim, "mc_after_delay_shared_limit", seed=11)
    assert pkg["connection"]["airport"] == "LHR" and pkg["connection"]["mct_min"] == 90
    assert pkg["journey"] == {"already_paid_usd": 300.0, "shared_limit_remaining_usd": 200.0}


def test_travel_delay_after_missed_connection_is_capped(platform, make_claim):
    _, claim, pkg, _ = _run(platform, make_claim, "delay_after_mc", seed=12)
    assert claim["recommended_usd"] == 100.0
    assert [(d["type"], d["clause"], d["amount_usd"]) for d in pkg["deductions"]] == [("A", "3.6", 100.0)]


def test_delay_and_missed_connection_on_one_journey_are_not_duplicate_claims(platform, make_claim):
    _, _, pkg, outs = _run(platform, make_claim, "mc_duplicate_receipt", seed=13)
    cov = next(o for k, o in outs.items() if k.startswith("AGT-TRV-COV"))
    assert cov["duplicate_of"] is None                              # REQ-F-013 check not triggered
    assert ("A", "7.2", 120.0) in [(d["type"], d["clause"], d["amount_usd"]) for d in pkg["deductions"]]


def test_onward_flight_is_stored_on_the_claim(platform, make_claim):
    cid = make_claim("mc_stp", 14)
    c = db.one("SELECT flight_no, journey_direction FROM claims WHERE claim_id = ?", [cid])
    assert c["flight_no"].startswith("BA0") and c["journey_direction"] == "outward"
