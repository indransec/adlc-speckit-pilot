"""test:document-chasing (REQ-F-006) and deterministic extraction parsing."""
import importlib

from shared.registry import load_agent_package

pkg = load_agent_package("document")
chasing = importlib.import_module(f"{pkg}.chasing")
parsing = importlib.import_module(f"{pkg}.parsing")


def test_chasing_schedule():
    assert chasing.due_actions(2, 0) == []
    assert chasing.due_actions(3, 0) == [("reminder", 3)]
    assert chasing.due_actions(7, 1) == [("reminder", 7)]
    assert chasing.due_actions(8, 2) == []
    assert chasing.due_actions(30, 2) == [("close_incomplete", 30)]
    assert chasing.due_actions(31, 0) == [("reminder", 3), ("reminder", 7), ("close_incomplete", 30)]


def test_illegible_receipt_lowers_certainty():
    f = parsing.parse("RECEIPT\nScan quality: low\nItem: Cl#th [illegible]\nAmount: [illegible]\n", "receipt")
    assert f["receipt_amount"] is None and set(f["illegible_fields"]) == {"item", "amount"}
    assert parsing.certainty(f) == 0.0


def test_claim_form_lines_parsed():
    text = ("Claimant name: A B\nPolicy number: TRV-STD-1\nFlight number: BA2490\nFlight date: 2026-09-01\n"
            "1 | Hotel (1 night) | accommodation | 140.00 USD | claimed USD 140.00 | purchased 2026-09-01\n"
            "Total claimed (USD): 140.00\n")
    f = parsing.parse(text, "claim_form")
    assert f["lines"][0]["claimed_usd"] == 140.0 and parsing.certainty(f) == 1.0


def test_incomplete_claim_requests_documents(platform, make_claim):
    cid = make_claim("incomplete")
    s = platform.process(cid)
    assert s["claim"]["status"] == "pending_documents"
    assert any(n["subject"].startswith("Documents needed") for n in s["notifications"])
    res = platform.chase_documents(31)
    assert {r["action"] for r in res if r["claim_id"] == cid} == {"reminder", "close_incomplete"}
