"""test:communication-templates (REQ-F-002, REQ-B-008): approved templates only, output policy enforced."""
import pytest

from shared.guardrails.output_policy import check_message
from shared.tool_gateway import call_tool
from tools.notify.impl import TemplateNotApproved, approved, render

CTX = {"claimant_name": "A B", "claim_id": "CLM-X", "claim_label": "baggage", "missing": ["PIR"], "day": 3,
       "outcome": "partial", "opening": "Thank you.", "claimed_usd": 100.0, "approved_usd": 50.0, "payment_note": "Paid.",
       "deductions": [{"clause": "6.1", "clause_title": "Receipts", "amount_usd": 50.0, "explanation": "No receipt."}],
       "reasons": [{"clause": "3.3", "clause_title": "Delay exclusions", "explanation": "Under 6 hours."}]}


@pytest.mark.parametrize("tid", sorted(k for k, t in approved().items() if t.get("status") == "approved"))  # drafts pending Compliance excluded (0.4.0)
def test_every_approved_template_renders(tid):
    subject, body = render(tid, CTX)
    assert "CLM-X" in subject and "A B" in body


def test_partial_letter_cites_clause_and_appeal():
    _, body = render("decision_partial", CTX)
    assert check_message(body, outcome="partial", deduction_clauses=["6.1"], is_decision=True) == []


def test_policy_flags_internal_terms_and_missing_appeal():
    v = check_message("Your fraud score was high. clause 6.1", outcome="partial", deduction_clauses=["6.1"], is_decision=True)
    assert any("appeal" in x for x in v) and any("fraud" in x for x in v)


def test_unapproved_template_refused(platform):
    with pytest.raises(TemplateNotApproved):
        call_tool("communication", "TOOL-NOTIFY", "send", claim_id="CLM-X", template="free_text", context={})
