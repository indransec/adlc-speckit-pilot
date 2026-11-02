"""0.4.0 missed connection letters (T040-T042; REQ-F-039, REQ-B-008).

The three templates are drafts pending Compliance (G4, T043): until approved, TOOL-NOTIFY refuses them and
missed connection claims use the approved decision_* templates.
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


def test_missed_connection_templates_are_pending_compliance():
    t = notify.approved()
    for k in ("missed_connection_paid", "missed_connection_partial", "missed_connection_rejected"):
        assert t[k]["status"] == "pending_compliance" and t[k]["approved_by"] is None
        with pytest.raises(notify.TemplateNotApproved):
            notify.render(k, CTX)


@pytest.mark.parametrize("template,needle", [("missed_connection_partial", "clause 3.4"),
                                             ("missed_connection_rejected", "Clause 3.7"),
                                             ("missed_connection_paid", "approved in full")])
def test_rendered_once_approved(monkeypatch, template, needle):
    real = notify.approved()
    monkeypatch.setattr(notify, "approved", lambda: {**real, template: {**real[template], "status": "approved"}})
    subject, body = notify.render(template, CTX)
    assert "missed connection (policy clause 3.7)" in body and "LH903" in body and "BA0175" in body and "LHR" in body
    assert needle in body
    if template != "missed_connection_paid":
        assert "appeal" in body.lower() or "review" in body.lower()
