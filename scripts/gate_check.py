"""Gate readiness check: confirms required evidence files exist and checklist items are ticked.
(The helper-agent version adds content checks on top of this.)"""
import argparse, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = {
    "G0": ["docs/use-case-card.md", "docs/feasibility.md", "docs/risk-assessment.md", "docs/raci.md"],
    "G1": ["docs/requirements/functional.yaml", "docs/requirements/non-functional.yaml",
           "docs/requirements/behavioural.yaml", "docs/kpis.yaml", "evals/thresholds.yaml"],
    "G2": ["docs/design/architecture.md", "docs/design/routing-matrix.md", "docs/design/threat-model.md",
           "docs/design/tool-permission-matrix.yaml", "docs/design/data-flows.md", "system.yaml"],
    "G3": ["evidence/{release}/eval-report.json", "evidence/{release}/traceability-matrix.json"],
    "G4": ["docs/rai/rai-card.md", "docs/rai/fairness-test-plan.md", "docs/rai/red-team-plan.md",
           "evidence/{release}/rai-report.json"],
    "G5": ["deploy/rollback.md", "deploy/runbook.md", "deploy/release-checklist.md", "deploy/rollout-plan.md"],
    "G6": ["observability/slos.yaml", "observability/alerts.yaml"],
    "G7": ["docs/reviews/G7-review-template.md"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", required=True, choices=sorted(REQUIRED))
    ap.add_argument("--release", default="0.1.0")
    a = ap.parse_args()
    missing = [p for p in (x.format(release=a.release) for x in REQUIRED[a.gate])
               if not os.path.exists(os.path.join(ROOT, p))]
    checklist = os.path.join(ROOT, "docs", "gates", f"{a.gate}.md")
    open_items = len(re.findall(r"\[ \]", open(checklist).read())) if os.path.exists(checklist) else None
    for m in missing:
        print("MISSING", m)
    print(f"{a.gate}: {len(REQUIRED[a.gate]) - len(missing)}/{len(REQUIRED[a.gate])} evidence files present; "
          f"{open_items} checklist items still open")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
