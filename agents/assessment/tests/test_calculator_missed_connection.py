"""test:calculation - missed connection section of the rules engine (0.4.0; T016, T033; REQ-F-037, REQ-F-038).

Type A clauses only from code (T9): limit 3.6, offset 3.4 (incl. unused-ticket refund), FX 6.2, duplicate 7.2.
Judgements (3.5, 3.7 class above reasonable, 6.1 receipts) are type B.
"""
import importlib

from shared.registry import load_agent_package

calc = importlib.import_module(f"{load_agent_package('assessment')}.calculator")
STD = {"delay_limit": 500, "bag_delay_limit": 300, "bag_loss_limit": 2000, "single_item_limit": 500,
       "valuables_limit": 400, "baggage_excess": 50}
PRM = {**STD, "delay_limit": 1000}
FX = {"USD": 1.0, "EUR": 1.08}


def ln(i, amt, cat="accommodation", cur="USD", claimed=None, benefit="missed_connection", fp=None):
    return {"line_id": f"L{i}", "description": f"item {i}", "category": cat, "amount_orig": amt, "currency": cur,
            "claimed_usd": claimed if claimed is not None else amt, "benefit": benefit, "receipt_fingerprint": fp}


def ded(r):
    return [(d["type"], d["clause"], d["amount_usd"]) for d in r["deductions"]]


def test_receipted_ticket_hotel_meals_paid_in_full():
    r = calc.calculate("missed_connection", [ln(1, 180, "ticket"), ln(2, 100), ln(3, 40, "meals")], STD, FX, {})
    assert r["payable_usd"] == 320.0 and r["deductions"] == []


def test_no_6_3_deduction_on_ticket_lines():
    r = calc.calculate("missed_connection", [ln(1, 250, "ticket")], STD, FX, {})
    assert "6.3" not in {d["clause"] for d in r["deductions"]} and r["payable_usd"] == 250.0


def test_premier_limit_type_a():
    r = calc.calculate("missed_connection", [ln(1, 900, "ticket"), ln(2, 400)], PRM, FX, {})
    assert r["payable_usd"] == 1000.0 and ded(r) == [("A", "3.6", 300.0)]


def test_compensation_and_unused_ticket_refund_offset_type_a():
    r = calc.calculate("missed_connection", [ln(1, 180, "ticket"), ln(2, 140)], STD, FX, {},
                       compensation_usd=100.0, refund_usd=50.0)
    assert r["payable_usd"] == 170.0 and ded(r) == [("A", "3.4", 150.0)]
    assert "refund" in r["deductions"][0]["reason"]


def test_judgements_are_type_b():
    js = {"L1": calc.Judgement("L1", "ineligible", "3.7", "business class above the originally booked class"),
          "L2": calc.Judgement("L2", "ineligible", "3.5", "spa"),
          "L3": calc.Judgement("L3", "ineligible", "6.1", "no receipt")}
    r = calc.calculate("missed_connection", [ln(1, 300, "ticket"), ln(2, 80), ln(3, 200), ln(4, 50)], STD, FX, js)
    assert {(t, c) for t, c, _ in ded(r)} == {("B", "3.7"), ("B", "3.5"), ("B", "6.1")} and r["payable_usd"] == 50.0


def test_eur_receipt_converted_at_policy_rate():
    r = calc.calculate("missed_connection", [ln(1, 100, cur="EUR", claimed=120)], STD, FX, {})
    assert r["payable_usd"] == 108.0 and ded(r) == [("A", "6.2", 12.0)]


def test_cheaper_rebooking_has_nothing_payable_for_the_ticket():
    # the rebooked ticket cost nothing extra: the customer claims 0 for the ticket line
    r = calc.calculate("missed_connection", [ln(1, 0, "ticket"), ln(2, 90)], STD, FX, {})
    assert r["line_payable"]["L1"] == 0.0 and r["payable_usd"] == 90.0


# ---- journey shared limit and duplicates (US3, T033)
def test_shared_limit_with_travel_delay_already_paid():
    j = {"paid_delay_usd": 300.0, "paid_missed_connection_usd": 0.0, "paid_fingerprints": []}
    r = calc.calculate("missed_connection", [ln(1, 200, "ticket"), ln(2, 150)], STD, FX, {}, journey=j)
    assert r["payable_usd"] == 200.0 and ded(r) == [("A", "3.6", 150.0)]


def test_duplicate_receipt_deducted_in_full_type_a():
    j = {"paid_delay_usd": 120.0, "paid_missed_connection_usd": 0.0, "paid_fingerprints": ["fp-hotel"]}
    r = calc.calculate("missed_connection", [ln(1, 120, fp="fp-hotel"), ln(2, 180, "ticket", fp="fp-ticket")], STD, FX, {},
                       journey=j)
    assert ("A", "7.2", 120.0) in ded(r) and r["payable_usd"] == 180.0


def test_combined_claim_one_shared_limit_across_benefits():
    lines = [ln(1, 250, benefit="delay"), ln(2, 200, "ticket"), ln(3, 120, "meals")]
    r = calc.calculate("missed_connection", lines, STD, FX, {})
    assert r["payable_usd"] == 500.0 and ded(r) == [("A", "3.6", 70.0)]


def test_travel_delay_claim_after_missed_connection_uses_the_same_journey_limit():
    j = {"paid_delay_usd": 0.0, "paid_missed_connection_usd": 400.0, "paid_fingerprints": []}
    r = calc.calculate("delay", [ln(1, 200, benefit=None)], STD, FX, {}, journey=j)
    assert r["payable_usd"] == 100.0 and ded(r) == [("A", "3.6", 100.0)]


def test_existing_delay_behaviour_unchanged_without_journey():
    r = calc.calculate("delay", [ln(1, 700, benefit=None)], STD, FX, {})
    assert r["payable_usd"] == 500.0 and ded(r) == [("A", "3.6", 200.0)]
