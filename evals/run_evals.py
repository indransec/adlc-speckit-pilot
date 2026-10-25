"""Minimal eval runner. Today it runs the routing eval over golden seed cases;
extend with model-backed suites (extraction, coverage, letters)."""
import json, os, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "agents", "orchestrator", "src"))
from routing import ClaimDecisionInput, Deduction, DeductionType, Recommendation, route_claim  # noqa: E402


def load(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def main(release="0.1.0"):
    cases = load(os.path.join(HERE, "datasets", "golden", "routing_cases.jsonl"))
    results, correct = [], 0
    for c in cases:
        inp = ClaimDecisionInput(
            claim_id=c["case_id"], claimed_usd=c["claimed_usd"],
            recommendation=Recommendation(c["recommendation"]), approved_usd=c["approved_usd"],
            prior_approved_linked_usd=c.get("prior_approved_linked_usd", 0.0),
            deductions=[Deduction(d["amount_usd"], DeductionType(d["type"]), d["clause"], d["evidence_refs"]) for d in c["deductions"]],
            fraud_risk=c["fraud_risk"], confidence=c["confidence"])
        got = route_claim(inp).route.value
        ok = got == c["expected_route"]
        correct += ok
        results.append({"case_id": c["case_id"], "requirements": c["requirements"], "expected": c["expected_route"], "got": got, "pass": ok})
    score = correct / len(cases)
    report = {"release": release, "generated_at": datetime.now(timezone.utc).isoformat(),
              "suite": "routing", "routing_correctness": score, "threshold": 0.99,
              "passed": score >= 0.99, "cases": results}
    out_dir = os.path.join(ROOT, "evidence", release)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "eval-report.json"), "w") as f:
        json.dump(report, f, indent=2)
    print(f"routing_correctness={score:.3f} passed={report['passed']} -> evidence/{release}/eval-report.json")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:2] or [])))
