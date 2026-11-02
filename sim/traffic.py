"""Simulated claim traffic: new claims arrive on the simulation calendar and are processed by the live system.

Each simulated day is split into business-hour batches (08:00-17:00). For every batch the clock moves to
that hour, the claims are created with that submission time and processed concurrently, so timestamps,
shadow reports and (later) alerts all line up with the simulated calendar.
"""
from __future__ import annotations

import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Callable

from shared import clock, db
from synthetic.generate import next_claim_ids, used_flights
from synthetic.materialize import materialize
from synthetic.scenarios import FLIGHTS, TRAFFIC_WEIGHTS, build_case

HOURS = range(8, 18)
# Live traffic needs far more (flight, date) pairs than the 11 dataset flights offer: same airlines and
# routes, numbered variants (the dataset's FLIGHTS list is left alone so generated datasets do not change).
SIM_FLIGHTS = FLIGHTS + [(f"{no[:2]}{int(no[2:]) + k:0{len(no) - 2}d}", airline, route)
                         for no, airline, route in FLIGHTS for k in range(1, 10)]


def mix(extra: dict | None = None, scale: dict | None = None) -> dict:
    """Production traffic mix, plus extra scenarios and per-scenario multipliers (e.g. a fraud ring: fraud x3)."""
    m = {**TRAFFIC_WEIGHTS, **(extra or {})}
    return {k: v * (scale or {}).get(k, 1) for k, v in m.items()}


def _process(cid: str) -> str | None:
    from backend import service
    try:
        service.process(cid)
        return None
    except Exception as e:  # recorded, not raised: one bad claim must not stop the day
        return f"{cid}: {type(e).__name__}: {e}"


def _new_case(rng: random.Random, scenario: str, cid: str, used: set) -> dict:
    """A case whose flight departed in the last 1-10 days and is not already in the flight-status source:
    two claims on one (flight, date) would overwrite each other's delay record."""
    days = [(clock.now() - timedelta(days=k)).date() for k in range(1, 11)]
    free = [(f, d) for d in days for f in SIM_FLIGHTS if (f[0], d.isoformat()) not in used]
    if not free:
        raise RuntimeError("no free (flight, date) left in the last 10 days")
    flight, d = rng.choice(free)
    case = build_case(scenario, rng, cid, used_flights=used, flight_date=d, shared={"flight": flight})
    used.add((flight[0], d.isoformat()))
    return case


def run(start_day: int, days: int, per_day: int, weights: dict | None = None, *, seed: int = 7, workers: int = 8,
        adjusters: bool = False, log: Callable[[str], None] = print) -> dict:
    """adjusters: the adjuster personas decide every human task each hour (assisted and full mode)."""
    rng = random.Random(seed)
    keys, w = zip(*(weights or TRAFFIC_WEIGHTS).items())
    ids_all, errors, decisions = [], [], 0
    for day in range(start_day, start_day + days):
        per_hour = Counter(rng.choice(list(HOURS)) for _ in range(per_day))
        for hour in HOURS:
            n = per_hour.get(hour, 0)
            if not n:
                continue
            target = clock.at_day(day, hour)
            if target > clock.now():
                clock.set_time(target)
            ids, used = next_claim_ids(n), used_flights()
            for cid in ids:
                case = _new_case(rng, rng.choices(keys, w)[0], cid, used)
                case["claim"]["submitted_at"] = clock.now().strftime("%Y-%m-%dT%H:%M:%S")
                materialize(case)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                errors += [e for e in pool.map(_process, ids) if e]
            ids_all += ids
            if adjusters:
                from synthetic import adjuster_bot
                decisions += adjuster_bot.drain(ids_all)
        statuses = Counter(r["status"] for r in db.query(
            f"SELECT status FROM claims WHERE claim_id IN ({','.join('?' * len(ids_all))})", ids_all)) if ids_all else {}
        log(f"    day {day:+d}: {per_day} claims · total {len(ids_all)} · {dict(statuses.most_common(3))} · errors {len(errors)}"
            + (f" · {decisions} human decisions" if adjusters else ""))
    return {"claims": len(ids_all), "errors": errors, "ids": ids_all, "decisions": decisions}


def run_now(n: int, weights: dict, *, seed: int = 7, workers: int = 8) -> dict:
    """n claims submitted and processed at the current moment (shadow replays): the clock does not move."""
    rng = random.Random(seed)
    keys, w = zip(*weights.items())
    ids, used = next_claim_ids(n), used_flights()
    for cid in ids:
        case = _new_case(rng, rng.choices(keys, w)[0], cid, used)
        case["claim"]["submitted_at"] = clock.now().strftime("%Y-%m-%dT%H:%M:%S")
        materialize(case)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        errors = [e for e in pool.map(_process, ids) if e]
    return {"claims": len(ids), "errors": errors, "ids": ids}
