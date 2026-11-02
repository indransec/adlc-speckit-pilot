"""End-of-run report: did every injected fault get caught where sim/faults.yaml says it must?

Built only from the evidence ledger and the gate records, so it can be regenerated at any time and
checked against the same hash chain an auditor would verify. Writes <EVIDENCE_DIR>/e2e-report.md and .json.
"""
from __future__ import annotations

import json
from datetime import datetime

from shared import changes, clock, incidents, ledger, metrics, releases, usecase
from shared.config import evidence_root
from shared.faults import catalog


def _hours(a: str | None, b: str | None) -> float | None:
    return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600, 1) if a and b else None


def _day(ts: str | None) -> float | None:
    return round(clock.sim_day(datetime.fromisoformat(ts)), 1) if ts and clock.day0() else None


def detection_matrix() -> list[dict]:
    evs = ledger.events()
    incs = {i["id"]: i for i in incidents.incidents()}
    rows = []
    for fid, spec in catalog().items():
        exp = spec.get("expected", {})
        inj = next((e for e in evs if e["type"] == "fault_injected" and e.get("fault") == fid), None)
        det = next((e for e in evs if e["type"] == "fault_detected" and e.get("fault") == fid), None)
        inc = incs.get(det.get("incident")) if det else None
        checks = {"caught": det is not None}
        if det and exp.get("alert"):  # caught by its alert, routed to its team, closed with a root cause
            checks.update(alert=det.get("alert") == exp["alert"], team=bool(inc) and inc["team"] == exp.get("team"),
                          closed_with_rca=bool(inc) and inc["status"] == "closed" and bool(inc.get("rca")))
        elif det:                     # caught by a gate before it reached customers
            checks["gate"] = det.get("gate") == exp.get("caught_at")
        if exp.get("auto_action"):
            checks["auto_action"] = bool(inc and inc["actions"])
        if exp.get("escalation"):
            checks["escalated"] = bool(det and len(det.get("paged") or []) >= 2)
        rows.append({
            "fault": fid, "name": spec["name"], "kind": spec["kind"], "expected": exp,
            "injected_day": _day(inj["ts"]) if inj else None, "release": (inj or {}).get("release"),
            "caught_by": (det or {}).get("alert") or (det or {}).get("gate"), "incident": (det or {}).get("incident"),
            "team": inc["team"] if inc else exp.get("team"), "paged": (det or {}).get("paged") or [],
            "acknowledged_by": (det or {}).get("acknowledged_by"), "seconds_to_incident": (det or {}).get("seconds_to_incident"),
            "auto_actions": inc["actions"] if inc else [], "hours_open_to_resolved": _hours(inc["opened"], inc.get("resolved")) if inc else None,
            "rca": inc.get("rca") if inc else None, "checks": checks, "passed": all(checks.values()),
        })
    return rows


def gate_decisions() -> list[dict]:
    out = []
    for e in ledger.events():
        if e["type"] in ("gate_approved", "gate_rejected"):
            m = ledger.manifest(e["manifest_sha256"])
            out.append({"day": _day(e["ts"]), "gate": e["gate"], "release": e["release"], "decision": e["decision"],
                        "signed_by": ", ".join(a.get("name") or a.get("persona", "") for a in m["approvers"]),
                        "artifacts": len(m["artifacts"]), "manifest": e["manifest_sha256"][:12]})
    return out


def build() -> dict:
    matrix = detection_matrix()
    v = ledger.verify()
    return {"use_case": usecase.current_id(), "generated_at": clock.now_iso(), "sim_day": clock.sim_day(),
            "total": len(matrix), "passed": sum(r["passed"] for r in matrix), "matrix": matrix,
            "gates": gate_decisions(), "releases": releases.history(), "changes": changes.crs(),
            "incidents": incidents.incidents(), "kpis": metrics.kpis(), "ledger": v}


