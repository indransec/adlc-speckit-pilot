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
import time
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
    llm_meta: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.manifest["name"]

    @property
    def agent_id(self) -> str:
        return self.manifest["id"]

    def tool(self, tool_id: str, op: str, **kwargs):
        from shared.tool_gateway import call_tool
        claim_id = kwargs.pop("claim_id", self.claim_id)
        return call_tool(self.name, tool_id, op, claim_id=claim_id, **kwargs)

    def llm(self, task: str, schema, user_content: str, fallback, model_override: str | None = None):
        from shared.llm import structured_call
        value, meta = structured_call(manifest=self.manifest, claim_id=self.claim_id, task=task, schema=schema,
                                      system_prompt=system_prompt(self.agent_id), user_content=user_content,
                                      fallback=fallback, model_override=model_override)
        self.llm_meta.append(meta)
        return value, meta

    @property
    def degraded(self) -> bool:
        return any(m.degraded for m in self.llm_meta)

    def audit(self, event: str, payload: dict | None = None):
        audit(self.claim_id, event, payload, actor=self.name, agent_id=self.agent_id,
              agent_version=self.manifest["version"], run_no=self.run_no)


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
    from shared.telemetry import agent_span
    check_delegation(caller, agent_id, depth)
    m = manifest(agent_id)
    ctx = AgentContext(m, claim_id, run_no)
    t0 = time.perf_counter()
    with agent_span(agent_id, m["version"], claim_id, f"agent {m['name']}.{entry}",
                    **{"adlc.agent.autonomy": m.get("autonomy")}):
        out = getattr(agent_module(agent_id), entry)(ctx, **inputs)
    out = dict(out or {})
    out["_meta"] = {"agent_id": agent_id, "agent_version": m["version"], "entry": entry,
                    "llm_mode": ctx.llm_meta[0].mode if ctx.llm_meta else "none",
                    "models": sorted({x.model for x in ctx.llm_meta}), "degraded": ctx.degraded,
                    "cost_usd": round(sum(x.cost_usd for x in ctx.llm_meta), 6),
                    "duration_ms": round((time.perf_counter() - t0) * 1000, 1)}
    key = agent_id if entry == "run" else f"{agent_id}:{entry}"
    db.upsert("agent_outputs", {"claim_id": claim_id, "run_no": run_no, "agent_id": key,
                                "agent_version": m["version"], "output": db.jdump(out), "created_at": db.now()})
    audit(claim_id, f"agent_completed:{m['name']}.{entry}", {"degraded": ctx.degraded,
                                                            "cost_usd": out["_meta"]["cost_usd"]},
          actor=caller, agent_id=agent_id, agent_version=m["version"], run_no=run_no,
          duration_ms=out["_meta"]["duration_ms"])
    return out
