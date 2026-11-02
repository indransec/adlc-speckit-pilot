"""Fault injection for the end-to-end simulation (catalogue: sim/faults.yaml).

Code calls apply(hook, value, **context) at a few injection points; outside a simulation run
(APP_ENV != sim) it always returns the value unchanged, so no fault can fire in dev, tests or
production builds. A fault is active when:
  - code_defect: the current release is one of its active_in_releases (F2);
  - runtime:     it was injected with inject() and not yet cleared (F3-F10; state in <DATA_DIR>/sim_faults.json);
  - forced:      SIM_FAULTS=F2,F3 (tests).
"""
from __future__ import annotations

import hashlib
import json
import os

import yaml

from shared.config import ROOT, data_dir, is_sim, release


class ToolUnavailable(RuntimeError):
    pass


def _bucket(*parts) -> int:
    """Stable 0-99 bucket so a fault hits the same claims on every replay."""
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest(), 16) % 100


def _assessment(out: dict, p: dict, claim_id: str = "", **_) -> dict:
    if out.get("recommendation") == "reject" or _bucket("F3", claim_id) >= p.get("pct", 35):
        return out
    return {**out, "recommendation": "reject", "recommended_usd": 0.0,
            "rationale": "coverage not found for the claimed loss (degraded coverage model)"}


def _tool_call(_value, p: dict, tool_id: str = "", claim_id: str = "", op: str = "", **_):
    if tool_id == p.get("tool") and _bucket("F7", claim_id, op) < p.get("pct", 50):
        raise ToolUnavailable(f"{tool_id} unavailable (simulated outage)")
    return _value


def _model(model: str, p: dict, **_) -> str:
    return p.get("swap", {}).get(model, model)


TRANSFORMS = {
    "policy_fx_rates": lambda rates, p, **_: {k: v for k, v in rates.items() if k not in p.get("drop", [])},
    "assessment_result": _assessment,
    "stp_limit": lambda limit, p, **_: float(p.get("limit", limit)),
    "skip_token_check": lambda skip, p, **_: True,
    "decision_latency_s": lambda s, p, **_: (s or 0) + float(p.get("add_s", 0)),
    "tool_call": _tool_call,
    "agent_model": _model,
}


def catalog() -> dict:
    return yaml.safe_load((ROOT / "sim" / "faults.yaml").read_text())["faults"]


def _state_path():
    return data_dir() / "sim_faults.json"


def injected() -> dict:
    p = _state_path()
    return json.loads(p.read_text()) if p.exists() else {}


def _save(state: dict) -> None:
    p = _state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, p)


def inject(fault_id: str, *, by: str, note: str = "") -> dict:
    from shared import clock, ledger
    spec = catalog()[fault_id]
    if spec.get("kind") != "runtime":
        raise ValueError(f"{fault_id} is a {spec.get('kind')} fault; it is injected through a release")
    state = injected()
    state[fault_id] = {"since": clock.now_iso(), "by": by}
    _save(state)
    ledger.append("fault_injected", item=None, fault=fault_id, release=release(), by=by, note=note, simulation=True)
    return state[fault_id]


def clear(fault_id: str, *, by: str, note: str = "") -> None:
    from shared import ledger
    state = injected()
    if state.pop(fault_id, None) is not None:
        _save(state)
        ledger.append("fault_cleared", item=None, fault=fault_id, release=release(), by=by, note=note, simulation=True)


def active(hook: str | None = None) -> list[dict]:
    if not is_sim():
        return []
    forced = {x.strip() for x in os.environ.get("SIM_FAULTS", "").split(",") if x.strip()}
    runtime = injected()
    rel = release()
    return [{"id": k, **v} for k, v in catalog().items()
            if v.get("hook") and (hook is None or v["hook"] == hook)
            and (rel in v.get("active_in_releases", []) or k in forced or k in runtime)]


def apply(hook: str, value, **context):
    for f in active(hook):
        value = TRANSFORMS[hook](value, f.get("params") or {}, **context)
    return value
