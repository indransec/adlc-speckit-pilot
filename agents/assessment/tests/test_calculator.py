"""test:calculation - rules engine arithmetic (REQ-F-012) and A/B labelling invariants (T9)."""
import importlib

from shared.registry import load_agent_package

calc = importlib.import_module(f"{load_agent_package('assessment')}.calculator")
STD = {"delay_limit": 500, "bag_delay_limit": 300, "bag_loss_limit": 2000, "single_item_limit": 500,
       "valuables_limit": 400, "baggage_excess": 50}
PRM = {**STD, "delay_limit": 1000, "bag_loss_limit": 3000, "single_item_limit": 750, "valuables_limit": 800, "baggage_excess": 0}
FX = {"USD": 1.0, "EUR": 1.08}


def line(i, amt, cat="clothing", cur="USD", claimed=None, valuable=False):
    return {"line_id": f"L{i}", "description": f"item {i}", "category": cat, "amount_orig": amt, "currency": cur,
            "claimed_usd": claimed if claimed is not None else amt, "valuable": valuable}


def test_excess_only_type_a():
    r = calc.calculate("baggage_loss", [line(1, 200), line(2, 350), line(3, 150)], STD, FX, {})
    assert r["payable_usd"] == 650.0
    assert [(d["type"], d["clause"], d["amount_usd"]) for d in r["deductions"]] == [("A", "5.6", 50.0)]


def test_fx_at_policy_rate():
    r = calc.calculate("baggage_loss", [line(1, 400, cur="EUR", claimed=460), line(2, 180)], PRM, FX, {})
    assert r["payable_usd"] == 612.0 and r["deductions"][0]["clause"] == "6.2"


def test_valuables_sublimit_then_excess():
    r = calc.calculate("baggage_loss", [line(1, 450, "valuables", valuable=True), line(2, 400, "electronics", valuable=True)],
                       STD, FX, {})
    assert r["payable_usd"] == 350.0
    assert [d["clause"] for d in r["deductions"]] == ["5.3", "5.6"]


def test_depreciation_is_type_b():
    j = {"L1": calc.Judgement("L1", "depreciate", "5.4", "3 years", ["doc:1"], 30)}
    r = calc.calculate("baggage_loss", [line(1, 400), line(2, 200)], STD, FX, j)
    assert r["payable_usd"] == 430.0
    assert ("B", "5.4", 120.0) in [(d["type"], d["clause"], d["amount_usd"]) for d in r["deductions"]]


def test_judgement_citing_type_a_clause_stays_type_b():
    j = {"L1": calc.Judgement("L1", "ineligible", "5.3", "model cited a type A clause", ["doc:1"])}
    r = calc.calculate("baggage_loss", [line(1, 300), line(2, 250)], PRM, FX, j)
    assert all(d["type"] == "B" for d in r["deductions"] if d["line_id"] == "L1")


def test_single_item_and_section_limit():
    r = calc.calculate("baggage_loss", [line(1, 900), line(2, 700), line(3, 700), line(4, 700)], STD, FX, {})
    clauses = [d["clause"] for d in r["deductions"]]
    assert clauses.count("5.2") == 4 and r["payable_usd"] == 1950.0


def test_compensation_offset_and_delay_limit():
    r = calc.calculate("delay", [line(1, 250, "accommodation"), line(2, 90, "meals"), line(3, 60, "transport")], PRM, FX, {}, 100)
    assert r["payable_usd"] == 300.0 and r["reduction_pct"] == 25.0
    r = calc.calculate("delay", [line(1, 700, "accommodation")], STD, FX, {})
    assert r["payable_usd"] == 500.0 and r["deductions"][0]["clause"] == "3.6"
