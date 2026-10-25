"""OpenTelemetry setup following shared/telemetry/README.md.

- Spans export over OTLP to Arize Phoenix (PHOENIX_COLLECTOR_ENDPOINT).
- LangChain / LangGraph calls are auto-instrumented with OpenInference (gen_ai / llm.* attributes).
- AdlcSpanProcessor stamps EVERY span with system id, release, agent id/version and a
  hashed claim id (REQ-N-007). No PII goes into span attributes.
"""
from __future__ import annotations

import contextvars
import hashlib
import logging
import os
import urllib.request
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider

from shared.config import release, system_id

log = logging.getLogger(__name__)

_agent_id = contextvars.ContextVar("adlc_agent_id", default="AGT-TRV-ORCH")
_agent_version = contextvars.ContextVar("adlc_agent_version", default=None)
_claim_hash = contextvars.ContextVar("adlc_claim_hash", default=None)
_routing_row = contextvars.ContextVar("adlc_routing_row", default=None)

_provider: TracerProvider | None = None
TRACER_NAME = "travel-claims-assistant"


def hash_claim_id(claim_id: str | None) -> str | None:
    if not claim_id:
        return None
    salt = os.environ.get("TELEMETRY_SALT", "adlc-sim")
    return "c_" + hashlib.sha256(f"{salt}:{claim_id}".encode()).hexdigest()[:12]


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

    def on_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30000):
        return True


def _phoenix_reachable(endpoint: str) -> bool:
    base = endpoint.split("/v1/")[0]
    try:
        urllib.request.urlopen(base, timeout=0.5)
        return True
    except Exception:
        return False


def init_telemetry(extra_processors: list | None = None) -> TracerProvider:
    """Idempotent. Exports to Phoenix only when it is reachable, so the app never blocks on it."""
    global _provider
    if _provider is not None:
        for p in extra_processors or []:
            _provider.add_span_processor(p)
        return _provider
    endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces")
    enabled = os.environ.get("TELEMETRY_ENABLED", "true").lower() == "true"
    if enabled and _phoenix_reachable(endpoint):
        from phoenix.otel import register
        provider = register(endpoint=endpoint, project_name=os.environ.get("PHOENIX_PROJECT", TRACER_NAME),
                            batch=True, set_global_tracer_provider=False, verbose=False)
        log.info("telemetry: exporting spans to Phoenix at %s", endpoint)
    else:
        provider = TracerProvider()
        log.info("telemetry: Phoenix not reachable; spans stay in-process")
    provider.add_span_processor(AdlcSpanProcessor())
    for p in extra_processors or []:
        provider.add_span_processor(p)
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
def agent_span(agent_id: str, agent_version: str, claim_id: str | None, name: str, **attrs):
    t1, t2, t3 = _agent_id.set(agent_id), _agent_version.set(agent_version), _claim_hash.set(hash_claim_id(claim_id))
    try:
        with tracer().start_as_current_span(name) as span:
            for k, v in attrs.items():
                if v is not None:
                    span.set_attribute(k, v)
            yield span
    finally:
        _agent_id.reset(t1); _agent_version.reset(t2); _claim_hash.reset(t3)


def set_routing_row(row: int | None):
    _routing_row.set(row)


def current_agent() -> tuple[str, str | None]:
    return _agent_id.get(), _agent_version.get()
