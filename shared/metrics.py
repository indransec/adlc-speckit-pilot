"""Observability read models: KPIs (docs/kpis.yaml), SLOs (observability/slos.yaml), alerts
(observability/alerts.yaml) and the dashboard panels in observability/dashboards/claims-agent.md.

Simulation assumptions (human effort is not real time in a demo) are explicit constants.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime

from shared import db
from shared.config import load_yaml
from shared.runtime import get_runtime

DECIDED = ("closed_paid", "closed_rejected", "approved", "payment_on_hold", "siu_referred")
# Simulated human effort per decision (minutes of adjuster work, hours of elapsed handling)
ADJUSTER_MINUTES = {"L1": 12, "L2": 15, "SIU": 45, "INVESTIGATION": 30}
ELAPSED_HOURS = {"L1": 6, "L2": 10, "SIU": 72, "INVESTIGATION": 24}
BASELINE = {"handling_days": 5.5, "adjuster_minutes": 45, "qa_accuracy": 0.95}   # synthetic baselines
ROW_LABEL = {1: "1 STP", 2: "2 L1 type A", 3: "3 L1 authority", 4: "4 L1+L2 significant", 5: "5 L1+L2 rejection",
             6: "6 investigation", 7: "7 SIU"}


def _claims(where: str = "", params=()) -> list[dict]:
    return db.query(f"SELECT * FROM claims WHERE historical = 0 {where}", params)


def _p95(xs: list[float]) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]


def overview() -> dict:
    cs = _claims()
    by_status = Counter(c["status"] for c in cs)
    decided = [c for c in cs if c["status"] in DECIDED]
    return {"claims": len(cs), "by_status": dict(by_status), "decided": len(decided),
            "in_queue": sum(by_status[s] for s in ("awaiting_l1", "awaiting_l2", "awaiting_investigation", "awaiting_siu")),
            "claimed_usd": round(sum(c["claimed_usd"] or 0 for c in cs), 2),
            "paid_usd": round(db.scalar("SELECT COALESCE(SUM(amount_usd),0) FROM payments") or 0, 2),
            "processed": sum(1 for c in cs if c["route"])}


def routing_mix() -> list[dict]:
    cs = [c for c in _claims() if c["route_row"]]
    n = len(cs) or 1
    cnt = Counter(c["route_row"] for c in cs)
    return [{"row": r, "label": ROW_LABEL[r], "claims": cnt.get(r, 0), "pct": round(100 * cnt.get(r, 0) / n, 1)}
            for r in range(1, 8)]


def _approvals() -> list[dict]:
    return db.query("SELECT a.*, c.route_row FROM approvals a JOIN claims c USING(claim_id)")


def override_rate() -> float | None:
    hs = [a for a in _approvals() if a["level"] in ("L1", "L2")]
    return round(sum(a["override"] for a in hs) / len(hs), 3) if hs else None


def kpis() -> list[dict]:
    decided = _claims(f"AND status IN ({','.join('?' * len(DECIDED))})", DECIDED)
    appr = defaultdict(list)
    for a in _approvals():
        appr[a["claim_id"]].append(a)

    def queue_of(a):
        return "INVESTIGATION" if a["level"] == "L1" and a["route_row"] == 6 else a["level"]

    handling, minutes = [], []
    for c in decided:
        hs = appr.get(c["claim_id"], [])
        handling.append(((c["package_latency_s"] or 0) / 3600 + sum(ELAPSED_HOURS[queue_of(a)] for a in hs)) / 24)
        minutes.append(sum(ADJUSTER_MINUTES[queue_of(a)] for a in hs))
    stp = [c for c in decided if c["decided_by"] == "AGT-TRV-ORCH"]
    qa = []
    for c in decided:
        gt = db.jload(c["ground_truth"], {})
        if gt.get("final_amount") is not None and c["status"] != "siu_referred":
            qa.append(abs((c["approved_usd"] or 0) - gt["final_amount"]) <= 1.0)
    ovr = override_rate()
    n = len(decided) or 1
    return [
        {"id": "KPI-01", "name": "Average handling time (days, simulated)", "baseline": BASELINE["handling_days"],
         "value": round(statistics.mean(handling), 2) if handling else None, "target": "-40%",
         "met": bool(handling) and statistics.mean(handling) <= 0.6 * BASELINE["handling_days"]},
        {"id": "KPI-02", "name": "Straight-through processing rate", "baseline": 0.0,
         "value": round(len(stp) / n, 3) if decided else None, "target": "15-25%",
         "met": bool(decided) and 0.15 <= len(stp) / n},
        {"id": "KPI-03", "name": "Adjuster minutes per claim (simulated)", "baseline": BASELINE["adjuster_minutes"],
         "value": round(statistics.mean(minutes), 1) if minutes else None, "target": "-30%",
         "met": bool(minutes) and statistics.mean(minutes) <= 0.7 * BASELINE["adjuster_minutes"]},
        {"id": "KPI-04", "name": "Payment accuracy (QA vs ground truth)", "baseline": BASELINE["qa_accuracy"],
         "value": round(sum(qa) / len(qa), 3) if qa else None, "target": ">= baseline",
         "met": bool(qa) and sum(qa) / len(qa) >= BASELINE["qa_accuracy"]},
        {"id": "KPI-05", "name": "Human override rate", "baseline": None, "value": ovr, "target": "< 15%",
         "met": ovr is not None and ovr < 0.15},
        {"id": "KPI-06", "name": "Complaints per 1,000 claims", "baseline": None, "value": None,
         "target": "<= baseline", "met": None, "note": "not simulated"},
    ]


def llm_usage() -> dict:
    by_agent = db.query("SELECT agent_id, model, mode, COUNT(*) calls, SUM(prompt_tokens) prompt_tokens, "
                        "SUM(completion_tokens) completion_tokens, ROUND(SUM(cost_usd), 5) cost_usd, "
                        "ROUND(AVG(latency_ms), 1) avg_latency_ms, SUM(degraded) degraded, "
                        "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) errors "
                        "FROM llm_calls GROUP BY agent_id, model, mode ORDER BY agent_id")
    processed = db.scalar("SELECT COUNT(DISTINCT claim_id) FROM llm_calls WHERE claim_id IS NOT NULL") or 0
    total = db.scalar("SELECT COALESCE(SUM(cost_usd),0) FROM llm_calls") or 0
    return {"by_agent": by_agent, "total_cost_usd": round(total, 5), "claims": processed,
            "cost_per_claim_usd": round(total / processed, 5) if processed else None}


def slos() -> list[dict]:
    lat = [c["package_latency_s"] for c in _claims("AND package_latency_s IS NOT NULL")]
    runs = _claims("AND run_no > 0")
    errors = sum(1 for c in runs if c["status"] == "error")
    usage = llm_usage()
    budget = get_runtime()["max_cost_per_claim_usd"]

    def turnaround(level):
        xs = []
        for a in db.query("SELECT queued_at, decided_at FROM approvals WHERE level = ? AND queued_at IS NOT NULL", [level]):
            xs.append((datetime.fromisoformat(a["decided_at"]) - datetime.fromisoformat(a["queued_at"])).total_seconds() / 3600)
        return round(statistics.mean(xs), 3) if xs else None

    p95 = _p95(lat)
    avail = 1 - errors / len(runs) if runs else None
    l1, l2 = turnaround("L1"), turnaround("L2")
    return [
        {"id": "slo:decision-latency", "target": "p95 <= 10 min", "value": f"{p95:.1f}s" if p95 is not None else None,
         "met": p95 is None or p95 <= 600},
        {"id": "slo:availability", "target": "99.5%", "value": f"{100 * avail:.2f}%" if avail is not None else None,
         "met": avail is None or avail >= 0.995},
        {"id": "slo:cost-per-claim", "target": f"<= USD {budget}", "value": usage["cost_per_claim_usd"],
         "met": usage["cost_per_claim_usd"] is None or usage["cost_per_claim_usd"] <= budget},
        {"id": "slo:l1-turnaround", "target": "1 business day", "value": f"{l1}h" if l1 is not None else None,
         "met": l1 is None or l1 <= 8},
        {"id": "slo:l2-turnaround", "target": "1 business day after L1", "value": f"{l2}h" if l2 is not None else None,
         "met": l2 is None or l2 <= 8},
    ]


def controls() -> dict:
    return {"payments_total": db.scalar("SELECT COUNT(*) FROM payments") or 0,
            "payments_without_valid_token": db.scalar("SELECT COUNT(*) FROM payments WHERE token_verified != 1") or 0,
            "events": db.query("SELECT control, outcome, COUNT(*) n FROM control_events GROUP BY 1, 2 ORDER BY 1, 2"),
            "type_b_mislabelled_as_a": 0, "type_b_mislabel_note": "structurally prevented: type A only from rules engine"}


def guardrails() -> list[dict]:
    return db.query("SELECT guardrail, action, COUNT(*) n FROM guardrail_events GROUP BY 1, 2 ORDER BY 3 DESC")


def alerts() -> list[dict]:
    defs = {a["name"]: a for a in load_yaml("observability/alerts.yaml")["alerts"]}
    ctl, usage, budget = controls(), llm_usage(), get_runtime()["max_cost_per_claim_usd"]
    lat = _p95([c["package_latency_s"] for c in _claims("AND package_latency_s IS NOT NULL")])
    today = db.query("SELECT substr(decided_at,1,10) d, AVG(override) r, COUNT(*) n FROM approvals "
                     "WHERE level IN ('L1','L2') GROUP BY 1 ORDER BY 1 DESC LIMIT 1")
    flags = db.query("SELECT substr(package_at,1,10) d, SUM(CASE WHEN fraud_risk IN ('medium','high') THEN 1 ELSE 0 END) f "
                     "FROM claims WHERE package_at IS NOT NULL GROUP BY 1 ORDER BY 1 DESC LIMIT 8")
    prior = [r["f"] for r in flags[1:]]
    fraud_fire = bool(prior) and flags[0]["f"] > 2 * (sum(prior) / len(prior))
    vals = {
        "payment_without_token": (ctl["payments_without_valid_token"], ctl["payments_without_valid_token"] > 0),
        "override_rate_high": (today[0]["r"] if today else None, bool(today) and today[0]["n"] >= 5 and today[0]["r"] > 0.2),
        "decision_latency": (lat, lat is not None and lat > 600),
        "cost_spike": (usage["cost_per_claim_usd"], (usage["cost_per_claim_usd"] or 0) > 1.5 * budget),
        "fraud_flag_spike": (flags[0]["f"] if flags else None, fraud_fire),
    }
    return [{**defs[k], "value": v, "firing": f} for k, (v, f) in vals.items() if k in defs]


def fairness() -> dict:
    decided = _claims(f"AND status IN ({','.join('?' * len(DECIDED))})", DECIDED)
    out = {}
    for seg in ("nationality_group", "destination_region", "age_band", "channel"):
        groups = defaultdict(list)
        for c in decided:
            groups[c[seg]].append(c)
        rows = []
        for g, cs in sorted(groups.items()):
            paid = [c for c in cs if (c["approved_usd"] or 0) > 0]
            ded = [100 * (1 - (c["approved_usd"] or 0) / c["claimed_usd"]) for c in paid if c["claimed_usd"]]
            rows.append({"group": g, "claims": len(cs), "approval_rate": round(len(paid) / len(cs), 3),
                         "avg_deduction_pct": round(statistics.mean(ded), 1) if ded else 0.0,
                         "stp_rate": round(sum(c["decided_by"] == "AGT-TRV-ORCH" for c in cs) / len(cs), 3)})
        rates = [r["approval_rate"] for r in rows if r["claims"] >= 3]
        out[seg] = {"groups": rows, "approval_gap_pts": round(100 * (max(rates) - min(rates)), 1) if len(rates) > 1 else 0.0}
    return out


def calibration() -> list[dict]:
    """Confidence vs agreement of the agent recommendation with the final human/agent decision."""
    rows = db.query("SELECT confidence, recommended_usd, approved_usd, recommendation, status FROM claims "
                    "WHERE historical = 0 AND confidence IS NOT NULL AND status IN ('closed_paid','closed_rejected','approved','payment_on_hold')")
    bins = defaultdict(list)
    for r in rows:
        agree = abs((r["recommended_usd"] or 0) - (r["approved_usd"] or 0)) <= 1.0
        b = min(int(r["confidence"] * 10), 9) / 10
        bins[b].append(agree)
    return [{"confidence_bin": f"{b:.1f}-{b + 0.1:.1f}", "claims": len(v), "agreement": round(sum(v) / len(v), 3)}
            for b, v in sorted(bins.items())]


def shadow_agreement() -> dict:
    cs = _claims("AND status = 'shadow_complete'")
    route_ok, amt_ok, n = 0, 0, 0
    for c in cs:
        gt = db.jload(c["ground_truth"], {})
        if not gt.get("route"):
            continue
        n += 1
        route_ok += c["route"] == gt["route"]
        amt_ok += gt.get("amount_check") is False or gt.get("amount") is None or abs((c["recommended_usd"] or 0) - gt["amount"]) <= 1
    return {"claims": n, "route_agreement": round(route_ok / n, 3) if n else None,
            "amount_agreement": round(amt_ok / n, 3) if n else None}


def queues_summary() -> list[dict]:
    rows = db.query("SELECT queue, COUNT(*) n, MIN(queue_since) oldest FROM claims WHERE queue IS NOT NULL "
                    "AND (status LIKE 'awaiting_%' OR status = 'manual_queue') GROUP BY queue")
    return rows


def override_reasons(limit: int = 5) -> list[dict]:
    return db.query("SELECT level, decision, notes, COUNT(*) n FROM approvals WHERE override = 1 "
                    "GROUP BY level, decision, notes ORDER BY n DESC LIMIT ?", [limit])


def dashboard() -> dict:
    return {"overview": overview(), "routing_mix": routing_mix(), "kpis": kpis(), "slos": slos(), "alerts": alerts(),
            "llm": llm_usage(), "guardrails": guardrails(), "controls": controls(), "fairness": fairness(),
            "calibration": calibration(), "shadow": shadow_agreement(), "queues": queues_summary()}
