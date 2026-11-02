"""FastAPI backend (backend-for-frontend) for the Travel Claims Assistant.

  uvicorn backend.app:app --reload        (PYTHONPATH must include . and agents/orchestrator/src)

OpenAPI docs at /docs. The Streamlit UI only talks to this API.
"""
from __future__ import annotations

import os
import random
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "agents" / "orchestrator" / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from backend import service  # noqa: E402
from shared import (board, changes, clock, db, faults, incidents, ledger, lifecycle, metrics, object_store,  # noqa: E402
                    releases, teams, usecase)
from shared.config import adlc_governed, intake_enabled, is_sim, project_root, release, system_manifest  # noqa: E402
from shared.runtime import get_runtime, set_runtime  # noqa: E402

app = FastAPI(title="Travel Claims Assistant API", version=release(),
              description="Agentic travel claims backend (ADLC pilot). LangGraph orchestrator, OpenRouter gateway.")


@app.on_event("startup")
def _startup():
    service.bootstrap()


def _err(e: Exception):
    raise HTTPException(status_code=409 if isinstance(e, service.ServiceError) else 400, detail=str(e))


# ------------------------------------------------------------------------------------------- system
class RuntimeUpdate(BaseModel):
    key: str
    value: Any
    by: str = "operator"


@app.get("/health")
def health():
    return {"status": "ok", "release": release(), "run": os.environ.get("SIM_RUN_ID")}


@app.get("/system")
def system():
    m = system_manifest()
    from shared.registry import manifests
    return {"id": m["id"], "name": m["name"], "release": m["release"], "risk_tier": m["risk_tier"], "scope": m["scope"],
            "routing": m["routing"], "hitl_points": m["hitl_points"], "runtime": get_runtime(),
            "agents": [{"id": a["id"], "name": a["name"], "version": a["version"], "autonomy": a["autonomy"],
                        "model": a["model"]["name"], "tools": [t["id"] for t in a.get("tools") or []],
                        "requirements": a["requirements"]} for a in manifests().values()],
            "phoenix_url": os.environ.get("PHOENIX_UI_URL", "http://localhost:6006")}


@app.put("/runtime")
def update_runtime(u: RuntimeUpdate):
    try:
        return set_runtime(u.key, u.value, by=u.by)
    except (KeyError, ValueError) as e:
        _err(e)


@app.get("/graph")
def graph_diagram():
    return {"mermaid": service.graph().get_graph().draw_mermaid()}


# ------------------------------------------------------------------------------------------- claims
class Generate(BaseModel):
    scenario: Optional[str] = None
    count: int = 1
    seed: Optional[int] = None


class Decision(BaseModel):
    user_id: str
    decision: str
    amount_usd: Optional[float] = None
    notes: str = ""


@app.get("/scenarios")
def scenarios():
    from synthetic.scenarios import SCENARIOS
    import datetime
    return [{"key": k, "title": f(datetime.date(2026, 9, 1), random.Random(0))["title"]} for k, f in SCENARIOS.items()]


@app.get("/claims")
def claims(status: Optional[str] = None):
    return service.list_claims(status)


@app.post("/claims/generate")
def generate(g: Generate):
    from synthetic.generate import add_traffic
    from synthetic.scenarios import SCENARIOS
    if g.scenario and g.scenario not in SCENARIOS:
        raise HTTPException(400, f"unknown scenario {g.scenario}")
    return {"claim_ids": add_traffic(max(1, min(g.count, 100)), g.seed, {g.scenario: 1} if g.scenario else None)}


@app.get("/claims/{claim_id}")
def claim(claim_id: str):
    try:
        return service.summary(claim_id)
    except service.ServiceError as e:
        raise HTTPException(404, str(e))


@app.post("/claims/{claim_id}/process")
def process(claim_id: str, background: bool = True):
    try:
        if background:
            service.process_async(claim_id)
            return {"claim_id": claim_id, "status": "submitted"}
        return service.process(claim_id)
    except Exception as e:
        _err(e)


@app.post("/claims/process-all")
def process_all(status: str = "new", limit: int = 50):
    ids = [c["claim_id"] for c in service.list_claims(status)][:limit]
    started = []
    for cid in ids:
        try:
            service.process_async(cid)
            started.append(cid)
        except service.ServiceError:
            pass
    return {"submitted": started}


@app.post("/claims/{claim_id}/decision")
def decide(claim_id: str, d: Decision):
    try:
        return service.decide(claim_id, d.user_id, d.decision, d.amount_usd, d.notes)
    except Exception as e:
        _err(e)


@app.post("/claims/{claim_id}/documents/upload-missing")
def upload_missing(claim_id: str):
    try:
        return service.upload_missing_documents(claim_id)
    except Exception as e:
        _err(e)


