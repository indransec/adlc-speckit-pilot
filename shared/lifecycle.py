"""ADLC lifecycle engine: gate checks (G0-G7), approvals, lifecycle actions and a full simulation.

Gate records live in <evidence>/<release>/gate-records.json (generated, never hand-edited).
A gate can be approved only when its automated evidence checks pass, the previous gate is
approved, and every named approver role has signed. In a simulation run each role is signed
separately by a persona holding it (ops/teams.yaml, one role per persona per gate); every
approval or rejection freezes its artifacts into the evidence ledger (shared/ledger.py).
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
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

from shared import clock, db, ledger, metrics, teams, usecase
from shared.config import ROOT, evidence_root, project_root, release, system_manifest
from shared.runtime import get_runtime, set_runtime

BUNDLE_PATHS = ["system.yaml", "agents", "tools", "guardrails", "deploy/models.yaml",
                "docs/design/tool-permission-matrix.yaml", "evals/thresholds.yaml"]
PHASES = {  # deploy/rollout-plan.md
    "shadow": {"mode": "shadow", "straight_through_enabled": False, "payments_enabled": False},
    "assisted": {"mode": "assisted", "straight_through_enabled": False, "payments_enabled": True},
    "full": {"mode": "full", "straight_through_enabled": True, "payments_enabled": True},
}


def ev_dir() -> Path:
    return evidence_root() / release()


def _read(name: str):
    p = ev_dir() / name
    return json.loads(p.read_text()) if p.exists() else None


def _write(name: str, data) -> Path:
    ev_dir().mkdir(parents=True, exist_ok=True)
    p = ev_dir() / name
    p.write_text(json.dumps(data, indent=2, default=str))
    return p


def _exists(*paths) -> tuple[bool, str]:
    missing = [p for p in paths if not (project_root() / p).exists()]
    return (not missing, "all present" if not missing else "missing: " + ", ".join(missing))


def _text(p: str) -> str:
    return (project_root() / p).read_text() if (project_root() / p).exists() else ""


def _pyaml(p: str):
    return yaml.safe_load((project_root() / p).read_text())


def _metric(name: str):
    r = (_read("eval-report.json") or {}).get("metrics", {}).get(name)
    return r


def _reqs() -> list[dict]:
    out = []
    for p in sorted((project_root() / "docs" / "requirements").glob("*.yaml")):
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
    code, r = usecase.routing_constants(), system_manifest()["routing"]
    ok = all(code.get(k) == r[k] for k in ("stp_limit_usd", "significance_net_pct", "confidence_cutoff")) \
        and system_manifest().get("hitl_points")
    return bool(ok), "routing.py constants match system.yaml; HITL points defined" if ok else \
        f"routing constants drift from system.yaml: routing.py {code} vs system.yaml {r}"


def c_kpis():
    k = _pyaml("docs/kpis.yaml")["kpis"]
    ok = all(x.get("target") for x in k)
    return ok, f"{len(k)} KPIs with targets (baselines '[...]' are placeholders to confirm)"


def c_thresholds():
    t = _pyaml("evals/thresholds.yaml")["thresholds"]
    ok = {"routing_correctness", "type_b_labelled_as_a", "pii_leak_count"} <= set(t)
    return ok, f"{len(t)} thresholds incl. zero-tolerance controls"


def c_manifests():
    p = subprocess.run([sys.executable, "scripts/validate_manifests.py"], cwd=ROOT, capture_output=True, text=True)
    return p.returncode == 0, p.stdout.strip().splitlines()[-1] if p.stdout else p.stderr[-200:]


def c_adrs():
    n = len(list((project_root() / "docs" / "design" / "adr").glob("ADR-*.md")))
    return n >= 5, f"{n} ADRs"


def c_tool_matrix():
    from shared.agent_runtime import check_attachments
    matrix = {t["id"]: t for t in _pyaml("docs/design/tool-permission-matrix.yaml")["tools"]}
    tools = {yaml.safe_load(p.read_text())["id"] for p in (project_root() / "tools").glob("*/tool.yaml")}
    problems = [f"tool {t} not in matrix" for t in tools - set(matrix)]
    for p in (project_root() / "agents").glob("*/agent.yaml"):
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
    hashes = {f.split("/")[-1]: hashlib.sha256((project_root() / f).read_bytes()).hexdigest()[:12]
              for f in files if (project_root() / f).exists()}
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
    t = _pyaml("agents/communication/templates/approved.yaml")["templates"]
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
        p = project_root() / "docs" / "reviews" / f"G7-review-{release()}.md"
        if not p.exists():
            return False, "G7 review not generated"
        return section in p.read_text(), f"{p.relative_to(project_root())} contains '{section}'"
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
USE_CASE_GATES = ("G0", "G1", "G2")


def _uc_records_path() -> Path:
    return evidence_root() / _item() / "gate-records.json"


def records(release_: str | None = None) -> dict:
    """Gate records of a release (default: the current one). For a use case captured through the intake
    form, G0-G2 belong to the use case and are approved once; G3-G7 are approved per release."""
    rel = release_ or release()
    p = evidence_root() / rel / "gate-records.json"
    data = json.loads(p.read_text()) if p.exists() else None
    if not data:
        data = json.loads((ROOT / "evidence" / "gate-records.template.json").read_text())
    data["release"] = rel
    if usecase.exists() and _uc_records_path().exists():
        shared = {g["gate"]: g for g in json.loads(_uc_records_path().read_text())["gates"]}
        data["gates"] = [shared.get(g["gate"], g) for g in data["gates"]]
    return data


def _save_records(data: dict):
    if usecase.exists():
        _uc_records_path().parent.mkdir(parents=True, exist_ok=True)
        _uc_records_path().write_text(json.dumps(
            {"use_case": _item(), "gates": [g for g in data["gates"] if g["gate"] in USE_CASE_GATES]}, indent=2, default=str))
    p = evidence_root() / data["release"] / "gate-records.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, default=str))


def _record(data, gate):
    return next(g for g in data["gates"] if g["gate"] == gate)


APPROVED = ("approved", "approved_with_conditions")


def _item() -> str:
    return system_manifest().get("use_case") or system_manifest()["id"]


def c_drift(gate: str):
    """Artifacts approved at earlier gates must not change underneath later ones (shared/ledger.py)."""
    changed = ledger.drift(before_gate=gate, item=_item())
    block = [f"{d['path']} ({d['gate']}, {d['status']})" for d in changed if d["on_drift"] == "block"]
    flag = [f"{d['path']} ({d['gate']}, {d['status']})" for d in changed if d["on_drift"] != "block"]
    if block:
        return False, "changed since approval, re-approve the earlier gate: " + "; ".join(block)
    return True, ("no changes since approval" if not flag else "modified since approval (flagged): " + "; ".join(flag))


def c_cr_shadow():
    from shared import changes
    cr = [c for c in changes.crs(release()) if c["kind"] in ("deploy_shadow", "emergency")]
    ok = any(c["status"] == "approved" for c in cr)
    return ok, (f"{cr[-1]['id']} ({cr[-1]['kind']}) {cr[-1]['status']}" if cr else "no deploy-in-shadow change request raised")


def c_deployed_shadow():
    from shared import changes
    d = [e for e in changes.deployments(release()) if e["phase"] in ("shadow", "shadow-replay")]
    return bool(d), (f"{d[-1]['phase']} {d[-1]['ts'][:16]} under {d[-1]['cr']}" if d else f"{release()} not deployed in shadow")


def _checks_for(g: dict) -> list:
    checks = list(g["checks"])
    if usecase.exists():  # captured through the intake form: structured checks replace document greps
        if g["gate"] == "G0":
            checks = usecase.g0_checks()
        elif g["gate"] == "G1":
            checks.append(usecase.g1_check())
        elif g["gate"] == "G5":
            checks[2:2] = [("CR-1: deploy in shadow approved by CAB", c_cr_shadow),
                           ("Deployed to production in shadow mode", c_deployed_shadow)]
    if g["gate"] != "G0":
        checks.append(("Artifacts approved at earlier gates unchanged", lambda: c_drift(g["gate"])))
    return checks


def check_gate(gate: str) -> dict:
    g = next(x for x in GATES if x["gate"] == gate)
    checks = []
    for label, fn in _checks_for(g):
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"check error: {e}"
        checks.append({"check": label, "ok": bool(ok), "detail": detail})
    rec = _record(records(), gate)
    idx = GATE_IDS.index(gate)
    prev_ok = idx == 0 or _record(records(), GATE_IDS[idx - 1])["status"] in APPROVED
    return {"gate": gate, "stage": g["stage"], "approvers_required": g["approvers"], "checks": checks,
            "checks_passed": all(c["ok"] for c in checks), "previous_gate_approved": prev_ok,
            "ready": all(c["ok"] for c in checks) and prev_ok, "record": rec}


def all_gates() -> list[dict]:
    return [check_gate(g) for g in GATE_IDS]


def approve(gate: str, approvals: list[dict], decision: str = "approved", notes: str = "") -> dict:
    """Record a gate decision. Approvals need passing checks, the previous gate and every role;
    approvals and rejections alike freeze their artifacts into the evidence ledger."""
    with ledger.locked():
        st = check_gate(gate)
        if decision in APPROVED:
            if not st["previous_gate_approved"]:
                raise ValueError(f"{gate}: previous gate not approved")
            if not st["checks_passed"]:
                raise ValueError(f"{gate}: evidence checks failing: " + "; ".join(c["check"] for c in st["checks"] if not c["ok"]))
            roles = {a["role"] for a in approvals}
            missing = [r for r in st["approvers_required"] if r not in roles]
            if missing:
                raise ValueError(f"{gate}: missing approver role(s): {missing}")
            people = [a["persona"] for a in approvals if a.get("persona")]
            if len(people) != len(set(people)):
                raise ValueError(f"{gate}: one person cannot sign for two roles at the same gate")
        frozen = ledger.freeze(gate=gate, item=_item(), release=release(), decision=decision, approvers=approvals,
                               checks=st["checks"], notes=notes, ctx={"use_case": _item(), "release": release()},
                               structured=usecase.exists())
        data = records()
        rec = _record(data, gate)
        rec.update(status=decision, approvers=approvals, decided_at=clock.now_iso(), notes=notes,
                   evidence=[c["detail"] for c in st["checks"]], checks=st["checks"], signatures=[],
                   manifest_sha256=frozen["manifest_sha256"])
        _save_records(data)
        if usecase.exists():
            from shared import changes
            changes.on_gate_decision(gate, release(), decision)
    from shared.audit import audit
    audit(None, f"gate_{decision}:{gate}", {"approvers": approvals, "notes": notes,
                                            "manifest_sha256": frozen["manifest_sha256"]}, actor="lifecycle")
    return rec


def sign(gate: str, persona_id: str, decision: str = "approved", notes: str = "") -> dict:
    """One person signs one role. The last required signature approves the gate; any rejection rejects it."""
    if decision not in ("approved", "approved_with_conditions", "rejected"):
        raise ValueError(f"unknown decision {decision!r}")
    p = teams.persona(persona_id)
    g = next(x for x in GATES if x["gate"] == gate)
    with ledger.locked():
        rec = _record(records(), gate)
        if rec["status"] in APPROVED:
            raise ValueError(f"{gate} is already {rec['status']}")
        sigs = rec.get("signatures") or []
        if any(s["persona"] == persona_id for s in sigs):
            raise ValueError(f"{p['name']} has already signed {gate}")
        open_roles = [r for r in g["approvers"] if r not in {s["role"] for s in sigs}]
        roles = [r for r in open_roles if r in p["roles"]]
        if not roles:
            raise ValueError(f"{p['name']} ({p['title']}) holds none of the open approver roles for {gate}: {open_roles}")
        sig = {"role": roles[0], "persona": persona_id, "name": p["name"], "decision": decision, "notes": notes,
               "at": clock.now_iso()}
        if decision == "rejected":
            return approve(gate, [*sigs, sig], "rejected", notes)
        st = check_gate(gate)
        if not st["ready"]:
            failing = [c["check"] for c in st["checks"] if not c["ok"]]
            raise ValueError(f"{gate} is not ready to sign: " + ("previous gate not approved" if not st["previous_gate_approved"]
                                                                else "failing checks: " + "; ".join(failing)))
        sigs = [*sigs, sig]
        data = records()
        _record(data, gate)["signatures"] = sigs
        _save_records(data)
        ledger.append("gate_signed", item=_item(), gate=gate, release=release(), persona=persona_id, role=sig["role"],
                      decision=decision)
        if len(sigs) < len(g["approvers"]):
            return _record(records(), gate)
        final = "approved_with_conditions" if any(s["decision"] == "approved_with_conditions" for s in sigs) else "approved"
        return approve(gate, sigs, final, "; ".join(s["notes"] for s in sigs if s["notes"]))


def reset_gates(from_gate: str = "G0") -> None:
    with ledger.locked():
        data = records()
        for g in data["gates"][GATE_IDS.index(from_gate):]:
            g.update(status="pending", approvers=[], decided_at=None, evidence=[], notes="", checks=[], signatures=[],
                     manifest_sha256=None)
        _save_records(data)
        ledger.append("gates_reset", item=_item(), release=release(), from_gate=from_gate)


def sim_approvals(gate: str) -> list[dict]:
    """The first persona holding each required role (simulated sign-off)."""
    g = next(x for x in GATES if x["gate"] == gate)
    out = []
    for r in g["approvers"]:
        p = next(x for x in teams.holders(r) if x["id"] not in {a["persona"] for a in out})
        out.append({"role": r, "persona": p["id"], "name": p["name"], "decision": "approved", "notes": "", "at": clock.now_iso()})
    return out


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
            p = project_root() / rel
            files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file() and "__pycache__" not in x.parts
                                                    and x.name != ".DS_Store")
            for f in files:
                info = tarfile.TarInfo(str(f.relative_to(project_root())))
                data = f.read_bytes()
                info.size, info.mtime = len(data), 0
                tar.addfile(info, io.BytesIO(data))
    return hashlib.sha256(buf.getvalue()).hexdigest()


def bundle_release() -> dict:
    from shared.registry import manifests
    h = bundle_hash()
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_root(), capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = None
    rec = {"system": system_manifest()["id"], "release": release(), "bundle_hash": h, "bundled_paths": BUNDLE_PATHS,
           "git_commit": commit, "created_at": clock.now_iso(),
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
    th = _pyaml("evals/thresholds.yaml")["thresholds"]
    rep = {"claims": total, "submitted": n, "errors": errors, "route_agreement": round(route_ok / total, 3) if total else None,
           "amount_agreement": round(amt_ok / total, 3) if total else None,
           "payments_created": db.scalar(f"SELECT COUNT(*) FROM payments WHERE claim_id IN ({','.join('?' * len(ids))})", ids),
           "thresholds": {"route": th["routing_correctness"]["min"], "amount": th["amount_accuracy_within_1usd"]["min"]},
           "generated_at": clock.now_iso()}
    rep["passed"] = bool(total) and rep["route_agreement"] >= rep["thresholds"]["route"] and \
        rep["amount_agreement"] >= rep["thresholds"]["amount"] and rep["payments_created"] == 0 and not errors
    _write("shadow-report.json", rep)
    return {"ok": rep["passed"], "output": json.dumps(rep, indent=1) + f"\nroute agreement {rep['route_agreement']}, "
                                                                     f"amount agreement {rep['amount_agreement']} on {total} claims"}


def shadow_report(min_claims: int = 300) -> dict:
    """G5 shadow results from live traffic: every claim this release processed in shadow mode, compared
    with what the adjusters decided (here: the scenario ground truth). Needs at least min_claims."""
    rel = release()
    cs = db.query("SELECT * FROM claims WHERE historical = 0 AND release = ? AND mode = 'shadow'", [rel])
    route_ok = amt_ok = total = 0
    by_scenario: dict[str, dict] = {}
    disagreements = []
    for c in cs:
        gt = db.jload(c["ground_truth"], {})
        if c["status"] != "shadow_complete" or not gt.get("route"):
            continue
        total += 1
        r_ok = c["route"] == gt["route"]
        a_ok = gt.get("amount_check") is False or gt.get("amount") is None or abs((c["recommended_usd"] or 0) - gt["amount"]) <= 1
        route_ok += r_ok
        amt_ok += a_ok
        sc = by_scenario.setdefault(c["scenario"], {"claims": 0, "route_agree": 0, "amount_agree": 0})
        sc["claims"] += 1
        sc["route_agree"] += r_ok
        sc["amount_agree"] += a_ok
        if not (r_ok and a_ok):
            disagreements.append({"claim_id": c["claim_id"], "scenario": c["scenario"], "expected_route": gt["route"],
                                  "agent_route": c["route"], "expected_usd": gt.get("amount"), "agent_usd": c["recommended_usd"]})
    ids = [c["claim_id"] for c in cs]
    payments = db.scalar(f"SELECT COUNT(*) FROM payments WHERE claim_id IN ({','.join('?' * len(ids))})", ids) if ids else 0
    th = _pyaml("evals/thresholds.yaml")["thresholds"]
    days = sorted(c["submitted_at"][:10] for c in cs if c["submitted_at"])
    rep = {"release": rel, "claims": total, "submitted": len(cs), "min_claims": min_claims,
           "errors": sum(c["status"] == "error" for c in cs),
           "route_agreement": round(route_ok / total, 3) if total else None,
           "amount_agreement": round(amt_ok / total, 3) if total else None, "payments_created": payments,
           "thresholds": {"route": th["routing_correctness"]["min"], "amount": th["amount_accuracy_within_1usd"]["min"]},
           "window": {"from": days[0], "to": days[-1]} if days else None, "by_scenario": by_scenario,
           "disagreements": disagreements, "generated_at": clock.now_iso()}
    rep["passed"] = total >= min_claims and rep["route_agreement"] >= rep["thresholds"]["route"] and \
        rep["amount_agreement"] >= rep["thresholds"]["amount"] and payments == 0 and not rep["errors"]
    _write("shadow-report.json", rep)
    worst = sorted(((v["route_agree"] / v["claims"], k) for k, v in by_scenario.items()))[:1]
    return {"ok": rep["passed"], "output": f"route agreement {rep['route_agreement']}, amount agreement "
                                           f"{rep['amount_agreement']} on {total} claims (min {min_claims})"
                                           + (f"; lowest: {worst[0][1]} {worst[0][0]:.0%}" if worst and worst[0][0] < 1 else "")}


def replay_shadow(n: int = 330) -> dict:
    """Emergency path to G5 evidence: replay the scenario mix of the last n production claims through this release
    in shadow mode, without waiting a week for new traffic. Production flags are restored afterwards."""
    from sim import traffic
    recent = db.query("SELECT scenario FROM claims WHERE historical = 0 AND mode IN ('full', 'assisted') "
                      "ORDER BY package_at DESC LIMIT ?", [n])
    if not recent:
        return {"ok": False, "output": "no production claims to replay"}
    weights: dict[str, int] = {}
    for r in recent:
        weights[r["scenario"]] = weights.get(r["scenario"], 0) + 1
    prev = {k: get_runtime()[k] for k in ("mode", "straight_through_enabled", "payments_enabled")}
    set_phase("shadow")
    try:
        r = traffic.run_now(n, weights, seed=n)
    finally:
        for k, v in prev.items():
            set_runtime(k, v, by="shadow-replay-restore")
    return {"ok": not r["errors"], "output": f"replayed {r['claims']} claims of the last {len(recent)} claims' mix in shadow, "
                                             f"{len(r['errors'])} errors"}


def _window(from_day: float, to_day: float) -> tuple[str, str]:
    return clock.at_day(from_day, 0).isoformat(timespec="seconds"), clock.at_day(to_day, 0).isoformat(timespec="seconds")


def hypercare_report(from_day: float, to_day: float) -> dict:
    """G6 evidence over a window of business days in full mode: SLOs, controls, incidents and value KPIs."""
    from shared import incidents
    lo, hi = _window(from_day, to_day)
    cs = _claims_between(lo, hi, "AND mode = 'full'")
    lat = sorted(c["package_latency_s"] for c in cs if c["package_latency_s"] is not None)
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))] if lat else None
    errors = sum(c["status"] == "error" for c in cs)
    cost = db.one("SELECT SUM(cost_usd) c, COUNT(DISTINCT claim_id) n FROM llm_calls WHERE ts >= ? AND ts < ? "
                  "AND claim_id IS NOT NULL", [lo, hi])
    cpc = round(cost["c"] / cost["n"], 4) if cost["n"] else None
    budget = get_runtime()["max_cost_per_claim_usd"]
    turn = {lvl: db.query("SELECT queued_at, decided_at FROM approvals WHERE level = ? AND decided_at >= ? AND decided_at < ? "
                          "AND queued_at IS NOT NULL", [lvl, lo, hi]) for lvl in ("L1", "L2")}
    hours = {lvl: round(sum((datetime.fromisoformat(a["decided_at"]) - datetime.fromisoformat(a["queued_at"])).total_seconds()
                            for a in xs) / 3600 / len(xs), 2) if xs else None for lvl, xs in turn.items()}
    slos = [{"id": "slo:decision-latency", "target": "p95 <= 10 min", "value": p95, "met": p95 is None or p95 <= 600},
            {"id": "slo:availability", "target": "99.5%", "value": round(1 - errors / len(cs), 4) if cs else None,
             "met": not cs or 1 - errors / len(cs) >= 0.995},
            {"id": "slo:cost-per-claim", "target": f"<= USD {budget}", "value": cpc, "met": cpc is None or cpc <= budget},
            {"id": "slo:l1-turnaround", "target": "1 business day", "value": hours["L1"], "met": hours["L1"] is None or hours["L1"] <= 8},
            {"id": "slo:l2-turnaround", "target": "1 business day after L1", "value": hours["L2"],
             "met": hours["L2"] is None or hours["L2"] <= 8}]
    unverified = db.scalar("SELECT COUNT(*) FROM payments WHERE token_verified != 1 AND created_at >= ? AND created_at < ?", [lo, hi]) or 0
    inc = [i for i in incidents.incidents() if lo <= i["opened"] < hi]
    rep = {"release": release(), "mode": "full", "claims": len(cs), "window": {"from_day": from_day, "to_day": to_day},
           "simulated_days": to_day - from_day, "errors": errors, "slos": slos,
           "controls": {"payments_without_valid_token": unverified},
           "alerts": [{"name": i["alert"], "severity": i["severity"], "incident": i["id"],
                       "firing": i["status"] in ("open", "acknowledged")} for i in inc],
           "incidents": [{k: i.get(k) for k in ("id", "alert", "severity", "team", "status", "ack_by", "rca")} for i in inc],
           "kpis": metrics.kpis(), "routing_mix": metrics.routing_mix(), "generated_at": clock.now_iso()}
    _write("hypercare-report.json", rep)
    ok = all(x["met"] for x in slos) and not unverified and not any(a["firing"] and a["severity"] in ("sev1", "sev2") for a in rep["alerts"])
    return {"ok": ok, "output": f"days {from_day:g}-{to_day:g}: {len(cs)} claims in full mode, {len(inc)} incidents, "
                                f"{unverified} payments without a valid token, SLOs met={all(x['met'] for x in slos)}"}


def _claims_between(lo: str, hi: str, where: str = "") -> list[dict]:
    return db.query(f"SELECT * FROM claims WHERE historical = 0 AND package_at >= ? AND package_at < ? {where}", [lo, hi])


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
    drills["kill_switch"] = {"passed": ok, "at": clock.now_iso(),
                             "summary": f"new claim -> {c['status']}; payment attempt {'blocked' if blocked else 'ALLOWED'}"}
    _write("drills.json", drills)
    return {"ok": ok, "output": drills["kill_switch"]["summary"]}


def rollback_drill() -> dict:
    drills = _read("drills.json") or {}
    rec = _read("release-record.json")
    steps = [l.strip() for l in _text("deploy/rollback.md").splitlines() if re.match(r"^\d\.", l.strip())]
    ok = bool(rec) and bool(drills.get("kill_switch", {}).get("passed")) and len(steps) >= 4
    drills["rollback"] = {"passed": ok, "at": clock.now_iso(), "steps": steps,
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
           "routing_mix": metrics.routing_mix(), "generated_at": clock.now_iso()}
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
    lines = [f"# G7 periodic review: release {release()} ({clock.now().strftime('%B %Y')})", "",
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
    p = project_root() / "docs" / "reviews" / f"G7-review-{release()}.md"
    p.write_text("\n".join(lines) + "\n")
    return {"ok": True, "output": f"wrote {p.relative_to(project_root())}"}


ACTIONS: dict[str, tuple[str, Callable]] = {
    "validate_manifests": ("G2 - validate system/agent manifests", lambda **_: _run(["scripts/validate_manifests.py"])),
    "unit_tests": ("Build/CI - unit + routing tests", lambda **_: _run(["scripts/run_tests.py", release()])),
    "evals": ("G3/G4 - run eval suites (isolated data)", lambda **_: _run(["evals/run_evals.py", release()])),
    "traceability": ("G3 - build traceability matrix", lambda **_: _run(["scripts/build_traceability.py", release()])),
    "perf_test": ("G3 - performance test at 3x peak", lambda **_: _run(["evals/perf_test.py", release()])),
    "bundle_release": ("G5 - bundle release + hash", lambda **_: bundle_release()),
    "shadow_run": ("G5 - shadow-mode run on new traffic", lambda n=20, **_: shadow_run(int(n))),
    "shadow_report": ("G5 - shadow report from the release's live traffic", lambda n=300, **_: shadow_report(int(n))),
    "replay_shadow": ("G5 - replay recent traffic in shadow (emergency changes)", lambda n=330, **_: replay_shadow(int(n))),
    "hypercare_report": ("G6 - hypercare report over a window of business days",
                         lambda from_day=0, to_day=30, **_: hypercare_report(float(from_day), float(to_day))),
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
