"""Approver directory and authority rules (REQ-B-006: L1 and L2 different users, L2 higher authority)."""
from __future__ import annotations

from shared import db

ROLE_AUTHORITY = {"L1": 1, "L2": 2, "SIU": 2}
QUEUE_ROLES = {"L1": {"L1", "L2"}, "INVESTIGATION": {"L1", "L2"}, "L2": {"L2"}, "SIU": {"SIU"}}


class ApproverError(PermissionError):
    pass


def get_user(user_id: str) -> dict:
    u = db.one("SELECT * FROM users WHERE user_id = ?", [user_id])
    if not u:
        raise ApproverError(f"unknown user {user_id}")
    return u


def validate(queue: str, user_id: str, prior_l1_user: str | None = None) -> dict:
    u = get_user(user_id)
    if u["role"] not in QUEUE_ROLES[queue]:
        raise ApproverError(f"{user_id} ({u['role']}) cannot act on the {queue} queue")
    if queue == "L2":
        if prior_l1_user and prior_l1_user == user_id:
            raise ApproverError("L2 approver must be a different user from L1")
        if prior_l1_user and u["authority"] <= get_user(prior_l1_user)["authority"]:
            raise ApproverError("L2 approver must have higher authority than L1")
    return u