@app.get("/claims/{claim_id}/documents/{doc_id}")
def document(claim_id: str, doc_id: str):
    d = db.one("SELECT object_key, filename FROM documents WHERE claim_id = ? AND doc_id = ?", [claim_id, doc_id])
    if not d:
        raise HTTPException(404, "document not found")
    return Response(object_store.get(d["object_key"]), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{d["filename"]}"'})


@app.get("/claims/{claim_id}/documents/{doc_id}/text")
def document_text(claim_id: str, doc_id: str):
    from tools.document_store.impl import read_text
    return {"doc_id": doc_id, "text": read_text(doc_id)}


@app.get("/claims/{claim_id}/llm-calls")
def llm_calls(claim_id: str):
    return db.query("SELECT ts, agent_id, task, model, mode, prompt_tokens, completion_tokens, cost_usd, latency_ms, "
                    "status, trace_id FROM llm_calls WHERE claim_id = ? ORDER BY id", [claim_id])


@app.get("/claims/{claim_id}/tool-calls")
def tool_calls(claim_id: str):
    return db.query("SELECT ts, caller, tool_id, operation, access, invoked_by, status, latency_ms FROM tool_calls "
                    "WHERE claim_id = ? ORDER BY id", [claim_id])


# ------------------------------------------------------------------------------------------- workbench
@app.get("/queues")
def queues():
    return service.queues()


@app.get("/users")
def users():
    return db.query("SELECT * FROM users ORDER BY role, user_id")


@app.post("/jobs/document-chasing")
def chase(days: int = 3):
    return service.chase_documents(days)


@app.post("/simulate/adjusters")
def simulate_adjusters(limit: int = 50):
    from synthetic import adjuster_bot
    ids = [q["claim_id"] for q in service.queues()][:limit]
    return {"decisions": adjuster_bot.drain(ids)}


# ------------------------------------------------------------------------------------------- observability
@app.get("/metrics/dashboard")
def dashboard():
    return metrics.dashboard()


@app.get("/metrics/llm-calls")
def recent_llm_calls(limit: int = 100):
    return db.query("SELECT ts, claim_id, agent_id, task, model, mode, prompt_tokens, completion_tokens, cost_usd, "
                    "latency_ms, status, trace_id FROM llm_calls ORDER BY id DESC LIMIT ?", [limit])


@app.get("/metrics/phoenix-cost")
def phoenix_cost():
    """Token and cost totals as computed by Phoenix from the exported spans (null if Phoenix is down)."""
    from shared.telemetry import phoenix_cost_summary
    return phoenix_cost_summary()


@app.get("/audit")
def audit_log(limit: int = 200, claim_id: Optional[str] = None):
    if claim_id:
        return db.query("SELECT * FROM audit_log WHERE claim_id = ? ORDER BY id DESC LIMIT ?", [claim_id, limit])
    return db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", [limit])


@app.get("/events/guardrails")
def guardrail_events(limit: int = 100):
    return db.query("SELECT * FROM guardrail_events ORDER BY id DESC LIMIT ?", [limit])


@app.get("/events/controls")
def control_events(limit: int = 100):
    return db.query("SELECT * FROM control_events ORDER BY id DESC LIMIT ?", [limit])


# ------------------------------------------------------------------------------------------- lifecycle
class Approval(BaseModel):
    approvals: list[dict] = []
    decision: str = "approved"
    notes: str = ""
    simulated: bool = False


class ActionParams(BaseModel):
    params: dict = {}


@app.get("/lifecycle/gates")
def gates():
    return lifecycle.all_gates()


@app.get("/lifecycle/gates/{gate}")
def gate(gate: str):
    return lifecycle.check_gate(gate)


@app.post("/lifecycle/gates/{gate}/approve")
def approve_gate(gate: str, a: Approval):
    try:
        approvals = lifecycle.sim_approvals(gate) if a.simulated else a.approvals
        return lifecycle.approve(gate, approvals, a.decision, a.notes)
    except ValueError as e:
        raise HTTPException(409, str(e))


class Signature(BaseModel):
    persona: str
    decision: str = "approved"
    notes: str = ""


@app.post("/lifecycle/gates/{gate}/sign")
def sign_gate(gate: str, s: Signature):
    """One persona signs one approver role; the last required signature approves the gate."""
    try:
        return lifecycle.sign(gate, s.persona, s.decision, s.notes)
    except (ValueError, KeyError) as e:
        raise HTTPException(409, str(e))


@app.post("/lifecycle/gates/reset")
def reset(frm: str = "G0"):
    lifecycle.reset_gates(frm)
    return lifecycle.all_gates()


@app.get("/lifecycle/actions")
def actions():
    return [{"name": k, "description": d} for k, (d, _) in lifecycle.ACTIONS.items()]


@app.post("/lifecycle/actions/{name}")
def run_action(name: str, p: ActionParams):
    try:
        return {"job_id": lifecycle.run_action_async(name, **p.params)}
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/lifecycle/jobs")
def jobs(limit: int = 20):
    return db.query("SELECT * FROM jobs ORDER BY started_at DESC LIMIT ?", [limit])


@app.get("/lifecycle/jobs/{job_id}")
def job(job_id: str):
    j = db.one("SELECT * FROM jobs WHERE job_id = ?", [job_id])
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.get("/lifecycle/evidence")
def evidence_index():
    d = lifecycle.ev_dir()
    return sorted(p.name for p in d.glob("*.json")) if d.exists() else []


@app.get("/lifecycle/evidence/{name}")
def evidence(name: str):
    p = lifecycle.ev_dir() / Path(name).name
    if not p.exists() or p.suffix != ".json":
        raise HTTPException(404, "evidence not found")
    import json
    return json.loads(p.read_text())


@app.get("/lifecycle/review")
def g7_review():
    p = project_root() / "docs" / "reviews" / f"G7-review-{release()}.md"
    return {"markdown": p.read_text() if p.exists() else None}


# ------------------------------------------------------------------------------------------- simulation
def _require_sim():
    if not is_sim():
        raise HTTPException(409, "only inside a simulation run: make sim-new, then make sim-api")


def _require_intake():
    if not intake_enabled():
        raise HTTPException(409, "the intake form writes only into a simulation run or a governed repo (adlc_governed)")


class ClockControl(BaseModel):
    action: str                    # pause | resume | rate | skip_days | skip_to_day
    value: Optional[float] = None


@app.get("/clock")
def get_clock():
    return {**clock.state(), "governed": adlc_governed(), "intake_enabled": intake_enabled()}


@app.post("/clock")
def control_clock(c: ClockControl):
    _require_sim()
    try:
        {"pause": lambda: clock.pause(), "resume": lambda: clock.resume(), "rate": lambda: clock.set_rate(c.value),
         "skip_days": lambda: clock.advance(c.value * clock.DAY), "skip_to_day": lambda: clock.skip_to_day(c.value)}[c.action]()
    except KeyError:
        raise HTTPException(400, f"unknown clock action {c.action}")
    except (ValueError, RuntimeError, TypeError) as e:
        raise HTTPException(409, str(e))
    return clock.state()


@app.get("/personas")
def personas():
    return teams.personas()


@app.get("/teams")
def team_list():
    return teams.teams()


class ByPersona(BaseModel):
    by: str


class UseCaseSave(BaseModel):
    by: str
    record: dict


class G1Save(BaseModel):
    by: str
    data: dict


def _uc(fn, *a):
    try:
        return fn(*a)
    except (ValueError, KeyError) as e:  # pydantic ValidationError is a ValueError
        raise HTTPException(409, str(e))


@app.get("/usecases/current")
def usecase_current():
    return usecase.summary()


@app.get("/usecases/template")
def usecase_template():
    return usecase.template()


@app.post("/usecases")
def usecase_save(u: UseCaseSave):
    _require_intake()
    return _uc(usecase.save, u.record, u.by)


@app.post("/usecases/{uc}/submit")
def usecase_submit(uc: str, b: ByPersona):
    _require_intake()
    return _uc(usecase.submit, uc, b.by)


@app.post("/usecases/{uc}/classification/confirm")
def usecase_confirm_classification(uc: str, b: ByPersona):
    _require_intake()
    return _uc(usecase.confirm_classification, uc, b.by)


@app.get("/usecases/{uc}/g1/template")
def usecase_g1_template(uc: str):
    return usecase.g1_template(uc)


@app.post("/usecases/{uc}/g1")
def usecase_g1(uc: str, g: G1Save):
    _require_intake()
    return _uc(usecase.save_g1, uc, g.data, g.by)


@app.get("/board")
def get_board():
    return board.board()


@app.get("/board/items/{item_id}")
def board_item(item_id: str):
    try:
        return board.item(item_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/ledger")
def ledger_events(item: Optional[str] = None, limit: int = 200):
    evs = ledger.for_item(item) if item else ledger.events()
    return evs[-limit:]


@app.get("/ledger/verify")
def ledger_verify():
    return ledger.verify()


@app.get("/ledger/manifests/{digest}")
def ledger_manifest(digest: str):
    try:
        return ledger.manifest(digest)
    except ledger.LedgerError as e:
        raise HTTPException(404, str(e))


@app.get("/ledger/artifacts/{digest}")
def ledger_artifact(digest: str):
    try:
        return Response(ledger.get_bytes(digest), media_type="application/octet-stream")
    except ledger.LedgerError as e:
        raise HTTPException(404, str(e))


@app.get("/releases")
def release_list():
    return releases.history()


@app.get("/changes")
def change_list(release: Optional[str] = None):
    return changes.crs(release)


@app.post("/changes/{cr_id}/decide")
def change_decide(cr_id: str, s: Signature):
    """A CAB member (or other named approver) decides a change request."""
    _require_sim()
    try:
        return changes.decide(cr_id, s.persona, s.decision, s.notes)
    except (ValueError, KeyError) as e:
        raise HTTPException(409, str(e))


_metrics_registry = None


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics():
    """Alert inputs over business-time windows (shared/prom.py), scraped by Prometheus in simulation runs."""
    global _metrics_registry
    from prometheus_client import CollectorRegistry, generate_latest
    from prometheus_client.exposition import CONTENT_TYPE_LATEST
    from shared.prom import AdlcCollector
    if _metrics_registry is None:
        _metrics_registry = CollectorRegistry()
        _metrics_registry.register(AdlcCollector())
    return Response(generate_latest(_metrics_registry), media_type=CONTENT_TYPE_LATEST)


# ------------------------------------------------------------------------------------------- incidents
@app.post("/webhooks/alertmanager", include_in_schema=False)
def alertmanager_webhook(payload: dict):
    """Alertmanager posts firing and resolved alerts here (deploy/sim): incident cards and automatic actions."""
    _require_sim()
    return {"changed": [i["id"] for i in incidents.from_alertmanager(payload)]}


@app.get("/incidents")
def incident_list():
    return incidents.incidents()


class IncidentUpdate(BaseModel):
    persona: str
    text: str = ""


@app.post("/incidents/{inc_id}/{action}")
def incident_update(inc_id: str, action: str, u: IncidentUpdate):
    _require_sim()
    fn = {"ack": lambda: incidents.acknowledge(inc_id, u.persona, u.text), "note": lambda: incidents.note(inc_id, u.persona, u.text),
          "close": lambda: incidents.close(inc_id, u.persona, u.text)}.get(action)
    if fn is None:
        raise HTTPException(404, f"unknown incident action {action}")
    try:
        return fn()
    except (ValueError, KeyError) as e:
        raise HTTPException(409, str(e))


@app.get("/faults")
def fault_list():
    active = {f["id"] for f in faults.active()}
    return [{"id": k, **v, "active": k in active} for k, v in faults.catalog().items()]


class FaultToggle(BaseModel):
    on: bool
    by: str = "simulator"
    note: str = ""


@app.post("/faults/{fault_id}")
def fault_toggle(fault_id: str, t: FaultToggle):
    _require_sim()
    try:
        faults.inject(fault_id, by=t.by, note=t.note) if t.on else faults.clear(fault_id, by=t.by, note=t.note)
    except (ValueError, KeyError) as e:
        raise HTTPException(409, str(e))
    return fault_list()


@app.get("/evidence-pack")
def evidence_pack(release: Optional[str] = None, gate: Optional[str] = None):
    """Zip of manifests, frozen artifacts and the hash-chained ledger for an auditor (optionally one release / gate)."""
    name = "-".join(x for x in ("evidence", release, gate) if x) + ".zip"
    return Response(ledger.evidence_pack(release, gate), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/simulation/report")
def simulation_report(refresh: bool = False):
    """The end-of-run detection matrix (sim/report.py); refresh=true rebuilds it from the ledger now."""
    import json as _json
    from shared.config import evidence_root
    p = evidence_root() / "e2e-report.json"
    if refresh or not p.exists():
        from sim import report
        report.write()
    return _json.loads(p.read_text())


@app.get("/simulation/story")
def simulation_story():
    """Story progress: steps in sim/story_e2e.yaml and what the runner has done in this run."""
    import json as _json
    from shared.config import evidence_root
    from sim.story import Story, load
    steps = load()["steps"]
    p = evidence_root() / "story-log.jsonl"
    log = [_json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    done = {r["key"] for r in log if r.get("done") and r.get("key")}
    return {"total": len(steps), "done": sum(Story.key(s) in done for s in steps),
            "steps": [{"n": i + 1, "day": s.get("day"), "do": s["do"], "what": s.get("gate") or s.get("version") or s.get("alert")
                       or s.get("fault") or s.get("kind") or s.get("phase") or "", "done": Story.key(s) in done}
                      for i, s in enumerate(steps)],
            "last": [r for r in log if not r.get("done")][-12:]}
