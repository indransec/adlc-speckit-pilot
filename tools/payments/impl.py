"""Mock payments system (TOOL-PAYMENTS). Creates an instruction ONLY with a valid approval
token (ADR-003, REQ-B-005) and only to the claimant's own account (REQ-F-030).
Bank details stay inside this system: they are never returned unmasked or sent to a model."""
from __future__ import annotations

import uuid

from shared import db
from shared.audit import audit, control_event
from shared.hitl import approval_token
from shared.runtime import get_runtime

OPERATIONS = {"verify_payee": "create-instruction", "create_instruction": "create-instruction"}


class PaymentRejected(RuntimeError):
    pass


def _norm(name: str | None) -> str:
    return " ".join((name or "").lower().split())


def _mask(acct: str | None) -> str:
    return f"****{acct[-4:]}" if acct else "****"


def verify_payee(claim_id: str, claimant_name: str) -> dict:
    c = db.one("SELECT bank_account_holder, bank_account_no FROM claims WHERE claim_id = ?", [claim_id])
    if not c:
        raise PaymentRejected("unknown claim")
    return {"holder_matches": _norm(c["bank_account_holder"]) == _norm(claimant_name),
            "account_masked": _mask(c["bank_account_no"])}


def create_instruction(claim_id: str, amount_usd: float, approval_token_str: str | None) -> dict:
    rt = get_runtime()
    try:
        if rt["kill_switch"]:
            raise PaymentRejected("kill switch active: payment instructions stopped")
        if not rt["payments_enabled"]:
            raise PaymentRejected("payments disabled in this rollout phase")
        claims = approval_token.verify(approval_token_str, claim_id=claim_id, amount_usd=amount_usd)
        if db.one("SELECT jti FROM used_tokens WHERE jti = ?", [claims["jti"]]):
            raise PaymentRejected("approval token already used")
        c = db.one("SELECT claimant_name, bank_account_holder, bank_account_no FROM claims WHERE claim_id = ?", [claim_id])
        if _norm(c["bank_account_holder"]) != _norm(c["claimant_name"]):
            raise PaymentRejected("account holder does not match claimant")
    except (approval_token.TokenError, PaymentRejected) as e:
        control_event(claim_id, "payment_requires_token", "blocked", str(e))
        audit(claim_id, "payment_blocked", {"reason": str(e)}, actor="TOOL-PAYMENTS")
        raise PaymentRejected(str(e)) from e
    db.insert("used_tokens", {"jti": claims["jti"], "claim_id": claim_id, "used_at": db.now()})
    pay = {"payment_id": "PAY-" + uuid.uuid4().hex[:10].upper(), "claim_id": claim_id,
           "amount_usd": round(amount_usd, 2), "payee_name": c["claimant_name"],
           "account_masked": _mask(c["bank_account_no"]), "token_jti": claims["jti"], "token_verified": 1,
           "approvers": db.jdump(claims["approvers"]), "status": "instructed", "created_at": db.now()}
    db.insert("payments", pay)
    control_event(claim_id, "payment_requires_token", "passed", f"row {claims['route_row']}")
    return pay
