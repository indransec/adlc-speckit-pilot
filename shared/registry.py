"""Manifest-driven agent registry.

The orchestrator never imports sub-agents directly: it asks the registry for the agent
pinned in system.yaml (id@version), which loads agents/<dir>/agent.yaml, the system
prompt from prompts/system.md and the implementation from src/agent.py. Delegation is
checked against system.yaml topology (allowed edges, max depth) - threat T7.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from shared import db
from shared.audit import audit, control_event
from shared.config import ROOT, system_manifest


class DelegationError(RuntimeError):
    pass


@lru_cache
def manifests() -> dict[str, dict]:
    out = {}
    for p in sorted((ROOT / "agents").glob("*/agent.yaml")):
        m = yaml.safe_load(p.read_text())
        m["_dir"] = p.parent
        out[m["id"]] = m
    return out


def agent_version_of(agent_id: str) -> str | None:
    m = manifests().get(agent_id)
    return m["version"] if m else None


def manifest(agent_id: str) -> dict:
    m = manifests().get(agent_id)
    if not m:
        raise KeyError(agent_id)
    pinned = {r["ref"].split("@")[0]: r["ref"].split("@")[1] for r in system_manifest()["topology"]["agents"]}
    if pinned.get(agent_id) != m["version"]:
        raise DelegationError(f"{agent_id} version {m['version']} is not the pinned {pinned.get(agent_id)}")
    return m


@lru_cache
def system_prompt(agent_id: str) -> str:
    m = manifest(agent_id)
    return (m["_dir"] / m["prompts"]["system"]).read_text()


def _slug(agent_dir: Path) -> str:
    return agent_dir.name.replace("-", "_")


def load_agent_package(agent_dir_name: str) -> str:
    """Load agents/<dir>/src as package adlc_agents.<slug>; returns the package name."""
    if "adlc_agents" not in sys.modules:
        ns = types.ModuleType("adlc_agents")
        ns.__path__ = []
        sys.modules["adlc_agents"] = ns
    src = ROOT / "agents" / agent_dir_name / "src"
    name = f"adlc_agents.{_slug(src.parent)}"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, src / "__init__.py", submodule_search_locations=[str(src)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return name


def agent_module(agent_id: str):
    m = manifest(agent_id)
    pkg = load_agent_package(m["_dir"].name)
    return importlib.import_module(f"{pkg}.agent")


@dataclass
class AgentContext:
    manifest: dict
    claim_id: str
    run_no: int
    runs: list = field(default_factory=list)          # one AgentRun per create_react_agent task
    bindings: dict = field(default_factory=dict)      # claim-scoped tool arguments set by the agent
    known_pii: dict = field(default_factory=dict)     # claimant contact data to redact from tool results
    documents: dict = field(default_factory=dict)     # guarded document text, by doc_id
    doc_types: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.manifest["name"]

    @property
    def agent_id(self) -> str:
        return self.manifest["id"]

    def tool(self, tool_id: str, op: str, invoked_by: str = "code", **kwargs):
        from shared.tool_gateway import call_tool
        claim_id = kwargs.pop("claim_id", self.claim_id)
        return call_tool(self.name, tool_id, op, claim_id=claim_id, invoked_by=invoked_by, **kwargs)

    def agent(self, task: str, schema, user_content: str, fallback, *, mock_plan: list | None = None,
              bindings: dict | None = None, model_override: str | None = None):
        """Run a task as a create_react_agent with this agent's attached tools (shared/agent_runtime.py)."""
        from shared.agent_runtime import run_agent
        return run_agent(self, task=task, schema=schema, user_content=user_content, fallback=fallback,
                         mock_plan=mock_plan, bindings=bindings, model_override=model_override)

    @property
    def degraded(self) -> bool:
        return any(r.degraded for r in self.runs)

    def audit(self, event: str, payload: dict | None = None):
        audit(self.claim_id, event, payload, actor=self.name, agent_id=self.agent_id,
              agent_version=self.manifest["version"], run_no=self.run_no)


def llm_usage_since(claim_id: str, run_no: int, agent_id: str, after_id: int) -> dict:
    """Tokens and cost recorded by the telemetry sink for one agent invocation."""
    return db.one("SELECT COUNT(*) calls, COALESCE(SUM(prompt_tokens), 0) prompt_tokens, "
                  "COALESCE(SUM(completion_tokens), 0) completion_tokens, ROUND(COALESCE(SUM(cost_usd), 0), 6) cost_usd "
                  "FROM llm_calls WHERE id > ? AND claim_id = ? AND run_no = ? AND agent_id = ?",
                  [after_id, claim_id, run_no, agent_id])


def check_delegation(caller: str, target: str, depth: int) -> None:
    topo = system_manifest()["topology"]
    allowed = {t for d in topo.get("delegation", []) if d["from"] == caller for t in d["to"]}
    if target not in allowed:
        control_event(None, "delegation", "denied", f"{caller} -> {target} not in topology")
        raise DelegationError(f"{caller} may not delegate to {target}")
    if depth > topo.get("max_delegation_depth", 2):
        control_event(None, "delegation", "denied", f"depth {depth} > max")
        raise DelegationError(f"delegation depth {depth} exceeds max")


def invoke(agent_id: str, entry: str = "run", *, claim_id: str, run_no: int, caller: str = "AGT-TRV-ORCH",
           depth: int = 1, **inputs) -> dict:
    from shared.agent_runtime import tool_call_summary
    from shared.telemetry import agent_span
    check_delegation(caller, agent_id, depth)
    m = manifest(agent_id)
    ctx = AgentContext(m, claim_id, run_no)
    last_id = db.scalar("SELECT COALESCE(MAX(id), 0) FROM llm_calls")
    with agent_span(agent_id, m["version"], claim_id, f"agent {m['name']}.{entry}", run_no=run_no,
                    **{"adlc.agent.autonomy": m.get("autonomy")}) as span:
        out = getattr(agent_module(agent_id), entry)(ctx, **inputs)
    usage = llm_usage_since(claim_id, run_no, agent_id, last_id)
    out = dict(out or {})
    out["_meta"] = {"agent_id": agent_id, "agent_version": m["version"], "entry": entry,
                    "framework": "langgraph.prebuilt.create_react_agent" if ctx.runs else "deterministic",
                    "llm_mode": ctx.runs[0].mode if ctx.runs else "none",
                    "models": sorted({r.model for r in ctx.runs}), "degraded": ctx.degraded,
                    "errors": [f"{r.task}: {r.error}" for r in ctx.runs if r.error],
                    "llm_calls": usage["calls"], "tokens": usage["prompt_tokens"] + usage["completion_tokens"],
                    "cost_usd": usage["cost_usd"], "tool_calls": tool_call_summary(ctx.runs),
                    "duration_ms": round((span.end_time - span.start_time) / 1e6, 1)}
    key = agent_id if entry == "run" else f"{agent_id}:{entry}"
    db.upsert("agent_outputs", {"claim_id": claim_id, "run_no": run_no, "agent_id": key,
                                "agent_version": m["version"], "output": db.jdump(out), "created_at": db.now()})
    audit(claim_id, f"agent_completed:{m['name']}.{entry}", {"degraded": ctx.degraded,
                                                            "cost_usd": out["_meta"]["cost_usd"]},
          actor=caller, agent_id=agent_id, agent_version=m["version"], run_no=run_no,
          duration_ms=out["_meta"]["duration_ms"])
    return out
