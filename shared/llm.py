"""LLM gateway: every model call goes through here (OpenRouter AI gateway, ADR-005).

Responsibilities
- Resolve the pinned model from the agent manifest; tiered catalog in deploy/models.yaml.
- Final never_send_to_model check (bank / card data) before any call (REQ-B-009).
- Structured output (Pydantic) via tool calling; one retry-with-fallback path.
- Mock mode: when no OPENROUTER_API_KEY (or llm_mode=mock) the agent's deterministic
  fallback is used, so the system, CI and evals run offline and reproducibly.
- Telemetry: gen_ai.* span attributes, tokens, cost and latency recorded per call.
"""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, TypeVar

from pydantic import BaseModel

from shared import db
from shared.config import OPENROUTER_BASE_URL, model_catalog, openrouter_api_key
from shared.guardrails.pii import redact, restricted_present
from shared.runtime import effective_llm_mode
from shared.telemetry import tracer

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMMeta:
    model: str
    tier: str
    mode: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: float
    degraded: bool
    status: str
    error: str | None = None


@lru_cache(maxsize=16)
def _chat(model: str, temperature: float):
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model, base_url=OPENROUTER_BASE_URL, api_key=openrouter_api_key(), temperature=temperature,
        timeout=float(os.environ.get("LLM_TIMEOUT_S", 90)), max_retries=2,
        default_headers={"HTTP-Referer": "https://localhost/travel-claims-assistant",
                         "X-Title": "Travel Claims Assistant (ADLC simulation)"},
    )


def _price(model: str, pt: int, ct: int) -> float:
    m = model_catalog().get(model, {"input_per_m": 0, "output_per_m": 0})
    return round(pt * m["input_per_m"] / 1e6 + ct * m["output_per_m"] / 1e6, 6)


def structured_call(*, manifest: dict, claim_id: str | None, task: str, schema: type[T], system_prompt: str,
                    user_content: str, fallback: Callable[[], T], model_override: str | None = None) -> tuple[T, LLMMeta]:
    agent_id = manifest["id"]
    model = model_override or manifest["model"]["name"]
    temperature = float(manifest["model"].get("params", {}).get("temperature", 0.1))
    tier = model_catalog().get(model, {}).get("tier", "unknown")

    restricted = restricted_present(user_content)
    if restricted:  # defence in depth: the agent should already have redacted these
        from shared.audit import control_event, guardrail_event
        user_content = redact(user_content, purpose="log").text
        guardrail_event(claim_id, "pii-redaction", "blocked-restricted-before-model", ",".join(restricted), agent_id)
        control_event(claim_id, "never_send_to_model", "redacted_at_gateway", ",".join(restricted))

    mode = effective_llm_mode()
    t0 = time.perf_counter()
    status, error, degraded = "ok", None, False
    with tracer().start_as_current_span(f"gen_ai.chat {task}") as span:
        span.set_attribute("gen_ai.system", "openrouter")
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("gen_ai.request.temperature", temperature)
        span.set_attribute("llm.mode", mode)
        if mode == "mock":
            lat = int(os.environ.get("MOCK_LLM_LATENCY_MS", "0"))
            if lat:
                time.sleep(random.uniform(0.5, 1.5) * lat / 1000)
            value = fallback()
            pt = (len(system_prompt) + len(user_content)) // 4
            ct = len(value.model_dump_json()) // 4
            status = "mock"
        else:
            try:
                runnable = _chat(model, temperature).with_structured_output(
                    schema, method="function_calling", include_raw=True)
                from langchain_core.messages import HumanMessage, SystemMessage
                out = runnable.invoke([SystemMessage(system_prompt), HumanMessage(user_content)])
                if out.get("parsing_error") or out.get("parsed") is None:
                    raise ValueError(f"structured output parse failed: {out.get('parsing_error')}")
                value = out["parsed"]
                usage = getattr(out["raw"], "usage_metadata", None) or {}
                pt, ct = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            except Exception as e:  # degrade to the deterministic path; confidence is lowered by the caller
                value, degraded, status, error = fallback(), True, "error", f"{type(e).__name__}: {e}"[:500]
                pt, ct = (len(system_prompt) + len(user_content)) // 4, 0
        latency = (time.perf_counter() - t0) * 1000
        cost = _price(model, pt, ct)
        span.set_attribute("gen_ai.usage.input_tokens", pt)
        span.set_attribute("gen_ai.usage.output_tokens", ct)
        span.set_attribute("llm.cost_usd", cost)
        if error:
            span.set_attribute("error.type", error[:100])

    db.insert("llm_calls", {
        "ts": db.now(), "claim_id": claim_id, "agent_id": agent_id, "task": task, "model": model, "tier": tier,
        "mode": mode, "prompt_tokens": pt, "completion_tokens": ct, "cost_usd": cost, "latency_ms": round(latency, 1),
        "status": status, "degraded": int(degraded), "input_excerpt": user_content[:4000],
    })
    return value, LLMMeta(model, tier, mode, pt, ct, cost, latency, degraded, status, error)
