"""Build REQ -> verification -> result matrix from requirements, eval cases and evidence reports.

Each verification method named in docs/requirements/*.yaml resolves to concrete evidence:
eval metrics (eval-report.json), RAI results (rai-report.json), unit test modules
(test-report.json), SLOs, perf test or design review.
"""
import glob
import json
import os
import sys

import yaml

ROOT = os.environ.get("PROJECT_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # sim runs: workspace

METHOD_EVIDENCE = {
    "test:routing": ("test", "agents/orchestrator/tests/test_routing.py"),
    "test:settlement": ("test", "agents/settlement/tests/test_settlement.py"),
    "test:hitl": ("test", "tests/test_hitl.py"),
    "test:intake": ("test", "agents/intake/tests/test_intake.py"),
    "test:communication-templates": ("test", "agents/communication/tests/test_communication_templates.py"),
    "test:document-chasing": ("test", "agents/document/tests/test_document_agent.py"),
    "test:calculation": ("test", "agents/assessment/tests/test_calculator.py"),
    "test:fraud-integration": ("test", "agents/fraud-screening/tests/test_fraud_integration.py"),
    "test:audit": ("test", "tests/test_audit_and_telemetry.py"),
    "test:telemetry": ("test", "tests/test_audit_and_telemetry.py"),
    "eval:routing": ("metric", "routing_correctness"),
    "eval:deduction-classification": ("metric", "type_b_labelled_as_a"),
    "eval:evidence-linking": ("metric", "type_b_evidence_link_present"),
    "eval:letters": ("metric", "letter_compliance_rate"),
    "eval:intake-classification": ("metric", "claim_type_classification_accuracy"),
    "eval:document-extraction": ("metric", "extraction_key_fields_accuracy"),
    "eval:document-completeness": ("metric", "completeness_check_accuracy"),
    "eval:coverage": ("metric", "coverage_agreement_with_adjuster"),
    "eval:amount-accuracy": ("metric", "amount_accuracy_within_1usd"),
    "eval:edge-cases": ("metric", "routing_correctness"),
    "eval:verification": ("metric", "delay_fact_verification_accuracy"),
    "eval:decision-package": ("metric", "decision_package_completeness"),
    "rai:red-team": ("metric", "red_team_attacks_blocked"),
    "rai:pii-suite": ("metric", "pii_leak_count"),
    "rai:injection-suite": ("metric", "injection_suite_pass_rate"),
    "rai:letter-review": ("metric", "letter_rubric_pass_rate"),
    "perf-test": ("perf", "perf-report.json"),
    "slo:decision-latency": ("perf", "perf-report.json"),
    "slo:availability": ("slo", "slo:availability"),
    "slo:cost-per-claim": ("slo", "slo:cost-per-claim"),
    "design-review": ("gate", "G2"),
}


def _load(path):
    return json.load(open(path)) if os.path.exists(path) else None


def main(release=None):
    release = release or yaml.safe_load(open(os.path.join(ROOT, "system.yaml")))["release"]
    ev = os.path.join(os.environ.get("EVIDENCE_DIR") or os.path.join(ROOT, "evidence"), release)
    reqs = []
    for p in sorted(glob.glob(os.path.join(ROOT, "docs", "requirements", "*.yaml"))):
        reqs += yaml.safe_load(open(p))["requirements"]
    case_links = {}
    for p in glob.glob(os.path.join(ROOT, "evals", "datasets", "**", "*.jsonl"), recursive=True):
        for line in open(p):
            if line.strip():
                c = json.loads(line)
                for r in c.get("requirements", []):
                    case_links.setdefault(r, []).append(c["case_id"])
    report = _load(os.path.join(ev, "eval-report.json")) or {}
    rai = _load(os.path.join(ev, "rai-report.json"))
    tests = _load(os.path.join(ev, "test-report.json")) or {}
    perf = _load(os.path.join(ev, "perf-report.json"))
    gates = {g["gate"]: g for g in (_load(os.path.join(ev, "gate-records.json")) or {}).get("gates", [])}
    results = {c["case_id"]: c["pass"] for c in report.get("cases", [])}
    metrics = report.get("metrics", {})

    def method_status(m):
        kind, ref = METHOD_EVIDENCE.get(m, (None, None))
        if kind == "metric":
            r = metrics.get(ref)
            return (r or {}).get("passed"), f"eval-report.json#{ref}"
        if kind == "test":
            r = tests.get("modules", {}).get(ref)
            return (None if r is None else r == "passed"), ref
        if kind == "perf":
            return (None if perf is None else perf.get("passed")), "perf-report.json"
        if kind == "slo":
            h = _load(os.path.join(ev, "hypercare-report.json"))
            slo = next((x for x in (h or {}).get("slos", []) if x["id"] == ref), None)
            return (None if slo is None else slo["met"] is not False), "hypercare-report.json#" + ref
        if kind == "gate":
            return ((gates.get(ref) or {}).get("status") == "approved" or None), f"gate {ref}"
        return None, "unmapped"

    rows, uncovered = [], []
    for r in reqs:
        cases = case_links.get(r["id"], [])
        methods = {m: dict(zip(("passed", "evidence"), method_status(m))) for m in r.get("verification", [])}
        statuses = [v["passed"] for v in methods.values()]
        status = "verified" if statuses and all(s is True for s in statuses) else \
            "failed" if any(s is False for s in statuses) else "partially_verified" if any(statuses) else "not_run"
        rows.append({"requirement": r["id"], "text": r["text"], "verification": r.get("verification", []),
                     "methods": methods, "eval_cases": cases, "case_results": {c: results.get(c) for c in cases},
                     "status": status})
        if not r.get("verification"):
            uncovered.append(r["id"])
    by_status = {s: sum(1 for x in rows if x["status"] == s) for s in ("verified", "partially_verified", "failed", "not_run")}
    out = {"release": release, "requirements": len(reqs), "without_verification": uncovered,
           "with_eval_cases": sum(1 for x in rows if x["eval_cases"]), "by_status": by_status,
           "rai_report_present": rai is not None, "matrix": rows}
    os.makedirs(ev, exist_ok=True)
    json.dump(out, open(os.path.join(ev, "traceability-matrix.json"), "w"), indent=2)
    print(f"{len(reqs)} requirements; {out['with_eval_cases']} linked to eval cases; {len(uncovered)} without "
          f"verification method; status {by_status} -> evidence/{release}/traceability-matrix.json")
    return 1 if uncovered or by_status["failed"] else 0


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:2] or [])))
