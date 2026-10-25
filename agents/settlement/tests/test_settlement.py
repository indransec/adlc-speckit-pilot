"""test:settlement - payment only with a valid approval token after the last approval (REQ-B-005, REQ-F-030)."""
import pytest

from shared import db
from shared.hitl import approval_token
from shared.runtime import set_runtime
from shared.tool_gateway import ToolPermissionError, call_tool
from tools.payments.impl import PaymentRejected

L1 = [{"level": "L1", "user_id": "l1.priya"}]
L2 = [{"level": "L2", "user_id": "l2.grace"}]


def pay(cid, token, amount=200.0, agent="settlement"):
    return call_tool(agent, "TOOL-PAYMENTS", "create_instruction", claim_id=cid, amount_usd=amount, approval_token_str=token)


def test_valid_token_pays_once(make_claim, runtime_reset):
    cid = make_claim("delay_stp")
    tok = approval_token.issue(cid, 200.0, 2, L1)
    assert pay(cid, tok)["status"] == "instructed"
    with pytest.raises(PaymentRejected, match="already used"):
        pay(cid, tok)


@pytest.mark.parametrize("token_fn,match", [
    (lambda cid: None, "missing"),
    (lambda cid: approval_token.issue(cid, 999.0, 2, L1), "amount"),
    (lambda cid: approval_token.issue(cid, 200.0, 4, L1), "needs 2"),
    (lambda cid: approval_token.issue(cid, 200.0, 4, L1 + [{"level": "L2", "user_id": "l1.priya"}]), "distinct"),
    (lambda cid: approval_token.issue(cid, 200.0, 6, L1), "cannot be paid"),
    (lambda cid: approval_token.issue("CLM-OTHER", 200.0, 1, []), "another claim"),
])
def test_invalid_tokens_blocked(make_claim, runtime_reset, token_fn, match):
    cid = make_claim("delay_stp")
    with pytest.raises(PaymentRejected, match=match):
        pay(cid, token_fn(cid))
    assert db.scalar("SELECT COUNT(*) FROM payments WHERE claim_id = ?", [cid]) == 0


def test_two_level_token_pays(make_claim, runtime_reset):
    cid = make_claim("delay_stp")
    assert pay(cid, approval_token.issue(cid, 200.0, 4, L1 + L2))["status"] == "instructed"


def test_kill_switch_and_payments_flag_block(make_claim, runtime_reset):
    cid = make_claim("delay_stp")
    set_runtime("kill_switch", True)
    with pytest.raises(PaymentRejected, match="kill switch"):
        pay(cid, approval_token.issue(cid, 200.0, 2, L1))
    set_runtime("kill_switch", False)
    set_runtime("payments_enabled", False)
    with pytest.raises(PaymentRejected, match="disabled"):
        pay(cid, approval_token.issue(cid, 200.0, 2, L1))


def test_payee_must_match_claimant(make_claim, runtime_reset):
    cid = make_claim("delay_stp")
    db.update("claims", "claim_id", cid, {"bank_account_holder": "Someone Else"})
    assert call_tool("settlement", "TOOL-PAYMENTS", "verify_payee", claim_id=cid,
                     claimant_name=db.one("SELECT claimant_name FROM claims WHERE claim_id=?", [cid])["claimant_name"])["holder_matches"] is False
    with pytest.raises(PaymentRejected, match="account holder"):
        pay(cid, approval_token.issue(cid, 200.0, 2, L1))


def test_only_settlement_agent_may_pay(make_claim, runtime_reset):
    cid = make_claim("delay_stp")
    with pytest.raises(ToolPermissionError):
        pay(cid, approval_token.issue(cid, 200.0, 2, L1), agent="assessment")
