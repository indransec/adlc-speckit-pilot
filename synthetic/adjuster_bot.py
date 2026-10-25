"""Simulated adjusters for traffic / hypercare / eval runs.

Each human decision follows the synthetic ground truth (what an experienced adjuster
would decide after reviewing the evidence). Where the agent's recommendation differs,
the bot overrides it, which is what drives the override-rate KPI and G7 review.
"""
from __future__ import annotations

import itertools

from backend import service
from shared import db

L1_USERS = itertools.cycle(["l1.priya", "l1.marco"])
L2_USERS = itertools.cycle(["l2.grace", "l2.tomas"])


def decide_one(claim_id: str) -> dict | None:
    pending = service.pending_interrupt(claim_id)
    if not pending:
        return None
    c = db.one("SELECT recommended_usd, recommendation, ground_truth FROM claims WHERE claim_id = ?", [claim_id])
    gt = db.jload(c["ground_truth"], {})
    target, outcome = gt.get("final_amount"), gt.get("final_outcome", "approve")
    rec_amt = c["recommended_usd"] or 0.0
    q = pending["queue"]
    if q == "SIU":
        args = ("siu.sam", "refer" if outcome == "siu_referred" else "clear", None,
                "SIU investigation: " + ("referral confirmed" if outcome == "siu_referred" else "no fraud indicators confirmed"))
    elif q in ("L1", "INVESTIGATION"):
        if outcome == "reject":
            args = (next(L1_USERS), "accept" if c["recommendation"] == "reject" else "reject", None,
                    "agree with rejection" if c["recommendation"] == "reject" else "evidence does not support the claim")
        elif target is None or abs(rec_amt - target) <= 1.0:
            args = (next(L1_USERS), "accept", None, "agree with recommendation")
        else:
            args = (next(L1_USERS), "set_amount", target, "amount differs after evidence review")
    else:  # L2
        l1 = pending.get("l1_view", {})
        if outcome == "reject":
            args = (next(L2_USERS), "accept_l1" if l1.get("outcome") == "reject" else "reject", None, "final rejection")
        elif target is None or abs((l1.get("amount_usd") or 0) - target) <= 1.0:
            args = (next(L2_USERS), "accept_l1", None, "agree with L1")
        else:
            args = (next(L2_USERS), "set_amount", target, "L2 adjusted amount")
        if args[0] == l1.get("user_id"):
            args = (next(L2_USERS),) + args[1:]
    user, decision, amount, notes = args
    return service.decide(claim_id, user, decision, amount, notes)


def drain(claim_ids: list[str] | None = None, max_rounds: int = 5) -> int:
    """Decide every pending human task (optionally restricted to claim_ids). Returns decisions made."""
    n = 0
    for _ in range(max_rounds):
        pending = [q["claim_id"] for q in service.queues() if q["status"] != "manual_queue"
                   and (claim_ids is None or q["claim_id"] in claim_ids)]
        if not pending:
            break
        for cid in pending:
            if decide_one(cid):
                n += 1
    return n
