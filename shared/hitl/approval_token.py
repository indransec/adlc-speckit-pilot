"""Signed approval token (ADR-003, shared/hitl/approval-token.md).

Issued by the orchestrator only after the last required approval. The payments tool
validates signature, claim, amount, expiry, single use and that the approver chain
satisfies the routing row (REQ-B-005, REQ-B-006).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt

from shared.config import approval_token_secret, release

ALG = "HS256"
ISSUER = "AGT-TRV-ORCH"
TTL_MINUTES = 30
# Human approvals required for the FINAL route row (row 6 is re-routed before a token is issued)
REQUIRED_APPROVALS = {1: 0, 2: 1, 3: 1, 4: 2, 5: 2, 7: 2}


class TokenError(ValueError):
    pass


def issue(claim_id: str, approved_amount_usd: float, route_row: int, approvers: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "iss": ISSUER, "claim_id": claim_id, "approved_amount_usd": round(float(approved_amount_usd), 2),
        "route_row": int(route_row), "approvers": approvers, "release": release(),
        "iat": int(now.timestamp()), "exp": int((now + timedelta(minutes=TTL_MINUTES)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, approval_token_secret(), algorithm=ALG)


def verify(token: str | None, *, claim_id: str, amount_usd: float) -> dict:
    if not token:
        raise TokenError("missing approval token")
    try:
        c = jwt.decode(token, approval_token_secret(), algorithms=[ALG], issuer=ISSUER,
                       options={"require": ["exp", "iat", "jti", "iss"]})
    except jwt.ExpiredSignatureError:
        raise TokenError("approval token expired")
    except jwt.InvalidTokenError as e:
        raise TokenError(f"invalid approval token: {e}")
    if c["claim_id"] != claim_id:
        raise TokenError("token issued for another claim")
    if abs(c["approved_amount_usd"] - round(float(amount_usd), 2)) > 0.005:
        raise TokenError("amount differs from approved amount")
    required = REQUIRED_APPROVALS.get(c["route_row"])
    if required is None:
        raise TokenError(f"route row {c['route_row']} cannot be paid")
    humans = [a for a in c["approvers"] if a.get("level") in ("L1", "L2")]
    if len(humans) < required:
        raise TokenError(f"row {c['route_row']} needs {required} human approval(s), token has {len(humans)}")
    if required == 2:
        l1 = [a for a in humans if a["level"] == "L1"]
        l2 = [a for a in humans if a["level"] == "L2"]
        if not l1 or not l2 or l1[0]["user_id"] == l2[0]["user_id"]:
            raise TokenError("two-level approval needs distinct L1 and L2 approvers")
    return c
