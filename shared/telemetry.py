"""OpenTelemetry setup following shared/telemetry/README.md.

- Spans export over OTLP to Arize Phoenix (PHOENIX_COLLECTOR_ENDPOINT).
- LangChain / LangGraph calls are auto-instrumented with OpenInference (gen_ai / llm.* attributes).
- AdlcSpanProcessor stamps EVERY span with system id, release, agent id/version and a
  hashed claim id (REQ-N-007). No PII goes into span attributes.
- TelemetrySink turns finished spans into the app's llm_calls / tool_calls rows: tokens come
  from the model's reported usage (OpenInference llm.token_count.*), latency from span
  timings, cost from those tokens at the deploy/models.yaml prices. The same prices are
  registered in Phoenix, which computes cost from the same spans (Phoenix is the system of
  record; the local rows keep budgets, dashboards and CI gates working without it).
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import re
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone

from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider

from shared.config import model_catalog, release, system_id

log = logging.getLogger(__name__)

_agent_id = contextvars.ContextVar("adlc_agent_id", default="AGT-TRV-ORCH")
_agent_version = contextvars.ContextVar("adlc_agent_version", default=None)
_claim_hash = contextvars.ContextVar("adlc_claim_hash", default=None)
_routing_row = contextvars.ContextVar("adlc_routing_row", default=None)
_run_no = contextvars.ContextVar("adlc_run_no", default=None)
_task = contextvars.ContextVar("adlc_task", default=None)
_llm_mode = contextvars.ContextVar("adlc_llm_mode", default=None)
_claim_by_hash: dict[str, str] = {}  # in-process only, so the sink can join spans to claims

_provider: TracerProvider | None = None
TRACER_NAME = "travel-claims-assistant"


def hash_claim_id(claim_id: str | None) -> str | None:
    if not claim_id:
        return None
    salt = os.environ.get("TELEMETRY_SALT", "adlc-sim")
    h = "c_" + hashlib.sha256(f"{salt}:{claim_id}".encode()).hexdigest()[:12]
    _claim_by_hash[h] = claim_id
    return h


class AdlcSpanProcessor(SpanProcessor):
    def on_start(self, span, parent_context=None):
        from shared.registry import agent_version_of
        span.set_attribute("adlc.system.id", system_id())
        span.set_attribute("adlc.system.release", release())
        aid = _agent_id.get()
        span.set_attribute("adlc.agent.id", aid)
        span.set_attribute("adlc.agent.version", _agent_version.get() or agent_version_of(aid))
        if _claim_hash.get():
            span.set_attribute("claim.id", _claim_hash.get())
        if _routing_row.get() is not None:
            span.set_attribute("routing.row", _routing_row.get())
        for key, var in (("adlc.run_no", _run_no), ("adlc.task", _task), ("llm.mode", _llm_mode)):
            if var.get() is not None:
                span.set_attribute(key, var.get())

    def on_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30000):
        return True


def price_usd(model: str | None, prompt_tokens: int, completion_tokens: int) -> float:
    m = model_catalog().get(model or "", {"input_per_m": 0, "output_per_m": 0})
    return round(prompt_tokens * m["input_per_m"] / 1e6 + completion_tokens * m["output_per_m"] / 1e6, 6)


def _iso(ns: int | None) -> str:
    return datetime.fromtimestamp((ns or 0) / 1e9, timezone.utc).isoformat(timespec="milliseconds")


def _input_text(attrs, limit: int = 20000) -> str:
    """What was sent to the model, as captured on the LLM span (system prompt, user turn, tool results)."""
    parts, i = [], 0
    while f"llm.input_messages.{i}.message.role" in attrs:
        content = attrs.get(f"llm.input_messages.{i}.message.content")
        if content:
            parts.append(f"[{attrs[f'llm.input_messages.{i}.message.role']}] {content}")
        i += 1
    return "\n".join(parts)[-limit:]


class TelemetrySink(SpanProcessor):
    """Records finished LLM spans (OpenInference) and tool-gateway spans into SQLite."""

    def on_start(self, span, parent_context=None):
        pass

    def on_end(self, span):
        try:
            a = span.attributes or {}
            if a.get("openinference.span.kind") == "LLM":
                self._llm(span, a)
            elif "tool.id" in a:
                self._tool(span, a)
        except Exception as e:  # telemetry must never break a claim
            log.warning("telemetry sink: %s", e)

    @staticmethod
    def _common(span, a) -> dict:
        from shared import clock  # rows use business time (the simulation clock in a run); latency stays real
        return {"ts": clock.now_iso(), "claim_id": _claim_by_hash.get(a.get("claim.id")),
                "run_no": a.get("adlc.run_no"), "agent_id": a.get("adlc.agent.id"),
                "latency_ms": round(((span.end_time or 0) - (span.start_time or 0)) / 1e6, 1),
                "trace_id": format(span.context.trace_id, "032x")}

    def _llm(self, span, a):
        from shared import db
        from opentelemetry.trace import StatusCode
        model = a.get("llm.model_name")
        pt, ct = int(a.get("llm.token_count.prompt") or 0), int(a.get("llm.token_count.completion") or 0)
        error = span.status.status_code == StatusCode.ERROR
        mode = a.get("llm.mode") or "live"
        db.insert("llm_calls", {**self._common(span, a), "agent_version": a.get("adlc.agent.version"),
                                "task": a.get("adlc.task"), "model": model,
                                "tier": model_catalog().get(model or "", {}).get("tier", "unknown"), "mode": mode,
                                "prompt_tokens": pt, "completion_tokens": ct, "cost_usd": price_usd(model, pt, ct),
                                "status": "error" if error else ("mock" if mode == "mock" else "ok"),
                                "degraded": int(error), "input_excerpt": _input_text(a),
                                "span_id": format(span.context.span_id, "016x")})

    def _tool(self, span, a):
        from shared import db
        db.insert("tool_calls", {**self._common(span, a), "caller": a.get("tool.caller"), "tool_id": a.get("tool.id"),
                                 "operation": a.get("tool.operation"), "access": a.get("tool.access"),
                                 "invoked_by": a.get("tool.invoked_by", "code"), "status": a.get("tool.status", "ok")})

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30000):
        return True


def register_model_prices(phoenix_base: str) -> list[str]:
    """Give Phoenix the deploy/models.yaml prices so its cost tracking prices our OpenRouter model ids."""
    def gql(query: str, variables: dict | None = None) -> dict:
        req = urllib.request.Request(f"{phoenix_base}/graphql", method="POST",
                                     data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read())
    have = {e["node"]["name"] for e in gql("{ generativeModels(first: 500) { edges { node { name } } } }")
            ["data"]["generativeModels"]["edges"]}
    added = []
    for mid, m in model_catalog().items():
        if mid in have:
            continue
        res = gql("mutation($input: CreateModelMutationInput!) { createModel(input: $input) { model { id } } }",
                  {"input": {"name": mid, "provider": "openrouter", "namePattern": f"^{re.escape(mid)}$",
                             "costs": [{"tokenType": "input", "costPerMillionTokens": m["input_per_m"], "kind": "PROMPT"},
                                       {"tokenType": "output", "costPerMillionTokens": m["output_per_m"],
                                        "kind": "COMPLETION"}]}})
        if res.get("errors"):
            raise RuntimeError(res["errors"][0].get("message"))
        added.append(mid)
    return added


def phoenix_cost_summary() -> dict | None:
    """Total tokens and cost for this project as computed by Phoenix (None if Phoenix is not reachable)."""
    base = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces").split("/v1/")[0]
    project = os.environ.get("PHOENIX_PROJECT", TRACER_NAME)
    q = "{ projects { edges { node { name costSummary { prompt { tokens cost } completion { tokens cost } total { tokens cost } } } } } }"
    try:
        req = urllib.request.Request(f"{base}/graphql", method="POST", data=json.dumps({"query": q}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            edges = json.loads(r.read())["data"]["projects"]["edges"]
    except Exception:
        return None
    node = next((e["node"] for e in edges if e["node"]["name"] == project), None)
    return {"project": project, **node["costSummary"]} if node else {"project": project, "total": None}


def _phoenix_reachable(endpoint: str) -> bool:
    base = endpoint.split("/v1/")[0]
    try:
        urllib.request.urlopen(base, timeout=0.5)
        return True
    except Exception:
        return False


def _add_processor(provider: TracerProvider, processor) -> None:
    # phoenix.otel's provider drops its OTLP exporter on add_span_processor unless told not to
    try:
        provider.add_span_processor(processor, replace_default_processor=False)
    except TypeError:
        provider.add_span_processor(processor)


def init_telemetry(extra_processors: list | None = None) -> TracerProvider:
    """Idempotent. Exports to Phoenix only when it is reachable, so the app never blocks on it."""
    global _provider
    if _provider is not None:
        for p in extra_processors or []:
            _add_processor(_provider, p)
        return _provider
    endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces")
    enabled = os.environ.get("TELEMETRY_ENABLED", "true").lower() == "true"
    if enabled and _phoenix_reachable(endpoint):
        from phoenix.otel import register
        provider = register(endpoint=endpoint, project_name=os.environ.get("PHOENIX_PROJECT", TRACER_NAME),
                            batch=True, set_global_tracer_provider=False, verbose=False)
        log.info("telemetry: exporting spans to Phoenix at %s", endpoint)
        try:
            added = register_model_prices(endpoint.split("/v1/")[0])
            if added:
                log.info("telemetry: registered model prices in Phoenix for %s", ", ".join(added))
        except Exception as e:
            log.warning("telemetry: could not register model prices in Phoenix: %s", e)
    else:
        provider = TracerProvider()
        log.info("telemetry: Phoenix not reachable; spans stay in-process")
    _add_processor(provider, AdlcSpanProcessor())
    _add_processor(provider, TelemetrySink())
    for p in extra_processors or []:
        _add_processor(provider, p)
    try:
        trace.set_tracer_provider(provider)
    except Exception:
        pass
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        LangChainInstrumentor().instrument(tracer_provider=provider)
    except Exception as e:  # instrumentation is best-effort
        log.warning("LangChain instrumentation unavailable: %s", e)
    _provider = provider
    return provider


def tracer():
    return (_provider or init_telemetry()).get_tracer(TRACER_NAME)


@contextmanager
def claim_context(claim_id: str | None):
    tok = _claim_hash.set(hash_claim_id(claim_id))
    try:
        yield
    finally:
        _claim_hash.reset(tok)


@contextmanager
def agent_span(agent_id: str, agent_version: str, claim_id: str | None, name: str, run_no: int | None = None, **attrs):
    t1, t2, t3 = _agent_id.set(agent_id), _agent_version.set(agent_version), _claim_hash.set(hash_claim_id(claim_id))
    t4 = _run_no.set(run_no) if run_no is not None else None
    try:
        with tracer().start_as_current_span(name) as span:
            for k, v in attrs.items():
                if v is not None:
                    span.set_attribute(k, v)
            yield span
    finally:
        _agent_id.reset(t1); _agent_version.reset(t2); _claim_hash.reset(t3)
        if t4 is not None:
            _run_no.reset(t4)


@contextmanager
def llm_context(*, task: str, mode: str, run_no: int | None):
    """Labels every span inside (agent graph, LLM, tool) with the task, LLM mode and run number."""
    toks = _task.set(task), _llm_mode.set(mode), _run_no.set(run_no)
    try:
        yield
    finally:
        for var, tok in zip((_task, _llm_mode, _run_no), toks):
            var.reset(tok)


def set_routing_row(row: int | None):
    _routing_row.set(row)


def current_agent() -> tuple[str, str | None]:
    return _agent_id.get(), _agent_version.get()
