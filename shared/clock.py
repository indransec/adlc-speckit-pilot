"""Simulation clock: one notion of 'now' for every process in a simulation run.

Business timestamps (claims, approvals, gate decisions, ledger events) come from here.
Outside a simulation there is no clock file and now() is the real UTC time.

The state lives in a small JSON file (SIM_CLOCK_FILE, default <DATA_DIR>/sim_clock.json) so the
API, the Streamlit pages, the story runner and evaluation subprocesses all agree:

  sim_now = anchor_sim + (wall_now - anchor_wall) * rate

rate 0 = paused, 1 = real time, 720 = one simulated day per two real minutes. Skipping ahead
re-anchors. Durations that measure real work (processing latency) and security expiries
(approval tokens) deliberately stay on wall-clock time.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared.config import data_dir

DAY = 86400.0
_cache: dict = {"path": None, "mtime": None, "state": None}


def clock_file() -> Path:
    return Path(os.environ.get("SIM_CLOCK_FILE") or data_dir() / "sim_clock.json")


def _state() -> dict | None:
    p = clock_file()
    try:
        mtime = p.stat().st_mtime
    except FileNotFoundError:
        return None
    if _cache["path"] != p or _cache["mtime"] != mtime:
        _cache.update(path=p, mtime=mtime, state=json.loads(p.read_text()))
    return _cache["state"]


def _write(state: dict) -> dict:
    p = clock_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, p)
    return state


def is_simulated() -> bool:
    return _state() is not None


def now() -> datetime:
    s = _state()
    if s is None:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(s["anchor_sim"]) + timedelta(seconds=(time.time() - s["anchor_wall"]) * s["rate"])


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def rate() -> float:
    s = _state()
    return 1.0 if s is None else float(s["rate"])


def wall_seconds(sim_seconds: float) -> float:
    """Real seconds that correspond to a span of simulated seconds at the current rate."""
    r = rate()
    return sim_seconds / r if r else sim_seconds


def day0() -> datetime | None:
    s = _state()
    return datetime.fromisoformat(s["day0"]) if s else None


def sim_day(at: datetime | None = None) -> float | None:
    """Days relative to day 0 (shadow start): -60 is G0 intake, 29 the start of full rollout."""
    d0 = day0()
    return None if d0 is None else ((at or now()) - d0).total_seconds() / DAY


def at_day(day: float, hour: float = 9.0) -> datetime:
    return day0() + timedelta(days=day, hours=hour)


def state() -> dict:
    s = _state()
    if s is None:
        return {"simulated": False, "now": now_iso(), "rate": 1.0}
    return {"simulated": True, "now": now_iso(), "rate": s["rate"], "day0": s["day0"],
            "sim_day": round(sim_day(), 3), "paused": s["rate"] == 0, "resume_rate": s.get("resume_rate", 1.0)}


# ------------------------------------------------------------------------------------- control
def start(sim_start: datetime, day0_at: datetime, rate_: float = 1.0) -> dict:
    return _write({"anchor_wall": time.time(), "anchor_sim": sim_start.isoformat(), "rate": rate_,
                   "resume_rate": rate_ or 1.0, "day0": day0_at.isoformat()})


def _reanchor(**changes) -> dict:
    s = _state()
    if s is None:
        raise RuntimeError("no simulation clock: create a run with `make sim-new`")
    return _write({**s, "anchor_wall": time.time(), "anchor_sim": now().isoformat(), **changes})


def set_rate(rate_: float) -> dict:
    return _reanchor(rate=float(rate_), **({"resume_rate": float(rate_)} if rate_ else {}))


def pause() -> dict:
    return _reanchor(rate=0.0)


def resume() -> dict:
    return _reanchor(rate=_state()["resume_rate"])


def advance(seconds: float) -> dict:
    """Skip ahead (quiet periods such as the weeks between G0 and G1)."""
    s = _reanchor()
    return _write({**s, "anchor_sim": (datetime.fromisoformat(s["anchor_sim"]) + timedelta(seconds=seconds)).isoformat()})


def set_time(at: datetime) -> dict:
    """Move to an exact moment (forward only); the story runner uses this with the clock paused."""
    if at < now():
        raise ValueError(f"{at.isoformat()} is in the past (now: {now_iso()})")
    return _reanchor(anchor_sim=at.isoformat())


def skip_to_day(day: float, hour: float = 9.0) -> dict:
    target = at_day(day, hour)
    if target < now():
        raise ValueError(f"sim day {day} is in the past (now: day {sim_day():.2f})")
    return _reanchor(anchor_sim=target.isoformat())