def matrix_lines(r: dict) -> list[str]:
    lines = [f"{'fault':5} {'caught by':26} {'team':13} {'paged':16} {'ack':9} result"]
    for x in r["matrix"]:
        lines.append(f"{x['fault']:5} {str(x['caught_by'] or '—'):26} {str(x['team'] or ''):13} "
                     f"{' → '.join(x['paged']) or '—':16} {str(x['acknowledged_by'] or '—'):9} "
                     f"{'✓' if x['passed'] else '✗ ' + ', '.join(k for k, ok in x['checks'].items() if not ok)}")
    return lines


def _md(r: dict) -> str:
    out = [f"# End-to-end ADLC simulation report: {r['use_case']}", "",
           f"Generated {r['generated_at']} (simulation day {r['sim_day']:.1f}). **{r['passed']} of {r['total']} faults caught "
           f"as expected.** Evidence ledger: {'intact' if r['ledger']['ok'] else 'BROKEN'} ({r['ledger']['events']} events, "
           f"{r['ledger']['manifests']} manifests, head `{r['ledger']['head'][:16]}`).", "",
           "## Detection matrix", "",
           "| Fault | Injected (day, release) | Expected | Caught by | Team | Paged | Acknowledged | Automatic action | Open→resolved (h) | Result |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for x in r["matrix"]:
        exp = x["expected"]
        out.append(f"| {x['fault']} {x['name']} | {x['injected_day']}, {x['release']} | {exp.get('caught_at')}"
                   f"{' / ' + exp['alert'] if exp.get('alert') else ''} | {x['caught_by'] or '—'} | {x['team'] or ''} | "
                   f"{' → '.join(x['paged']) or '—'} | {x['acknowledged_by'] or '—'} | {'; '.join(x['auto_actions']) or '—'} | "
                   f"{x['hours_open_to_resolved'] if x['hours_open_to_resolved'] is not None else '—'} | "
                   f"{'✅' if x['passed'] else '❌ ' + ', '.join(k for k, ok in x['checks'].items() if not ok)} |")
    out += ["", "## Gate decisions", "", "| Sim day | Gate | Release | Decision | Signed by | Artifacts | Manifest |", "|---|---|---|---|---|---|---|"]
    out += [f"| {g['day']} | {g['gate']} | {g['release']} | {g['decision']} | {g['signed_by']} | {g['artifacts']} | `{g['manifest']}` |"
            for g in r["gates"]]
    out += ["", "## Releases", "", "| Release | Commit | Cut by | Outcome |", "|---|---|---|---|"]
    out += [f"| {x['version']} | `{x['commit'][:10]}` | {x['by']} | "
            f"{(x['closed']['state'] + ': ' + x['closed']['reason']) if x['closed'] else 'active'} |" for x in r["releases"]]
    out += ["", "## Change requests", "", "| CR | Kind | Release | Status |", "|---|---|---|---|"]
    out += [f"| {c['id']} | {c['kind']} | {c['release']} | {c['status']} |" for c in r["changes"]]
    out += ["", "## Incidents", "", "| Incident | Alert | Severity | Team | Acknowledged | Root cause |", "|---|---|---|---|---|---|"]
    out += [f"| {i['id']} | {i['alert']} | {i['severity']} | {i['team']} | {i.get('ack_by') or '—'} | {i.get('rca') or i['status']} |"
            for i in r["incidents"]]
    out += ["", "## Value KPIs", "", "| KPI | Baseline | Value | Target | Met |", "|---|---|---|---|---|"]
    out += [f"| {k['id']} {k['name']} | {k['baseline']} | {k['value']} | {k['target']} | {k['met']} |" for k in r["kpis"]]
    return "\n".join(out) + "\n"


def write() -> dict:
    r = build()
    md, js = evidence_root() / "e2e-report.md", evidence_root() / "e2e-report.json"
    md.write_text(_md(r))
    js.write_text(json.dumps(r, indent=2, default=str))
    r["path"] = str(md)
    return r
