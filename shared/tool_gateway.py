"""Tool gateway: enforces docs/design/tool-permission-matrix.yaml on every tool call (threat T3).

Each tool implementation (tools/<name>/impl.py) declares OPERATIONS = {op: access}. A call
is allowed only if the calling agent is listed for the tool AND the op's access level is
granted to that tool in the matrix.
"""
from __future__ import annotations

import importlib
import inspect
from functools import lru_cache
from pathlib import Path

import yaml

from shared.audit import audit, control_event
from shared.config import ROOT, load_yaml
from shared.telemetry import hash_claim_id, tracer


class ToolPermissionError(PermissionError):
    pass


@lru_cache
def permission_matrix() -> dict:
    out = {}
    for t in load_yaml("docs/design/tool-permission-matrix.yaml")["tools"]:
        out[t["id"]] = {"agents": set(t["agents"]),
                        "access": {a.strip() for a in str(t["access"]).split(",")}}
    return out


@lru_cache
def tool_modules() -> dict:
    mods = {}
    for p in sorted((ROOT / "tools").glob("*/tool.yaml")):
        spec = yaml.safe_load(p.read_text())
        mods[spec["id"]] = importlib.import_module(f"tools.{Path(p).parent.name}.impl")
    return mods


def call_tool(agent_name: str, tool_id: str, op: str, *, claim_id: str | None = None, invoked_by: str = "code",
              **kwargs):
    """invoked_by: 'model' when the LLM chose the call (create_react_agent tool), 'code' otherwise.
    Latency and outcome are recorded from this span by the telemetry sink."""
    rule = permission_matrix().get(tool_id)
    mod = tool_modules().get(tool_id)
    if rule is None or mod is None:
        raise ToolPermissionError(f"unknown tool {tool_id}")
    access = mod.OPERATIONS.get(op)
    if access is None:
        raise ToolPermissionError(f"{tool_id} has no operation {op}")
    with tracer().start_as_current_span(f"tool {tool_id}.{op}") as span:
        for k, v in {"tool.id": tool_id, "tool.operation": op, "tool.access": access, "tool.caller": agent_name,
                     "tool.invoked_by": invoked_by}.items():
            span.set_attribute(k, v)
        if claim_id:
            span.set_attribute("claim.id", hash_claim_id(claim_id))
        if agent_name not in rule["agents"] or access not in rule["access"]:
            span.set_attribute("tool.status", "denied")
            control_event(claim_id, "tool_permission", "denied", f"{agent_name} -> {tool_id}.{op} ({access})")
            raise ToolPermissionError(f"agent '{agent_name}' may not call {tool_id}.{op} (access '{access}')")
        fn = getattr(mod, op)
        if "claim_id" in inspect.signature(fn).parameters:
            kwargs["claim_id"] = claim_id
        try:
            result = fn(**kwargs)
        except Exception:
            span.set_attribute("tool.status", "error")
            raise
        span.set_attribute("tool.status", "ok")
    audit(claim_id, "tool_call", {"tool": tool_id, "op": op, "access": access, "invoked_by": invoked_by},
          actor=agent_name)
    return result
