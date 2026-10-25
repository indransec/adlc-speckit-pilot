"""Runtime flags (rollout mode, feature flags, kill switch, LLM mode).

Defaults come from deploy/environments/<APP_ENV>.yaml; operators override them at
runtime (Streamlit lifecycle console / API) and every change is audited.
"""
from __future__ import annotations

import json

from shared import db
from shared.config import env_config, openrouter_api_key

MODES = ("shadow", "assisted", "full")
LLM_MODES = ("auto", "live", "mock")


def _defaults() -> dict:
    e = env_config()
    flags = e.get("feature_flags", {})
    limits = e.get("limits", {})
    return {
        "mode": e.get("mode", "full"),
        "straight_through_enabled": flags.get("straight_through_enabled", True),
        "payments_enabled": flags.get("payments_enabled", True),
        "kill_switch": flags.get("kill_switch", False),
        "llm_mode": e.get("llm_mode", "auto"),
        "max_cost_per_claim_usd": float(limits.get("max_cost_per_claim_usd", 0.25)),
        "max_agent_steps": int(limits.get("max_agent_steps", 40)),
    }


def get_runtime() -> dict:
    cfg = _defaults()
    try:
        for r in db.query("SELECT key, value FROM runtime_config"):
            cfg[r["key"]] = json.loads(r["value"])
    except Exception:  # table not created yet
        pass
    cfg["llm_effective"] = effective_llm_mode(cfg)
    return cfg


def effective_llm_mode(cfg: dict | None = None) -> str:
    mode = (cfg or get_runtime())["llm_mode"]
    if mode == "mock":
        return "mock"
    if openrouter_api_key():
        return "live"
    return "mock"


def set_runtime(key: str, value, by: str = "operator") -> dict:
    if key not in _defaults():
        raise KeyError(key)
    if key == "mode" and value not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if key == "llm_mode" and value not in LLM_MODES:
        raise ValueError(f"llm_mode must be one of {LLM_MODES}")
    db.upsert("runtime_config", {"key": key, "value": json.dumps(value), "updated_at": db.now(), "updated_by": by})
    from shared.audit import audit
    audit(None, "runtime_config_changed", {"key": key, "value": value}, actor=by)
    return get_runtime()


def reset_runtime() -> None:
    db.execute("DELETE FROM runtime_config")
