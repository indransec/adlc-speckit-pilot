"""Build REQ -> verification -> result matrix from requirements, eval cases and eval report."""
import glob, json, os, sys
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(release="0.1.0"):
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
    report_p = os.path.join(ROOT, "evidence", release, "eval-report.json")
    results = {}
    if os.path.exists(report_p):
        for c in json.load(open(report_p))["cases"]:
            results[c["case_id"]] = c["pass"]
    rows, uncovered = [], []
    for r in reqs:
        cases = case_links.get(r["id"], [])
        row = {"requirement": r["id"], "verification": r.get("verification", []), "eval_cases": cases,
               "case_results": {c: results.get(c) for c in cases}}
        if not r.get("verification"):
            uncovered.append(r["id"])
        rows.append(row)
    out = {"release": release, "requirements": len(reqs), "without_verification": uncovered,
           "with_eval_cases": sum(1 for x in rows if x["eval_cases"]), "matrix": rows}
    os.makedirs(os.path.join(ROOT, "evidence", release), exist_ok=True)
    json.dump(out, open(os.path.join(ROOT, "evidence", release, "traceability-matrix.json"), "w"), indent=2)
    print(f"{len(reqs)} requirements; {out['with_eval_cases']} linked to eval cases; "
          f"{len(uncovered)} without verification method -> evidence/{release}/traceability-matrix.json")
    return 1 if uncovered else 0


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:2] or [])))
