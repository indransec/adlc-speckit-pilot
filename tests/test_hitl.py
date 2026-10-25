"""test:hitl (REQ-B-006): L1 and L2 are different users and L2 has higher authority; full HITL paths."""
import pytest

from backend.service import ServiceError
from shared.hitl.approvers import ApproverError, validate


def test_approver_rules(platform):
    validate("L1", "l1.priya")
    with pytest.raises(ApproverError):
        validate("L2", "l1.marco", prior_l1_user="l1.priya")        # L1 role cannot act as L2
    with pytest.raises(ApproverError):
        validate("L2", "l2.grace", prior_l1_user="l2.grace")        # same user twice
    with pytest.raises(ApproverError):
        validate("SIU", "l1.priya")


def test_two_level_path_requires_distinct_users(platform, make_claim, runtime_reset):
    cid = make_claim("missing_receipt")
    s = platform.process(cid)
    assert s["pending"]["queue"] == "L1"
    s = platform.decide(cid, "l1.priya", "accept")
    assert s["pending"]["queue"] == "L2"
    with pytest.raises(ServiceError):
        platform.decide(cid, "l1.priya", "accept_l1")             # the L1 user cannot also decide at L2
    with pytest.raises(ServiceError):
        platform.decide(cid, "l1.marco", "accept_l1")             # nor another L1 (authority must be higher)
    s = platform.decide(cid, "l2.tomas", "accept_l1")
    assert s["claim"]["status"] == "closed_paid" and s["payments"][0]["amount_usd"] == 700.0
    assert {a["level"] for a in s["approvals"]} == {"L1", "L2"}


def test_rejection_never_without_two_humans(platform, make_claim, runtime_reset):
    cid = make_claim("delay_short")
    s = platform.process(cid)
    assert s["claim"]["route"] == "row5_l1_l2_rejection" and s["claim"]["status"] == "awaiting_l1"
    s = platform.decide(cid, "l1.priya", "accept")
    assert s["claim"]["status"] == "awaiting_l2"
    s = platform.decide(cid, "l2.grace", "accept_l1")
    assert s["claim"]["status"] == "closed_rejected" and not s["payments"]
    assert "appeal" in s["notifications"][-1]["body"].lower()


def test_l1_removing_type_b_drops_to_one_level(platform, make_claim, runtime_reset):
    cid = make_claim("missing_receipt", seed=5)
    platform.process(cid)
    s = platform.decide(cid, "l1.marco", "remove_type_b")
    assert s["claim"]["status"] == "closed_paid" and s["claim"]["decided_by"] == "l1.marco"


def test_investigation_reroutes_after_l1(platform, make_claim, runtime_reset):
    cid = make_claim("mismatch")
    s = platform.process(cid)
    assert s["pending"]["queue"] == "INVESTIGATION" and s["claim"]["confidence"] < 0.7
    s = platform.decide(cid, "l1.priya", "accept")
    assert s["claim"]["status"] == "closed_paid"


def test_siu_clear_and_refer(platform, make_claim, runtime_reset):
    cid = make_claim("fraud")
    platform.process(cid)
    s = platform.decide(cid, "siu.sam", "refer")
    assert s["claim"]["status"] == "siu_referred" and not s["payments"]
    cid = make_claim("fraud", seed=9)
    platform.process(cid)
    s = platform.decide(cid, "siu.sam", "clear")
    assert s["pending"]["queue"] == "L1"


def test_rollout_modes(platform, make_claim, runtime_reset):
    from shared.runtime import set_runtime
    set_runtime("mode", "shadow")
    s = platform.process(make_claim("delay_stp"))
    assert s["claim"]["status"] == "shadow_complete" and not s["payments"] and len(s["notifications"]) == 1
    set_runtime("mode", "assisted")
    s = platform.process(make_claim("delay_stp"))
    assert s["claim"]["status"] == "awaiting_l1"                   # STP off: every claim gets L1
    set_runtime("mode", "full")
    set_runtime("kill_switch", True)
    s = platform.process(make_claim("delay_stp"))
    assert s["claim"]["status"] == "manual_queue" and not s["payments"]
