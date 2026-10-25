"""Routing-matrix tests: one or more per row. Run: python -m unittest discover -s agents/orchestrator/tests"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from routing import (ClaimDecisionInput, Deduction, DeductionType as T, Recommendation as R,
                     Route, route_claim, l1_outcome_reroute)


def claim(**kw):
    base = dict(claim_id="C1", claimed_usd=300, recommendation=R.APPROVE, approved_usd=300)
    base.update(kw)
    return ClaimDecisionInput(**base)


class RoutingMatrix(unittest.TestCase):
    def test_row1_straight_through(self):
        d = route_claim(claim())
        self.assertEqual(d.route, Route.STRAIGHT_THROUGH); self.assertEqual(d.approvals_required, 0)

    def test_row1_boundary_exactly_1000(self):
        self.assertEqual(route_claim(claim(claimed_usd=1000, approved_usd=1000)).route, Route.STRAIGHT_THROUGH)

    def test_row2_type_a_only(self):
        c = claim(claimed_usd=700, approved_usd=600, recommendation=R.PARTIAL,
                  deductions=[Deduction(100, T.A, "Sec 4.2 excess")])
        d = route_claim(c)
        self.assertEqual(d.route, Route.L1_POLICY_NORM); self.assertEqual(d.approvals_required, 1)

    def test_row2_type_a_above_1000(self):
        c = claim(claimed_usd=1400, approved_usd=1200, recommendation=R.PARTIAL,
                  deductions=[Deduction(100, T.A, "excess"), Deduction(100, T.A, "per-item cap")])
        self.assertEqual(route_claim(c).route, Route.L1_POLICY_NORM)

    def test_row3_over_authority_no_deduction(self):
        self.assertEqual(route_claim(claim(claimed_usd=1400, approved_usd=1400)).route, Route.L1_AUTHORITY)

    def test_row3_linked_claims_aggregate(self):
        c = claim(claimed_usd=600, approved_usd=600, prior_approved_linked_usd=500)
        self.assertEqual(route_claim(c).route, Route.L1_AUTHORITY)

    def test_row4_type_b(self):
        c = claim(claimed_usd=1400, approved_usd=900, recommendation=R.PARTIAL,
                  deductions=[Deduction(500, T.B, "Sec 6.1 receipts", ["doc:receipt-missing"])])
        d = route_claim(c)
        self.assertEqual(d.route, Route.L1_L2_SIGNIFICANT); self.assertEqual(d.approvals_required, 2)

    def test_row4_type_a_over_25pct_net(self):
        c = claim(claimed_usd=400, approved_usd=250, recommendation=R.PARTIAL,
                  deductions=[Deduction(150, T.A, "excess")])
        self.assertEqual(route_claim(c).route, Route.L1_L2_SIGNIFICANT)

    def test_row4_net_boundary_exactly_25pct_is_one_level(self):
        c = claim(claimed_usd=400, approved_usd=300, recommendation=R.PARTIAL,
                  deductions=[Deduction(100, T.A, "excess")])
        self.assertEqual(route_claim(c).route, Route.L1_POLICY_NORM)

    def test_row5_rejection(self):
        c = claim(claimed_usd=500, approved_usd=0, recommendation=R.REJECT)
        self.assertEqual(route_claim(c).route, Route.L1_L2_REJECTION)

    def test_row6_low_confidence(self):
        self.assertEqual(route_claim(claim(confidence=0.69)).route, Route.INVESTIGATION)

    def test_row6_type_b_without_evidence(self):
        c = claim(claimed_usd=800, approved_usd=500, recommendation=R.PARTIAL,
                  deductions=[Deduction(300, T.B, "Sec 6.3")])
        self.assertEqual(route_claim(c).route, Route.INVESTIGATION)

    def test_row7_fraud_beats_everything(self):
        self.assertEqual(route_claim(claim(fraud_risk="medium")).route, Route.SIU)

    def test_agent_never_rejects_alone(self):
        for amt in (0, 10, 999):
            d = route_claim(claim(claimed_usd=max(amt, 1), approved_usd=0, recommendation=R.REJECT))
            self.assertGreaterEqual(d.approvals_required, 2)

    def test_l1_removes_type_b_drops_to_one_level(self):
        c = claim(claimed_usd=1400, approved_usd=900, recommendation=R.PARTIAL,
                  deductions=[Deduction(100, T.A, "excess"), Deduction(400, T.B, "receipts", ["doc:1"])])
        d = l1_outcome_reroute(c, l1_removed_all_type_b=True)
        self.assertEqual(d.approvals_required, 1)


if __name__ == "__main__":
    unittest.main()
