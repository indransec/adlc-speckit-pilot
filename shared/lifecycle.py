"""ADLC lifecycle engine: gate checks (G0-G7), approvals, lifecycle actions and a full simulation.

Gate records live in evidence/<release>/gate-records.json (generated, never hand-edited).
A gate can be approved only when its automated evidence checks pass, the previous gate is
approved, and every named approver role has signed.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

from shared import db, metrics
from shared.config import ROOT, load_yaml, release, system_manifest
from shared.runtime import get_runtime, set_runtime

BUNDLE_PATHS = ["system.yaml", "agents", "tools", "guardrails", "deploy/models.yaml",
                "docs/design/tool-permission-matrix.yaml", "evals/thresholds.yaml"]
PHASES = {  # deploy/rollout-plan.md
    "shadow": {"mode": "shadow", "straight_through_enabled": False, "payments_enabled": False},
    "assisted": {"mode": "assisted", "straight_through_enabled": False, "payments_enabled": True},
    "full": {"mode": "full", "straight_through_enabled": True, "payments_enabled": True},
}
SIM_APPROVERS = {
    "Sponsor (Head of Claims)": "sim.head-of-claims", "Risk": "sim.risk-officer", "Data owner": "sim.data-owner",
    "Business owner (Head of Claims)": "sim.head-of-claims", "AI lead": "sim.ai-lead", "Architecture": "sim.architect",
    "Security": "sim.security-architect", "Product owner": "sim.product-owner", "RAI/Risk": "sim.rai-officer",
    "Compliance": "sim.compliance", "CAB": "sim.cab-chair", "Head of Claims": "sim.head-of-claims",
}


def ev_dir() -> Path:
    return ROOT / "evidence" / release()


def _read(name: str):
    p = ev_dir() / name
    return json.loads(p.read_text()) if p.exists() else None


def _write(name: str, data) -> Path:
    ev_dir().mkdir(parents=True, exist_ok=True)
    p = ev_dir() / name
    p.write_text(json.dumps(data, indent=2, default=str))
    return p


def _exists(*paths) -> tuple[bool, str]:
    missing = [p for p in paths if not (ROOT / p).exists()]
    return (not missing, "all present" if not missing else "missing: " + ", ".join(missing))


def _text(p: str) -> str:
    return (ROOT / p).read_text() if (ROOT / p).exists() else ""


def _metric(name: str):
    r = (_read("eval-report.json") or {}).get("metrics", {}).get(name)
    return r


def _reqs() -> list[dict]:
    out = []
    for p in sorted((ROOT / "docs" / "requirements").glob("*.yaml")):
        out += yaml.safe_load(p.read_text())["requirements"]
    return out


# ---------------------------------------------------------------------------------------------- checks
def c_risk_tier():
    t = _text("docs/risk-assessment.md")
    ok = "Tier: HIGH" in t and system_manifest()["risk_tier"] == "high"
    return ok, "risk-assessment.md tier HIGH = system.yaml risk_tier" if ok else "tier missing or inconsistent"


def c_feasibility():
    t = _text("docs/feasibility.md")
    ok = "Go with conditions" in t and "| Condition | Owner | Due |" in t
    return ok, "verdict 'Go with conditions' with owned conditions" if ok else "verdict / conditions missing"


def c_req_verification():
    reqs = _reqs()
    ids = [r["id"] for r in reqs]
    bad = [r["id"] for r in reqs if not r.get("verification")]
    ok = bool(reqs) and not bad and len(ids) == len(set(ids))
    return ok, f"{len(reqs)} requirements, all with verification methods" if ok else f"without verification: {bad}"


def c_routing_defined():
    import importlib
    sys.path.insert(0, str(ROOT / "agents" / "orchestrator" / "src"))
    routing = importlib.import_module("routing")
    r = system_manifest()["routing"]
    ok = (routing.STP_LIMIT_USD == r["stp_limit_usd"] and routing.SIGNIFICANCE_NET_PCT == r["significance_net_pct"]
          and routing.CONFIDENCE_CUTOFF == r["confidence_cutoff"] and system_manifest().get("hitl_points"))
    return bool(ok), "routing.py constants match system.yaml; HITL points defined" if ok else "routing constants drift from system.yaml"


def c_kpis():
    k = load_yaml("docs/kpis.yaml")["kpis"]
    ok = all(x.get("target") for x in k)
    return ok, f"{len(k)} KPIs with targets (baselines '[...]' are placeholders to confirm)"


def c_thresholds():
    t = load_yaml("evals/thresholds.yaml")["thresholds"]
    ok = {"routing_correctness", "type_b_labelled_as_a", "pii_leak_count"} <= set(t)
    return ok, f"{len(t)} thresholds incl. zero-tolerance controls"


def c_manifests():
    p = subprocess.run([sys.executable, "scripts/validate_manifests.py"], cwd=ROOT, capture_output=True, text=True)
    return p.returncode == 0, p.stdout.strip().splitlines()[-1] if p.stdout else p.stderr[-200:]


def c_adrs():
    n = len(list((ROOT / "docs" / "design" / "adr").glob("ADR-*.md")))
    return n >= 5, f"{n} ADRs"


def c_tool_matrix():
    from shared.agent_runtime import check_attachments
    matrix = {t["id"]: t for t in load_yaml("docs/design/tool-permission-matrix.yaml")["tools"]}
    tools = {yaml.safe_load(p.read_text())["id"] for p in (ROOT / "tools").glob("*/tool.yaml")}
    problems = [f"tool {t} not in matrix" for t in tools - set(matrix)]
    for p in (ROOT / "agents").glob("*/agent.yaml"):
        m = yaml.safe_load(p.read_text())
        for t in m.get("tools") or []:
            if m["name"] not in matrix.get(t["id"], {}).get("agents", []):
                problems.append(f"{m['name']} uses {t['id']} without a matrix entry")
        problems += check_attachments({**m, "name": m["name"]})
    return not problems, ("every tool and agent covered; model-attached operations are read-only"
                          if not problems else "; ".join(problems))


def c_threat_model():
    t = _text("docs/design/threat-model.md")
    ok = all(f"| T{i} |" in t for i in range(1, 10)) and "injection" in t.lower()
    return ok, "T1-T9 incl. document injection" if ok else "threats missing"


def c_data_flows():
    t = _text("docs/design/data-flows.md")
    return "Redaction" in t, "redaction points documented"


def c_codeowners():
    t = _text("CODEOWNERS")
    ok = "/agents/**/prompts/" in t and "/guardrails/" in t and "routing.py" in t
    return ok, "prompts, guardrails and routing rules need named reviewers"


def c_datasets():
    files = ["evals/datasets/golden/routing_cases.jsonl", "evals/datasets/golden/claims_cases.jsonl",
             "evals/datasets/adversarial/cases.jsonl", "evals/datasets/adversarial/e2e_cases.jsonl"]
    ok, _ = _exists(*files)
    hashes = {f.split("/")[-1]: hashlib.sha256((ROOT / f).read_bytes()).hexdigest()[:12] for f in files if (ROOT / f).exists()}
    return ok, ", ".join(f"{k}@{v}" for k, v in hashes.items())


def c_evals_pass():
    r = _read("eval-report.json")
    if not r:
        return False, "no eval-report.json - run evals"
    failed = [k for k, v in r.get("metrics", {}).items() if v.get("passed") is False]
    ok = r.get("passed") and not r.get("quick")
    return bool(ok), f"{len(r.get('metrics', {}))} metrics, llm={r.get('llm_mode')}, failed={failed}" + (" (quick run)" if r.get("quick") else "")


def c_zero_mislabels():
    m = _metric("type_b_labelled_as_a")
    return (m is not None and m["value"] == 0), f"type_b_labelled_as_a = {m['value'] if m else 'n/a'}"


def c_traceability():
    t = _read("traceability-matrix.json")
    if not t:
        return False, "no traceability-matrix.json"
    ok = not t["without_verification"] and not t.get("by_status", {}).get("failed")
    return ok, f"status {t.get('by_status')}"


def c_perf():
    p = _read("perf-report.json")
    return (bool(p and p["passed"]), f"p95 {p['p95_package_latency_s']}s at {p['concurrency']} concurrent, {p['errors']} errors"
            if p else "no perf-report.json")


def c_unit_tests():
    t = _read("test-report.json")
    return (bool(t and t["passed"]), f"{t['tests']} tests, {t['failed']} failed" if t else "no test-report.json")


def c_fairness():
    r = _read("rai-report.json")
    v = (r or {}).get("fairness", {}).get("counterfactual", {}).get("counterfactual_fairness_parity")
    return (v is not None and v >= 0.95), f"counterfactual parity {v}"


def c_pii_injection():
    a, b = _metric("pii_leak_count"), _metric("injection_suite_pass_rate")
    ok = bool(a and a["passed"] and b and b["passed"])
    return ok, f"pii_leak_count={a and a['value']}, injection_pass_rate={b and b['value']}"


def c_red_team():
    r = _read("rai-report.json")
    if not r:
        return False, "no rai-report.json"
    allowed = [f["attack"] for f in r["red_team"] if f["result"] != "blocked"]
    return not allowed, f"{len(r['red_team'])} automated attacks, allowed: {allowed or 'none'}"


def c_rai_card():
    return _exists("docs/rai/rai-card.md", "docs/rai/transparency-notice.md")


def c_letters():
    t = load_yaml("agents/communication/templates/approved.yaml")["templates"]
    m = _metric("letter_compliance_rate")
    ok = all(x["status"] == "approved" for x in t) and bool(m and m["passed"])
    return ok, f"{len(t)} templates approved; letter compliance {m and m['value']}"


def c_bundle():
    rec = _read("release-record.json")
    if not rec:
        return False, "no release-record.json - bundle the release"
    cur = bundle_hash()
    return rec["bundle_hash"] == cur, f"sha256 {rec['bundle_hash'][:16]}" + ("" if rec["bundle_hash"] == cur else " DRIFT: code changed since bundling")


def c_shadow():
    s = _read("shadow-report.json")
    return (bool(s and s["passed"]), f"route agreement {s['route_agreement']}, amount agreement {s['amount_agreement']} on {s['claims']} claims"
            if s else "no shadow-report.json")


def c_drill(kind):
    def f():
        d = (_read("drills.json") or {}).get(kind)
        return (bool(d and d["passed"]), d["summary"] if d else f"{kind} drill not run")
    return f


def c_g3g4():
    a, b = _read("eval-report.json"), _read("rai-report.json")
    return bool(a and a["passed"] and b and b["passed"]), "eval + RAI evidence passed" if a and b else "missing G3/G4 evidence"


def c_hypercare():
    h = _read("hypercare-report.json")
    return (bool(h and h["claims"] >= 20 and h["mode"] == "full"),
            f"{h['claims']} claims in full mode over a simulated {h['simulated_days']} days" if h else "no hypercare-report.json")


def c_hypercare_part(part):
    def f():
        h = _read("hypercare-report.json")
        if not h:
            return False, "no hypercare-report.json"
        if part == "slos":
            bad = [s["id"] for s in h["slos"] if s["met"] is False]
            return not bad, "all SLOs met" if not bad else f"breached: {bad}"
        if part == "controls":
            n = h["controls"]["payments_without_valid_token"]
            return n == 0, f"payments without valid token = {n}; blocked attempts logged"
        if part == "alerts":
            firing = [a["name"] for a in h["alerts"] if a["firing"] and a["severity"] in ("sev1", "sev2")]
            return not firing, "no Sev1/Sev2 firing" if not firing else f"firing: {firing}"
        if part == "qa":
            k = next(k for k in h["kpis"] if k["id"] == "KPI-04")
            return bool(k["met"]), f"payment accuracy {k['value']} vs baseline {k['baseline']}"
        if part == "dashboard":
            vals = [k for k in h["kpis"] if k["id"] != "KPI-06" and k["value"] is not None]
            return len(vals) >= 5, f"{len(vals)} value KPIs live"
    return f


def c_review(section):
    def f():
        p = ROOT / "docs" / "reviews" / f"G7-review-{release()}.md"
        if not p.exists():
            return False, "G7 review not generated"
        return section in p.read_text(), f"{p.relative_to(ROOT)} contains '{section}'"
    return f


GATES = [
    {"gate": "G0", "stage": "Intake & triage", "approvers": ["Sponsor (Head of Claims)", "Risk", "Data owner"], "checks": [
        ("Use-case card, feasibility, risk assessment, RACI", lambda: _exists("docs/use-case-card.md", "docs/feasibility.md", "docs/risk-assessment.md", "docs/raci.md")),
        ("Risk tier assessed", c_risk_tier), ("Data classification agreed", lambda: ("Data classification" in _text("docs/risk-assessment.md"), "classification table present")),
        ("Feasibility verdict with owned conditions", c_feasibility)]},
    {"gate": "G1", "stage": "Requirements", "approvers": ["Business owner (Head of Claims)", "AI lead"], "checks": [
        ("Requirements with IDs and verification methods", c_req_verification),
        ("Autonomy level and HITL points (routing matrix)", c_routing_defined),
        ("KPI baseline and targets", c_kpis), ("Eval thresholds agreed", c_thresholds)]},
    {"gate": "G2", "stage": "Design", "approvers": ["Architecture", "Security"], "checks": [
        ("Architecture documented; manifests validate", c_manifests), ("ADRs recorded", c_adrs),
        ("Tool permission matrix covers every tool and agent", c_tool_matrix),
        ("Threat model: OWASP Agentic + document injection", c_threat_model),
        ("Data flows and redaction points", c_data_flows), ("Change control on prompts / routing", c_codeowners)]},
    {"gate": "G3", "stage": "Test & evals", "approvers": ["AI lead", "Product owner"], "checks": [
        ("Unit and routing tests pass (CI)", c_unit_tests), ("Datasets versioned", c_datasets),
        ("Component + system evals pass all thresholds", c_evals_pass), ("Zero type B -> A mislabels", c_zero_mislabels),
        ("Traceability: requirements linked to verification", c_traceability), ("Performance test at 3x peak", c_perf)]},
    {"gate": "G4", "stage": "RAI assurance", "approvers": ["RAI/Risk", "Compliance"], "checks": [
        ("Fairness test (counterfactual parity)", c_fairness), ("PII and injection suites pass", c_pii_injection),
        ("Red-team findings fixed (automated battery)", c_red_team), ("RAI card and transparency notice", c_rai_card),
        ("Customer letters approved", c_letters)]},
    {"gate": "G5", "stage": "Deploy", "approvers": ["CAB", "Head of Claims"], "checks": [
        ("Release record with bundle hash", c_bundle), ("G3 and G4 evidence for this release", c_g3g4),
        ("Shadow-mode results meet thresholds", c_shadow), ("Rollback plan and runbook tested", c_drill("rollback")),
        ("Kill switch exercised", c_drill("kill_switch"))]},
    {"gate": "G6", "stage": "Run & observe (hypercare)", "approvers": ["Head of Claims"], "checks": [
        ("Hypercare traffic in full mode", c_hypercare), ("SLOs met", c_hypercare_part("slos")),
        ("No control breaches", c_hypercare_part("controls")), ("No Sev1/Sev2 alerts", c_hypercare_part("alerts")),
        ("QA error rate within threshold", c_hypercare_part("qa")), ("Dashboard live with value KPIs", c_hypercare_part("dashboard"))]},
    {"gate": "G7", "stage": "Improve / retire", "approvers": ["Head of Claims", "AI lead"], "checks": [
        ("Override reasons reviewed", c_review("Override reasons")), ("Drift and calibration checked", c_review("Confidence calibration")),
        ("Value realised vs hypothesis", c_review("Value realised"))]},
]
GATE_IDS = [g["gate"] for g in GATES]


# ---------------------------------------------------------------------------------------------- records
def records() -> dict:
    data = _read("gate-records.json")
    if not data:
        data = json.loads((ROOT / "evidence" / "gate-records.template.json").read_text())
        data["release"] = release()
    return data


def _save_records(data: dict):
    _write("gate-records.json", data)


def _record(data, gate):
    return next(g for g in data["gates"] if g["gate"] == gate)


def check_gate(gate: str) -> dict:
    g = next(x for x in GATES if x["gate"] == gate)
    checks = []
    for label, fn in g["checks"]:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"check error: {e}"
        checks.append({"check": label, "ok": bool(ok), "detail": detail})
    rec = _record(records(), gate)
    idx = GATE_IDS.index(gate)
    prev_ok = idx == 0 or _record(records(), GATE_IDS[idx - 1])["status"] == "approved"
    return {"gate": gate, "stage": g["stage"], "approvers_required": g["approvers"], "checks": checks,
            "checks_passed": all(c["ok"] for c in checks), "previous_gate_approved": prev_ok,
            "ready": all(c["ok"] for c in checks) and prev_ok, "record": rec}


def all_gates() -> list[dict]:
    return [check_gate(g) for g in GATE_IDS]


def approve(gate: str, approvals: list[dict], decision: str = "approved", notes: str = "") -> dict:
    st = check_gate(gate)
    if decision == "approved":
        if not st["previous_gate_approved"]:
            raise ValueError(f"{gate}: previous gate not approved")
        if not st["checks_passed"]:
            raise ValueError(f"{gate}: evidence checks failing: " + "; ".join(c["check"] for c in st["checks"] if not c["ok"]))
        roles = {a["role"] for a in approvals}
        missing = [r for r in st["approvers_required"] if r not in roles]
        if missing:
            raise ValueError(f"{gate}: missing approver role(s): {missing}")
    data = records()
    rec = _record(data, gate)
    rec.update(status=decision, approvers=approvals, decided_at=datetime.now(timezone.utc).isoformat(), notes=notes,
               evidence=[c["detail"] for c in st["checks"]], checks=st["checks"])
    _save_records(data)
    from shared.audit import audit
    audit(None, f"gate_{decision}:{gate}", {"approvers": approvals, "notes": notes}, actor="lifecycle")
    return rec


def reset_gates(from_gate: str = "G0") -> None:
    data = records()
    for g in data["gates"][GATE_IDS.index(from_gate):]:
        g.update(status="pending", approvers=[], decided_at=None, evidence=[], notes="", checks=[])
    _save_records(data)


def sim_approvals(gate: str) -> list[dict]:
    g = next(x for x in GATES if x["gate"] == gate)
    return [{"role": r, "name": SIM_APPROVERS.get(r, "sim.approver"), "at": datetime.now(timezone.utc).isoformat()}
            for r in g["approvers"]]


# ---------------------------------------------------------------------------------------------- actions
def _run(cmd: list[str]) -> dict:
    env = {**os.environ, "PYTHONPATH": f"{ROOT}{os.pathsep}{ROOT / 'agents' / 'orchestrator' / 'src'}"}
    p = subprocess.run([sys.executable, *cmd], cwd=ROOT, capture_output=True, text=True, env=env)
    out = (p.stdout + ("\n" + p.stderr if p.returncode else "")).strip()
    out = "\n".join(l for l in out.splitlines() if "Warning" not in l and "warn(" not in l)
    return {"ok": p.returncode == 0, "output": out[-6000:]}


def bundle_hash() -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel in BUNDLE_PATHS:
            p = ROOT / rel
            files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file() and "__pycache__" not in x.parts
                                                    and x.name != ".DS_Store")
            for f in files:
                info = tarfile.TarInfo(str(f.relative_to(ROOT)))
                data = f.read_bytes()
                info.size, info.mtime = len(data), 0
                tar.addfile(info, io.BytesIO(data))
    return hashlib.sha256(buf.getvalue()).hexdigest()


def bundle_release() -> dict:
    from shared.registry import manifests
    h = bundle_hash()
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = None
    rec = {"system": system_manifest()["id"], "release": release(), "bundle_hash": h, "bundled_paths": BUNDLE_PATHS,
           "git_commit": commit, "created_at": datetime.now(timezone.utc).isoformat(),
           "agents": {m["id"]: {"version": m["version"], "model": m["model"]["name"]} for m in manifests().values()}}
    _write("release-record.json", rec)
    return {"ok": True, "output": f"bundle sha256 {h}"}


def set_phase(phase: str) -> dict:
    for k, v in PHASES[phase].items():
        set_runtime(k, v, by=f"rollout:{phase}")
    return {"ok": True, "output": f"rollout phase {phase}: {PHASES[phase]}"}


def _process_all(ids: list[str]) -> int:
    from backend import service
    errors = 0
    for cid in ids:
        try:
            service.process(cid)
        except Exception:
            errors += 1
    return errors


def shadow_run(n: int = 20, seed: int | None = None) -> dict:
    from synthetic.generate import add_traffic
    prev = {k: get_runtime()[k] for k in ("mode", "straight_through_enabled", "payments_enabled")}
    set_phase("shadow")
    try:
        ids = add_traffic(n, seed)
        errors = _process_all(ids)
    finally:
        for k, v in prev.items():
            set_runtime(k, v, by="shadow-run-restore")
    cs = [db.one("SELECT * FROM claims WHERE claim_id = ?", [i]) for i in ids]
    route_ok, amt_ok, total = 0, 0, 0
    for c in cs:
        gt = db.jload(c["ground_truth"], {})
        if c["status"] != "shadow_complete" or not gt.get("route"):
            continue
        total += 1
        route_ok += c["route"] == gt["route"]
        amt_ok += gt.get("amount_check") is False or gt.get("amount") is None or abs((c["recommended_usd"] or 0) - gt["amount"]) <= 1
    th = load_yaml("evals/thresholds.yaml")["thresholds"]
    rep = {"claims": total, "submitted": n, "errors": errors, "route_agreement": round(route_ok / total, 3) if total else None,
           "amount_agreement": round(amt_ok / total, 3) if total else None,
           "payments_created": db.scalar(f"SELECT COUNT(*) FROM payments WHERE claim_id IN ({','.join('?' * len(ids))})", ids),
           "thresholds": {"route": th["routing_correctness"]["min"], "amount": th["amount_accuracy_within_1usd"]["min"]},
           "generated_at": datetime.now(timezone.utc).isoformat()}
    rep["passed"] = bool(total) and rep["route_agreement"] >= rep["thresholds"]["route"] and \
        rep["amount_agreement"] >= rep["thresholds"]["amount"] and rep["payments_created"] == 0 and not errors
    _write("shadow-report.json", rep)
    return {"ok": rep["passed"], "output": json.dumps(rep, indent=1) + f"\nroute agreement {rep['route_agreement']}, "
                                                                     f"amount agreement {rep['amount_agreement']} on {total} claims"}


def kill_switch_drill() -> dict:
    from shared.hitl import approval_token
    from shared.tool_gateway import call_tool
    from synthetic.generate import add_traffic
    from tools.payments.impl import PaymentRejected
    drills = _read("drills.json") or {}
    set_runtime("kill_switch", True, by="drill")
    try:
        cid = add_traffic(1, scenarios={"delay_stp": 1})[0]
        _process_all([cid])
        c = db.one("SELECT status FROM claims WHERE claim_id = ?", [cid])
        try:
            call_tool("settlement", "TOOL-PAYMENTS", "create_instruction", claim_id=cid, amount_usd=1.0,
                      approval_token_str=approval_token.issue(cid, 1.0, 1, []))
            blocked = False
        except PaymentRejected:
            blocked = True
    finally:
        set_runtime("kill_switch", False, by="drill")
    ok = c["status"] == "manual_queue" and blocked
    drills["kill_switch"] = {"passed": ok, "at": datetime.now(timezone.utc).isoformat(),
                             "summary": f"new claim -> {c['status']}; payment attempt {'blocked' if blocked else 'ALLOWED'}"}
    _write("drills.json", drills)
    return {"ok": ok, "output": drills["kill_switch"]["summary"]}


def rollback_drill() -> dict:
    drills = _read("drills.json") or {}
    rec = _read("release-record.json")
    steps = [l.strip() for l in _text("deploy/rollback.md").splitlines() if re.match(r"^\d\.", l.strip())]
    ok = bool(rec) and bool(drills.get("kill_switch", {}).get("passed")) and len(steps) >= 4
    drills["rollback"] = {"passed": ok, "at": datetime.now(timezone.utc).isoformat(), "steps": steps,
                          "summary": (f"tabletop: {len(steps)} runbook steps walked; kill switch verified; redeploy target "
                                      f"bundle {rec['bundle_hash'][:12] if rec else '?'}") if ok else
                          "needs release record and a passed kill-switch drill"}
    _write("drills.json", drills)
    return {"ok": ok, "output": drills["rollback"]["summary"]}


def hypercare(n: int = 40, seed: int | None = None) -> dict:
    from synthetic import adjuster_bot
    from synthetic.generate import add_traffic
    set_phase("full")
    ids = add_traffic(n, seed)
    errors = _process_all(ids)
    decisions = adjuster_bot.drain(ids)
    rep = {"mode": "full", "claims": n, "errors": errors, "human_decisions": decisions, "simulated_days": 30,
           "slos": metrics.slos(), "alerts": metrics.alerts(), "controls": metrics.controls(), "kpis": metrics.kpis(),
           "routing_mix": metrics.routing_mix(), "generated_at": datetime.now(timezone.utc).isoformat()}
    _write("hypercare-report.json", rep)
    ok = all(s["met"] is not False for s in rep["slos"]) and rep["controls"]["payments_without_valid_token"] == 0
    return {"ok": ok, "output": f"{n} claims, {decisions} human decisions, {errors} errors; "
                                f"SLOs met={all(s['met'] is not False for s in rep['slos'])}"}


def g7_review() -> dict:
    import random

    from synthetic.scenarios import SCENARIOS, TRAFFIC_WEIGHTS
    k = {x["id"]: x for x in metrics.kpis()}
    mix = metrics.routing_mix()
    exp_rows: dict[int, int] = {}
    for sc, w in TRAFFIC_WEIGHTS.items():
        row = SCENARIOS[sc](datetime(2026, 9, 1).date(), random.Random(0))["expected"]["row"]
        if row:
            exp_rows[row] = exp_rows.get(row, 0) + w
    tot = sum(exp_rows.values())
    reasons = metrics.override_reasons()
    cal = metrics.calibration()
    type_a = db.query("SELECT a.override FROM approvals a JOIN claims c USING(claim_id) WHERE c.route_row = 2 AND a.level = 'L1'")
    agree_a = 1 - (sum(r["override"] for r in type_a) / len(type_a)) if type_a else None
    mix_now = {m["row"]: m["pct"] for m in mix}
    lines = [f"# G7 periodic review: release {release()} ({datetime.now().strftime('%B %Y')})", "",
             "_Generated from production telemetry and the evidence store by `adlc g7-review`. Simulated data._", "",
             "| Item | Finding | Action |", "|---|---|---|",
             "| Override reasons (top 5) | " + ("; ".join(f"{r['level']} {r['decision']}: {r['notes']} (x{r['n']})" for r in reasons) or "none")
             + " | Add each reason as a golden eval case |",
             "| Routing mix vs expectation | " + "; ".join(f"row {r}: {mix_now.get(r, 0)}% vs {round(100 * exp_rows.get(r, 0) / tot, 1)}% expected"
                                                     for r in range(1, 8)) + " | Investigate rows deviating > 5 pts |",
             "| Confidence calibration (0.7) | " + "; ".join(f"{c['confidence_bin']}: {c['agreement']} agreement (n={c['claims']})" for c in cal)
             + " | Recalibrate if agreement in >=0.7 bins < 0.95 |",
             "| Drift in claim mix / documents | claim types: " + ", ".join(f"{r['claim_type']} {r['n']}" for r in db.query(
                 "SELECT claim_type, COUNT(*) n FROM claims WHERE historical = 0 AND claim_type IS NOT NULL GROUP BY 1"))
             + f"; mean extraction certainty {db.scalar('SELECT ROUND(AVG(extraction_certainty), 3) FROM documents')} | Compare with golden set mix |",
             f"| Value realised vs KPIs | STP {k['KPI-02']['value']}, handling {k['KPI-01']['value']}d vs {k['KPI-01']['baseline']}d, "
             f"adjuster min {k['KPI-03']['value']} vs {k['KPI-03']['baseline']}, override {k['KPI-05']['value']}, QA accuracy {k['KPI-04']['value']} | Report to sponsor |",
             f"| Authority widening proposal | L1 agreement on type A (row 2): {agree_a if agree_a is None else round(agree_a, 3)} over {len(type_a)} claims | "
             + ("Propose widening row 1 to type A <= USD 100 via new release + G3" if agree_a and agree_a > 0.98 and len(type_a) >= 20 else "Not yet: need > 98% over >= 20 claims") + " |",
             "| Decision | " + ("continue" if k["KPI-04"]["met"] else "iterate") + " | Head of Claims to confirm at G7 |", "",
             "## Override reasons", "", *[f"- {r['level']} `{r['decision']}`: {r['notes']} (x{r['n']})" for r in reasons], "",
             "## Confidence calibration", "", "| Confidence | Claims | Agreement with final decision |", "|---|---|---|",
             *[f"| {c['confidence_bin']} | {c['claims']} | {c['agreement']} |" for c in cal], "",
             "## Value realised", "", "| KPI | Baseline | Value | Target | Met |", "|---|---|---|---|---|",
             *[f"| {x['id']} {x['name']} | {x['baseline']} | {x['value']} | {x['target']} | {x['met']} |" for x in k.values()]]
    p = ROOT / "docs" / "reviews" / f"G7-review-{release()}.md"
    p.write_text("\n".join(lines) + "\n")
    return {"ok": True, "output": f"wrote {p.relative_to(ROOT)}"}


ACTIONS: dict[str, tuple[str, Callable]] = {
    "validate_manifests": ("G2 - validate system/agent manifests", lambda **_: _run(["scripts/validate_manifests.py"])),
    "unit_tests": ("Build/CI - unit + routing tests", lambda **_: _run(["scripts/run_tests.py", release()])),
    "evals": ("G3/G4 - run eval suites (isolated data)", lambda **_: _run(["evals/run_evals.py", release()])),
    "traceability": ("G3 - build traceability matrix", lambda **_: _run(["scripts/build_traceability.py", release()])),
    "perf_test": ("G3 - performance test at 3x peak", lambda **_: _run(["evals/perf_test.py", release()])),
    "bundle_release": ("G5 - bundle release + hash", lambda **_: bundle_release()),
    "shadow_run": ("G5 - shadow-mode run on new traffic", lambda n=20, **_: shadow_run(int(n))),
    "kill_switch_drill": ("G5 - exercise kill switch", lambda **_: kill_switch_drill()),
    "rollback_drill": ("G5 - rollback tabletop", lambda **_: rollback_drill()),
    "set_phase": ("G5/G6 - set rollout phase", lambda phase="full", **_: set_phase(phase)),
    "hypercare": ("G6 - simulate hypercare traffic", lambda n=40, **_: hypercare(int(n))),
    "g7_review": ("G7 - generate periodic review", lambda **_: g7_review()),
    "simulate_lifecycle": ("All - walk G0 -> G7 with simulated approvers", lambda **_: _simulate_action()),
}


def _simulate_action() -> dict:
    lines: list[str] = []
    res = simulate(log=lines.append)
    ok = bool(res) and all(g["record"]["status"] == "approved" for g in res) and len(res) == len(GATES)
    return {"ok": ok, "output": "\n".join(lines)}


def _execute(job_id: str, name: str, kw: dict) -> dict:
    db.update("jobs", "job_id", job_id, {"status": "running"})
    try:
        r = ACTIONS[name][1](**kw)
        status, out = ("succeeded" if r["ok"] else "failed"), r["output"]
    except Exception as e:
        status, out = "failed", f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
    db.update("jobs", "job_id", job_id, {"status": status, "finished_at": db.now(), "output": out})
    return {"job_id": job_id, "action": name, "status": status, "output": out}


def _new_job(name: str) -> str:
    if name not in ACTIONS:
        raise KeyError(f"unknown action {name}")
    job_id = uuid.uuid4().hex[:10]
    db.insert("jobs", {"job_id": job_id, "action": name, "status": "queued", "started_at": db.now()})
    return job_id


def run_action(name: str, **kw) -> dict:
    return _execute(_new_job(name), name, kw)


def run_action_async(name: str, **kw) -> str:
    job_id = _new_job(name)
    threading.Thread(target=_execute, args=(job_id, name, kw), daemon=True).start()
    return job_id


# ---------------------------------------------------------------------------------------------- simulation
PLAN = [
    ("G0", []), ("G1", []), ("G2", ["validate_manifests"]),
    ("G3", ["unit_tests", "evals", "perf_test", "traceability"]),
    ("G4", []),
    ("G5", ["bundle_release", ("shadow_run", {"n": 20}), "kill_switch_drill", "rollback_drill"]),
    ("G6", [("set_phase", {"phase": "assisted"}), ("hypercare", {"n": 40})]),
    ("G7", ["g7_review"]),
]


def simulate(log: Callable[[str], None] = print, stop_on_fail: bool = True) -> list[dict]:
    """Walk the whole lifecycle: run each stage's actions, check the gate, sign it with simulated approvers."""
    out = []
    reset_gates()
    for gate, actions in PLAN:
        log(f"--- {gate} {next(g['stage'] for g in GATES if g['gate'] == gate)}")
        for a in actions:
            name, kw = (a, {}) if isinstance(a, str) else a
            r = run_action(name, **kw)
            log(f"    action {name}: {r['status']} - {r['output'].splitlines()[-1] if r['output'] else ''}")
        st = check_gate(gate)
        for c in st["checks"]:
            log(f"    [{'x' if c['ok'] else ' '}] {c['check']}: {c['detail']}")
        if st["ready"]:
            approve(gate, sim_approvals(gate), "approved", "simulated sign-off")
            log(f"    {gate} APPROVED by {', '.join(st['approvers_required'])}")
        else:
            log(f"    {gate} NOT READY")
            out.append(st)
            if stop_on_fail:
                break
        out.append(check_gate(gate))
    run_action("traceability")
    return out
