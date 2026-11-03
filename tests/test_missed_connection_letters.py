"""0.4.0 missed connection letters (T040-T042; REQ-F-039, REQ-B-008).

Approved by Compliance on 2026-11-02 after the G4 rework (T043); the rejection letter is v0.2 (clause reason only).
"""
import pytest

from tools.notify import impl as notify

CTX = {"claimant_name": "A Customer", "claim_id": "CLM-MC-1", "outcome": "partial", "opening": "Thank you.",
       "claimed_usd": 320.0, "approved_usd": 170.0, "payment_note": "We will confirm the payment separately.",
       "deductions": [{"clause": "3.4", "clause_title": "Compensation offset", "amount_usd": 150.0,
                       "explanation": "The airline already paid you USD 150."}],
       "reasons": [{"clause": "3.7", "clause_title": "Missed connection",
                    "explanation": "The flights were booked on separate tickets."}],
       "inbound_leg": "LH903", "onward_leg": "BA0175", "connection_airport": "LHR"}


def test_missed_connection_templates_approved_by_compliance():
    t = notify.approved()
    for k in ("missed_connection_paid", "missed_connection_partial", "missed_connection_rejected"):
        assert t[k]["status"] == "approved" and t[k]["approved_by"].startswith("compliance")
    assert t["missed_connection_rejected"]["version"] == "0.2"   # G4 rework


@pytest.mark.parametrize("template,needle", [("missed_connection_partial", "clause 3.4"),
                                             ("missed_connection_rejected", "Clause 3.7"),
                                             ("missed_connection_paid", "approved in full")])
def test_rendered_once_approved(monkeypatch, template, needle):
    real = notify.approved()
    monkeypatch.setattr(notify, "approved", lambda: {**real, template: {**real[template], "status": "approved"}})
    subject, body = notify.render(template, CTX)
    if template == "missed_connection_rejected":   # G4 rework (Compliance): clause reason only, no restated facts
        assert "missed connection cover (policy clause 3.7)" in body and "LH903" not in body and "arrived late" not in body
    else:
        assert "missed connection (policy clause 3.7)" in body and "LH903" in body and "BA0175" in body and "LHR" in body
    assert needle in body
    if template != "missed_connection_paid":
        assert "appeal" in body.lower() or "review" in body.lower()
