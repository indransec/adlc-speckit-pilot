"""Agent runtime: each LLM-backed agent task runs as a LangGraph prebuilt ReAct agent
(create_react_agent) with its manifest's tools attached (decision D-12).

Tool attachment
- agent.yaml lists, per tool, the operations the model may call (`attach:`). Only operations
  with access 'read' can be attached; side-effecting operations (register, write, pay, send)
  stay in agent code, after validation.
- Every model tool call goes through the tool gateway (permission matrix, audit, span).
- Claim-scoped arguments (claim_id, exclude_claim_id, policy_no, signals) are bound by the
  platform and are not part of the tool schema, so the model cannot query another claim.
- Results are PII-redacted before the model sees them; untrusted documents are also
  injection-scanned and wrapped as data (REQ-B-009, REQ-B-010).

Each run: create_react_agent(model, tools, prompt=<system prompt>, response_format=<schema>).
Any failure (model error, bad output, tool loop over the recursion limit) degrades to the
agent's deterministic fallback and marks the agent degraded, which caps confidence.
"""
from __future__ import annotations

import inspect
import json
import logging
import typing
import warnings
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, TypeVar

import yaml
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from shared.config import ROOT
from shared.guardrails.injection import guard_document
from shared.guardrails.pii import redact, redact_for_model, restricted_present
from shared.llm import chat_model
from shared.runtime import effective_llm_mode
from shared.telemetry import init_telemetry, llm_context
from shared.tool_gateway import ToolPermissionError, tool_modules

with warnings.catch_warnings():
    # LangGraph 1.x marks create_react_agent deprecated in favour of langchain.agents.create_agent
    # (decision D-12 records why this release still uses it).
    warnings.simplefilter("ignore")
    from langgraph.prebuilt import create_react_agent

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

SCOPED_ARGS = {"claim_id", "exclude_claim_id"}
RECURSION_LIMIT = 12   # about 5 model/tool round trips per task


@lru_cache
def tool_contracts() -> dict[str, dict]:
    return {c["id"]: c for c in (yaml.safe_load(p.read_text()) for p in sorted((ROOT / "tools").glob("*/tool.yaml")))}


def tool_name(tool_id: str, op: str) -> str:
    return f"{tool_modules()[tool_id].__name__.split('.')[1]}_{op}"


def attached_operations(manifest: dict) -> list[tuple[str, str]]:
    return [(t["id"], op) for t in manifest.get("tools") or [] for op in t.get("attach") or []]


def check_attachments(manifest: dict) -> list[str]:
    """Problems with a manifest's attach lists (used by tests and the G2 tool-matrix check)."""
    problems = []
    for tool_id, op in attached_operations(manifest):
        spec = tool_contracts().get(tool_id, {}).get("operations", {}).get(op)
        if spec is None:
            problems.append(f"{manifest['name']}: {tool_id}.{op} is not in the tool contract")
        elif spec["access"] != "read":
            problems.append(f"{manifest['name']}: {tool_id}.{op} has access '{spec['access']}'; only read can be attached")
        elif not spec.get("description"):
            problems.append(f"{manifest['name']}: {tool_id}.{op} has no model-facing description")
    return problems


@dataclass
class AgentRun:
    task: str
    model: str
    mode: str
    degraded: bool
    error: str | None
    tool_calls: list[dict] = field(default_factory=list)


def _untrusted(ctx, tool_id: str, op: str, kwargs: dict, text: str) -> str:
    """Redact + injection-guard a document once per run; the code path reuses the result."""
    doc_id = kwargs.get("doc_id", f"{tool_id}.{op}")
    cached = ctx.documents.get(doc_id)
    if cached is None:
        safe = redact_for_model(text, known=ctx.known_pii, claim_id=ctx.claim_id, agent_id=ctx.agent_id)
        wrapped, scan = guard_document(safe, doc_id=doc_id, doc_type=ctx.doc_types.get(doc_id, "document"),
                                       claim_id=ctx.claim_id, agent_id=ctx.agent_id)
        cached = ctx.documents[doc_id] = {"safe": safe, "wrapped": wrapped, "scan": scan}
    return cached["wrapped"]


def read_document(ctx, doc_id: str, invoked_by: str = "code") -> dict:
    """Guarded document text for agent code (same path as the model's read tool)."""
    if doc_id not in ctx.documents:
        raw = ctx.tool("TOOL-DOCUMENT-STORE", "read_text", doc_id=doc_id, invoked_by=invoked_by)
        _untrusted(ctx, "TOOL-DOCUMENT-STORE", "read_text", {"doc_id": doc_id}, raw)
    return ctx.documents[doc_id]


