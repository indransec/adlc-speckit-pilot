"""Settlement agent (AGT-TRV-SETTLE): payment instruction only with a valid approval token.

Deterministic by design (decision D-09): no model call, bank details never leave the
payments system (REQ-F-030, REQ-B-005).
"""
from __future__ import annotations

TOOL = "TOOL-PAYMENTS"


def run(ctx, *, approval_token: str | None, amount_usd: float, claimant_name: str) -> dict:
    from tools.payments.impl import PaymentRejected
    payee = ctx.tool(TOOL, "verify_payee", claimant_name=claimant_name)
    if not payee["holder_matches"]:
        ctx.audit("payment_on_hold", {"reason": "account holder does not match claimant"})
        return {"status": "payment_on_hold", "reason": "account holder does not match claimant (REQ-F-030)"}
    try:
        pay = ctx.tool(TOOL, "create_instruction", amount_usd=amount_usd, approval_token_str=approval_token)
    except PaymentRejected as e:
        return {"status": "payment_on_hold", "reason": str(e)}
    return {"status": "instructed", "payment_id": pay["payment_id"], "amount_usd": pay["amount_usd"],
            "account_masked": pay["account_masked"]}
