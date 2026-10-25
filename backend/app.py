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
from shared import db, lifecycle, metrics, object_store  # noqa: E402
from shared.config import release, system_manifest  # noqa: E402
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
    return {"status": "ok", "release": release()}


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
                    "status, degraded FROM llm_calls WHERE claim_id = ? ORDER BY id", [claim_id])


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
                    "latency_ms, status, degraded FROM llm_calls ORDER BY id DESC LIMIT ?", [limit])


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
    p = ROOT / "docs" / "reviews" / f"G7-review-{release()}.md"
    return {"markdown": p.read_text() if p.exists() else None}
