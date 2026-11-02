"""Prometheus metrics for alerting (served by the API at /metrics).

Every gauge is computed at scrape time from the system of record over a window of BUSINESS time:
"the last day" means the last 24 hours on the simulation clock during a simulation run. Prometheus
evaluates the alert rules generated from observability/alerts.yaml against these gauges, so a simulated
day of traffic can raise and clear real alerts while the calendar runs much faster than the wall clock.
"""
from __future__ import annotations

import json
from datetime import timedelta

from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from shared import clock, db
from shared.config import evidence_root, release, system_manifest
from shared.runtime import get_runtime

DAY = timedelta(days=1)


def _since(delta: timedelta) -> str:
    return (clock.now() - delta).isoformat(timespec="seconds")


def _window(days_back_start: int, days_back_end: int) -> tuple[str, str]:
    now = clock.now()
    return ((now - DAY * days_back_start).isoformat(timespec="seconds"),
            (now - DAY * days_back_end).isoformat(timespec="seconds"))


def _p95(xs: list[float]) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))] if len(xs) >= 5 else None


MIN_BASELINE_DAYS = 5  # a spike needs a week-long baseline built from days that actually had traffic


def _daily_rate(count_sql: str, day: str, base_from: str, base_to: str) -> tuple:
    """(count today, per-claim rate today, per-claim rate over the previous 7 days or None if too few traffic days)."""
    now = clock.now_iso() + "~"
    claims = "SELECT COUNT(*) FROM claims WHERE historical = 0 AND package_at >= ? AND package_at < ?"
    n_today, c_today = db.scalar(count_sql, [day, now]) or 0, db.scalar(claims, [day, now]) or 0
    n_base, c_base = db.scalar(count_sql, [base_from, base_to]) or 0, db.scalar(claims, [base_from, base_to]) or 0
    days = db.scalar("SELECT COUNT(DISTINCT substr(package_at, 1, 10)) FROM claims WHERE historical = 0 "
                     "AND package_at >= ? AND package_at < ?", [base_from, base_to]) or 0
    return (n_today, round(n_today / c_today, 4) if c_today else None,
            round(n_base / c_base, 4) if c_base and days >= MIN_BASELINE_DAYS else None)


def values() -> dict[str, float | None]:
    """Current value of every alert input (None = not enough data, the gauge is left out)."""
    day = _since(DAY)
    base_from, base_to = _window(8, 1)
    limit = float(system_manifest()["routing"]["stp_limit_usd"])
    v: dict[str, float | None] = {}
    v["adlc_payments_without_valid_token"] = db.scalar(
        "SELECT COUNT(*) FROM payments WHERE token_verified != 1 AND created_at >= ?", [day]) or 0
    v["adlc_stp_over_authority"] = db.scalar(
        "SELECT COUNT(*) FROM claims WHERE decided_by = 'AGT-TRV-ORCH' AND approved_usd > ? AND package_at >= ?",
        [limit, day]) or 0
    a = db.one("SELECT COUNT(*) n, AVG(override) r FROM approvals WHERE level IN ('L1', 'L2') AND decided_at >= ?", [day])
    v["adlc_human_decisions"] = a["n"] or 0
    v["adlc_override_rate"] = round(a["r"], 4) if a["n"] else None
    flags = _daily_rate("SELECT COUNT(*) FROM claims WHERE fraud_risk IN ('medium', 'high') AND historical = 0 "
                        "AND package_at >= ? AND package_at < ?", day, base_from, base_to)
    v["adlc_fraud_flags"], v["adlc_fraud_flag_rate"], v["adlc_fraud_flag_rate_baseline"] = flags
    v["adlc_decision_latency_p95_seconds"] = _p95([r["package_latency_s"] for r in db.query(
        "SELECT package_latency_s FROM claims WHERE package_at >= ? AND historical = 0", [day])])
    cost = "SELECT SUM(cost_usd) c, COUNT(DISTINCT claim_id) n FROM llm_calls WHERE ts >= ? AND ts < ? AND claim_id IS NOT NULL"
    today, base = db.one(cost, [day, clock.now_iso() + "~"]), db.one(cost, [base_from, base_to])
    v["adlc_cost_per_claim_usd"] = round(today["c"] / today["n"], 5) if today["n"] else None
    v["adlc_cost_per_claim_baseline_usd"] = round(base["c"] / base["n"], 5) if base["n"] else None
    v["adlc_injection_blocks"], v["adlc_injection_block_rate"], v["adlc_injection_block_rate_baseline"] = _daily_rate(
        "SELECT COUNT(*) FROM guardrail_events WHERE guardrail = 'document-injection' AND ts >= ? AND ts < ?",
        day, base_from, base_to)
    v["adlc_shadow_route_agreement"] = v["adlc_shadow_route_threshold"] = None
    p = evidence_root() / release() / "shadow-report.json"
    if get_runtime()["mode"] == "shadow" and p.exists():
        rep = json.loads(p.read_text())
        v["adlc_shadow_route_agreement"] = rep["route_agreement"]
        v["adlc_shadow_route_threshold"] = rep["thresholds"]["route"]
    v["adlc_sim_day"] = clock.sim_day()
    return v


def tool_values() -> list[dict]:
    """Per tool: one failing dependency must stand out, not disappear in the average over all tools."""
    return db.query("SELECT tool_id, COUNT(*) calls, ROUND(1.0 * SUM(status = 'error') / COUNT(*), 4) error_rate "
                    "FROM tool_calls WHERE ts >= ? GROUP BY tool_id", [_since(DAY)])


class AdlcCollector(Collector):
    def collect(self):
        rel, rt = release(), get_runtime()
        for name, value in values().items():
            if value is None:
                continue
            g = GaugeMetricFamily(name, name.replace("adlc_", "").replace("_", " "), labels=["release", "mode"])
            g.add_metric([rel, rt["mode"]], float(value))
            yield g
        calls = GaugeMetricFamily("adlc_tool_calls", "tool calls in the last day", labels=["release", "mode", "tool"])
        errors = GaugeMetricFamily("adlc_tool_error_rate", "tool error rate in the last day", labels=["release", "mode", "tool"])
        for t in tool_values():
            calls.add_metric([rel, rt["mode"], t["tool_id"]], float(t["calls"]))
            errors.add_metric([rel, rt["mode"], t["tool_id"]], float(t["error_rate"]))
        yield calls
        yield errors
        for flag in ("kill_switch", "straight_through_enabled", "payments_enabled"):
            g = GaugeMetricFamily(f"adlc_runtime_{flag}", f"runtime flag {flag}", labels=["release", "mode"])
            g.add_metric([rel, rt["mode"]], 1.0 if rt[flag] else 0.0)
            yield g