def build_tools(ctx, bindings: dict | None = None) -> list[StructuredTool]:
    """LangChain tools for the operations attached in the agent manifest."""
    problems = check_attachments(ctx.manifest)
    if problems:
        raise ToolPermissionError("; ".join(problems))
    bindings = {"claim_id": ctx.claim_id, "exclude_claim_id": ctx.claim_id, **ctx.bindings, **(bindings or {})}
    tools = []
    for tool_id, op in attached_operations(ctx.manifest):
        spec = tool_contracts()[tool_id]["operations"][op]
        fn = getattr(tool_modules()[tool_id], op)
        hints = typing.get_type_hints(fn)
        params = inspect.signature(fn).parameters
        bound = {k: v for k, v in bindings.items() if k in params and k != "claim_id"}
        fields = {}
        for pname, p in params.items():
            if pname in bound or pname in SCOPED_ARGS:
                continue
            default = ... if p.default is inspect.Parameter.empty else p.default
            fields[pname] = (hints.get(pname, str), Field(default, description=str((spec.get("input") or {}).get(pname, ""))))
        name = tool_name(tool_id, op)
        tools.append(StructuredTool.from_function(
            func=_tool_fn(ctx, tool_id, op, bound, bool(spec.get("untrusted_output"))), name=name,
            description=spec["description"], args_schema=create_model(f"{name}_args", **fields)))
    return tools


def _tool_fn(ctx, tool_id: str, op: str, bound: dict, untrusted: bool) -> Callable[..., str]:
    def run(**kwargs) -> str:
        if untrusted and tool_id == "TOOL-DOCUMENT-STORE" and op == "read_text":
            try:
                return read_document(ctx, kwargs["doc_id"], invoked_by="model")["wrapped"]
            except (PermissionError, KeyError) as e:
                return f"ERROR: {type(e).__name__}: {e}"
        try:
            result = ctx.tool(tool_id, op, invoked_by="model", **bound, **kwargs)
        except (PermissionError, KeyError, ValueError, TypeError) as e:
            return f"ERROR: {type(e).__name__}: {e}"
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        if untrusted:
            return _untrusted(ctx, tool_id, op, kwargs, text)
        return redact_for_model(text, known=ctx.known_pii, claim_id=ctx.claim_id, agent_id=ctx.agent_id)
    return run


def run_agent(ctx, *, task: str, schema: type[T], user_content: str, fallback: Callable[[], T],
              mock_plan: list | None = None, bindings: dict | None = None, model_override: str | None = None,
              prompt: str | None = None) -> T:
    """Run one agent task as a create_react_agent graph; returns the structured response."""
    from shared.registry import system_prompt
    init_telemetry()
    manifest = ctx.manifest
    model = model_override or manifest["model"]["name"]
    temperature = float(manifest["model"].get("params", {}).get("temperature", 0.1))

    restricted = restricted_present(user_content)
    if restricted:  # defence in depth: the agent should already have redacted these
        from shared.audit import control_event, guardrail_event
        user_content = redact(user_content, purpose="log").text
        guardrail_event(ctx.claim_id, "pii-redaction", "blocked-restricted-before-model", ",".join(restricted), ctx.agent_id)
        control_event(ctx.claim_id, "never_send_to_model", "redacted_at_gateway", ",".join(restricted))

    mode = effective_llm_mode()
    run = AgentRun(task, model, mode, degraded=False, error=None)
    with llm_context(task=task, mode=mode, run_no=ctx.run_no):
        try:
            agent = create_react_agent(
                chat_model(model, temperature, mode, plan=mock_plan, answer=fallback),
                build_tools(ctx, bindings), prompt=prompt or system_prompt(ctx.agent_id), response_format=schema,
                name=f"{ctx.name}.{task.split(':')[0]}",
                checkpointer=False)  # an inner tool loop; HITL checkpoints belong to the claim graph only
            out = agent.invoke({"messages": [HumanMessage(user_content)]},
                               {"recursion_limit": RECURSION_LIMIT, "run_name": f"{ctx.name}.{task}",
                                "tags": [ctx.agent_id, task], "metadata": {"adlc.agent.id": ctx.agent_id,
                                                                           "adlc.task": task}})
            run.tool_calls = [{"tool": c["name"], "args": c["args"]} for m in out["messages"]
                              if isinstance(m, AIMessage) for c in m.tool_calls]
            value = out.get("structured_response")
            if isinstance(value, dict):
                value = schema(**value)
            if not isinstance(value, schema):
                raise ValueError("agent returned no structured response")
        except Exception as e:  # degrade to the deterministic path; confidence is capped by the orchestrator
            log.warning("%s.%s degraded: %s", ctx.name, task, e)
            value, run.degraded, run.error = fallback(), True, f"{type(e).__name__}: {e}"[:500]
    ctx.runs.append(run)
    return value


def tool_call_summary(runs: list[AgentRun]) -> list[dict[str, Any]]:
    return [{"task": r.task, **c} for r in runs for c in r.tool_calls]
