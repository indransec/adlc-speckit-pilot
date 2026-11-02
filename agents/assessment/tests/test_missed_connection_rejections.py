"""Missed connection eligibility reasons (0.4.0; T027; REQ-F-036, REQ-B-002). Each produces a rejection
with its clause, so the claim routes L1 then L2 (row 5)."""
import importlib

import pytest

from shared.registry import load_agent_package

pytestmark = pytest.mark.usefixtures("platform")   # clause titles come from the clause index

agent = importlib.import_module(f"{load_agent_package('assessment')}.agent")
COV = {"policy_found": True, "policy_active": True, "claimant_insured": True, "exclusions": []}
OK = {"airport": "LHR", "booked_connection_min": 150, "mct_min": 90, "mct_source": "test",
      "connection_time_ok": True, "single_ticket": True}


def reasons(**ver):
    v = {"connection": OK, "inbound_delay_verified": True, **ver}
    return [(r["clause"], r["reason"]) for r in agent.rejection_reasons("missed_connection", COV, v, {"itinerary": ["D02"]})]


def test_valid_claim_has_no_rejection_reason():
    assert reasons() == []


def test_short_connection():
    r = reasons(connection={**OK, "booked_connection_min": 60, "connection_time_ok": False})
    assert len(r) == 1 and r[0][0] == "3.7" and "shorter than the minimum connection time" in r[0][1]


def test_self_transfer():
    r = reasons(connection={**OK, "single_ticket": False})
    assert len(r) == 1 and r[0][0] == "3.7" and "separate tickets" in r[0][1]


def test_strike_announced_before_booking():
    r = reasons(strike_announced_before_booking=True)
    assert r == [("3.3", "the strike delaying the inbound flight was publicly announced before the trip was booked")]


def test_late_check_in_for_the_inbound_leg():
    r = reasons(late_check_in=True)
    assert r == [("3.3", "the customer did not check in on time for the inbound flight")]


def test_ticket_lines_not_excluded_under_6_3_and_class_above_reasonable_is_3_7():
    lines = [{"line_id": "L1", "description": "Rebooked flight (economy)", "category": "ticket", "claimed_usd": 180,
              "receipt_doc_id": "D03"},
             {"line_id": "L2", "description": "Business class ticket", "category": "ticket", "claimed_usd": 900,
              "receipt_doc_id": "D04"}]
    js = {j.line_id: j for j in agent.heuristic_judgements("missed_connection", lines, "2026-08-10", "D01")}
    assert js["L1"].decision == "eligible"
    assert js["L2"].decision == "ineligible" and js["L2"].clause == "3.7"
