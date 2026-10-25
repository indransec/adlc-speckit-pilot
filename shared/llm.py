"""LLM gateway: chat models for the agents (OpenRouter AI gateway, ADR-005).

- Live: ChatOpenAI pointed at OpenRouter, with the model pinned in the agent manifest
  (tiered catalog in deploy/models.yaml). The catalog also sets each model's structured-output
  method: tool calling, or JSON schema where the model rejects a forced tool_choice.
- Mock: when there is no OPENROUTER_API_KEY (or llm_mode=mock) a scripted chat model stands in.
  It makes the tool calls a live model would typically make, then returns the agent's
  deterministic fallback as the structured answer, and reports estimated token usage the same
  way a provider does. The system, CI and evals run offline and reproducibly, and the traces
  have the same shape as live runs.

Nothing here times or prices calls: OpenInference instrumentation records tokens and latency
on the LLM spans, and the telemetry sink / Phoenix derive cost from them (shared/telemetry.py).
"""
from __future__ import annotations

import json
import os
import random
import time
import uuid
from functools import lru_cache
from typing import Any, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from shared.config import OPENROUTER_BASE_URL, model_catalog, openrouter_api_key


FINAL_ANSWER_TURN = "Now give your final answer in the required output format."


def _end_with_user_turn(messages):
    """create_react_agent asks for the structured response with the agent's last (assistant) message at
    the end of the conversation; Claude models reject that as prefill, so close with a user turn."""
    if isinstance(messages, list) and messages and isinstance(messages[-1], AIMessage):
        return [*messages, HumanMessage(FINAL_ANSWER_TURN)]
    return messages


class OpenRouterChat(ChatOpenAI):
    """ChatOpenAI against OpenRouter. The structured-output method comes from the model catalog and the
    request never ends on an assistant turn."""

    structured_method: str = "function_calling"

    def with_structured_output(self, schema=None, *, method: str | None = None, **kwargs):
        inner = super().with_structured_output(schema, method=method or self.structured_method, **kwargs)
        return RunnableLambda(_end_with_user_turn) | inner


@lru_cache(maxsize=16)
def live_chat(model: str, temperature: float) -> OpenRouterChat:
    return OpenRouterChat(
        structured_method=model_catalog().get(model, {}).get("structured_output", "function_calling"),
        model=model, base_url=OPENROUTER_BASE_URL, api_key=openrouter_api_key(), temperature=temperature,
        timeout=float(os.environ.get("LLM_TIMEOUT_S", 90)), max_retries=2,
        default_headers={"HTTP-Referer": "https://localhost/travel-claims-assistant",
                         "X-Title": "Travel Claims Assistant (ADLC simulation)"},
    )


ToolCall = tuple[str, dict]


class ScriptedChatModel(BaseChatModel):
    """Deterministic stand-in for the live model (mock mode).

    plan: tool calls to make before answering; each step is one (name, args) call or a list of
    them (parallel calls). Calls to tools that are not bound are skipped.
    answer: returns the structured answer (the agent's deterministic fallback).
    """

    model_name: str
    plan: list = []
    answer: Callable[[], BaseModel] | None = None
    latency_ms: int = 0
    bound: list[str] = []
    forced: str | None = None
    tools_chars: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted-mock"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name}

    def _get_ls_params(self, stop=None, **kwargs):
        params = super()._get_ls_params(stop=stop, **kwargs)
        params.update(ls_provider="openrouter", ls_model_name=self.model_name)
        return params

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        specs = [convert_to_openai_tool(t) for t in tools]
        names = [s["function"]["name"] for s in specs]
        forced = names[0] if tool_choice not in (None, "auto", "none") and len(names) == 1 else None
        return self.model_copy(update={"bound": names, "forced": forced, "tools_chars": len(json.dumps(specs))})

    def _step(self, messages: list[BaseMessage]) -> int:
        n = 0
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                break
            n += isinstance(m, AIMessage)
        return n

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self.latency_ms:
            time.sleep(random.uniform(0.5, 1.5) * self.latency_ms / 1000)
        if self.forced:  # the structured-response call
            calls = [(self.forced, self.answer().model_dump(mode="json"))]
            msg = AIMessage(content="", tool_calls=[_tc(n, a) for n, a in calls])
        else:
            steps = [s if isinstance(s, list) else [s] for s in self.plan]
            steps = [[c for c in s if c[0] in self.bound] for s in steps]
            steps = [s for s in steps if s]
            i = self._step(messages)
            if i < len(steps):
                msg = AIMessage(content="", tool_calls=[_tc(n, a) for n, a in steps[i]])
            else:
                msg = AIMessage(content="I have the information I need.")
        pt = (sum(len(str(m.content)) for m in messages) + self.tools_chars) // 4 + 1
        ct = (len(str(msg.content)) + len(json.dumps([c["args"] for c in msg.tool_calls]))) // 4 + 1
        usage = {"input_tokens": pt, "output_tokens": ct, "total_tokens": pt + ct}
        msg.usage_metadata = usage
        msg.response_metadata = {"model_name": self.model_name}
        return ChatResult(generations=[ChatGeneration(message=msg)],
                          llm_output={"model_name": self.model_name,
                                      "token_usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}})


def _tc(name: str, args: dict) -> dict:
    return {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}


def chat_model(model: str, temperature: float, mode: str, *, plan: list | None = None,
               answer: Callable[[], BaseModel] | None = None) -> BaseChatModel:
    if mode == "live":
        return live_chat(model, temperature)
    return ScriptedChatModel(model_name=model, plan=plan or [], answer=answer,
                             latency_ms=int(os.environ.get("MOCK_LLM_LATENCY_MS", "0")))
