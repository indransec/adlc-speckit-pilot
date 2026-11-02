"""ADLC board: where each work item stands in the lifecycle, derived from the gate engine and the ledger.

Nothing here is stored separately. Columns come from gate records (the gate engine), history and
artifact counts from the evidence ledger, so the board cannot disagree with either. Cards move only
when a gate is approved; there is no drag and drop.

  epic            the use case: G0-G2 once, then it follows its active release
  release         one card per release (G3 onward); a release rejected and replaced is closed where it stopped
  change request  CAB changes for a release (deploy in shadow, go live = the G5 sign-off, phase change, emergency)
  incident        one per firing alert, in the column of the phase it fired in (shadow G5, assisted Rollout, full G6)
"""
from __future__ import annotations

import time
from datetime import datetime

from shared import changes, clock, incidents, ledger, lifecycle, releases, usecase
from shared.config import release

COLUMNS = [("Backlog", "Backlog"), ("G0", "G0 · Intake"), ("G1", "G1 · Requirements"), ("G2", "G2 · Design"),
           ("G3", "G3 · Test & evals"), ("G4", "G4 · RAI"), ("G5", "G5 · Deploy + shadow"), ("Rollout", "Rollout"),
           ("G6", "G6 · Hypercare"), ("G7", "G7 · Improve / operate"), ("Done", "Done / retired")]
RELEASE_GATES = ["G3", "G4", "G5", "G6", "G7"]
DECISIONS = ("gate_approved", "gate_rejected")
_cache: dict = {}


def _check(gate: str) -> dict:
    """Gate checks are not free (G2 runs the manifest validator): reuse a result for a few seconds."""
    key = (gate, release())
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < 5:
        return hit[1]
    st = lifecycle.check_gate(gate)
    _cache[key] = (time.monotonic(), st)
    return st


def _days_since(ts: str) -> float:
    return round((datetime.fromisoformat(clock.now_iso()) - datetime.fromisoformat(ts)).total_seconds() / 86400, 1)


def _gate_progress(card: dict, gate: str) -> None:
    """Checks, signatures and sub-status of the current release's open gate."""
    st = _check(gate)
    rec = st["record"]
    sigs = rec.get("signatures") or []
    card.update(column=gate, gate=gate, gate_status=rec["status"], checks_passed=sum(c["ok"] for c in st["checks"]),
                checks_total=len(st["checks"]), signed=[s["role"] for s in sigs], approvers_required=st["approvers_required"],
                sub_status="Awaiting approval" if st["ready"] else "Doing", failing=[c["check"] for c in st["checks"] if not c["ok"]])
    if rec["status"] == "rejected":
        card.update(blocked=True, reason=rec.get("notes") or "rejected", sub_status="Rejected")
        if gate == "G6":
            _mark_extended(card, release())


def _mark_extended(card: dict, rel: str) -> None:
    ext = [e for e in ledger.events() if e["type"] == "hypercare_extended" and e.get("item") == rel]
    if ext:
        card.update(sub_status=f"Hypercare extended to day {ext[-1]['until_day']:g}", reason=ext[-1]["conditions"], blocked=True)


def _production_release() -> str | None:
    """The release serving customers: the last one deployed in full mode."""
    full = [e["item"] for e in changes.deployments() if e["phase"] == "full"]
    return full[-1] if full else None


def epic(uc: str | None = None) -> dict | None:
    uc = uc or usecase.current_id()
    evs = ledger.for_item(uc)
    created = next((e for e in evs if e["type"] == "use_case_created"), None)
    if created is None:
        return None
    summary = usecase.summary(uc)
    prod = _production_release()  # once live, the use case follows production, not a newer candidate release
    statuses = {g["gate"]: g for g in lifecycle.records(prod)["gates"]}
    current = next((g for g in lifecycle.GATE_IDS if statuses[g]["status"] not in lifecycle.APPROVED), None)
    moved = [e for e in evs if e["type"] in ("use_case_submitted", "gate_approved")]
    card = {"id": uc, "type": "epic", "title": created["title"], "tier": summary.get("tier"), "release": prod or release(),
            "artifacts": sum(e.get("artifacts", 0) for e in evs if e["type"] == "gate_approved"),
            "drift": ledger.drift(item=uc), "blocked": False, "reason": ""}
    if not summary["submitted"]:
        card.update(column="Backlog", sub_status="Draft", gate=None)
    elif current is None:
        card.update(column="G7", sub_status="In operation", gate=None,
                    next_release=release() if release() != prod else None)
    elif prod and prod != release():
        card.update(column=current, gate=current, sub_status=statuses[current]["status"])
        if current == "G6" and statuses["G6"]["status"] == "rejected":
            _mark_extended(card, prod)
    else:
        _gate_progress(card, current)
        if current == "G6" and _deployed_phase(release()) == "assisted":
            card.update(column="Rollout", sub_status="Assisted")
    card["days_in_column"] = _days_since(moved[-1]["ts"] if moved else created["ts"])
    return card


def _deployed_phase(rel: str) -> str | None:
    d = changes.deployments(rel)
    return d[-1]["phase"] if d else None


def release_card(r: dict) -> dict:
    v = r["version"]
    gates = {g["gate"]: g for g in lifecycle.records(v)["gates"]}
    current = next((g for g in RELEASE_GATES if gates[g]["status"] not in lifecycle.APPROVED), None)
    evs = [e for e in ledger.events() if e.get("item") == v or (e["type"] in DECISIONS and e.get("release") == v)]
    card = {"id": v, "type": "release", "title": r["message"], "commit": r["commit"][:10], "release": v, "by": r["by"],
            "artifacts": sum(e.get("artifacts", 0) for e in evs if e["type"] == "gate_approved"),
            "phase": _deployed_phase(v), "drift": [], "blocked": False, "reason": "", "gate": current,
            "gate_status": gates[current]["status"] if current else None}
    if r["closed"]:
        where = current or "G7"
        card.update(column=where, sub_status=f"Closed · {r['closed']['state'].replace('_', ' ')}", blocked=True,
                    reason=r["closed"]["reason"], closed=True)
    elif current is None:
        card.update(column="G7", sub_status="In operation")
    elif v == release():
        _gate_progress(card, current)
        if current == "G6" and card["phase"] == "assisted":
            card.update(column="Rollout", sub_status="Assisted")
    else:
        card.update(column=current, sub_status=gates[current]["status"])
    last_move = [e for e in evs if e["type"] in ("release_created", "gate_approved", "deployed")]
    card["days_in_column"] = _days_since(last_move[-1]["ts"])
    return card


def cr_card(c: dict) -> dict:
    return {"id": c["id"], "type": "change", "title": c["title"], "release": c["release"], "kind": c["kind"],
            "column": "G5", "sub_status": c["status"].capitalize(), "approvers_required": c["approvers"],
            "signed": [s["role"] for s in c["signatures"]], "blocked": c["status"] == "rejected",
            "reason": c.get("notes") or ("rejected at G5" if c.get("via") else ""), "drift": [], "artifacts": 0,
            "days_in_column": _days_since(c.get("decided") or c["raised"])}


INCIDENT_COLUMN = {"shadow": "G5", "assisted": "Rollout", "full": "G6"}


def incident_card(i: dict) -> dict:
    return {"id": i["id"], "type": "incident", "title": f"{i['alert']}: {i['summary']}", "release": i["release"],
            "column": INCIDENT_COLUMN.get(i["mode"], "G6"), "sub_status": i["status"].capitalize(), "severity": i["severity"],
            "team": i["team"], "ack_by": i.get("ack_by"), "blocked": i["status"] in ("open", "acknowledged"),
            "reason": "; ".join(i["actions"]), "drift": [], "artifacts": 0,
            "days_in_column": _days_since(i.get("closed") or i.get("resolved") or i["opened"])}


def board() -> dict:
    items = [c for c in [epic()] if c]
    items += [release_card(r) for r in releases.history()]
    items += [cr_card(c) for c in changes.crs()]
    items += [incident_card(i) for i in incidents.incidents()]
    return {"columns": [{"id": k, "title": t} for k, t in COLUMNS], "items": items, "clock": clock.state(),
            "ledger_head": ledger.head()}


def _decisions(pred) -> list[dict]:
    out = []
    for e in ledger.events():
        if e["type"] in DECISIONS and pred(e):
            m = ledger.manifest(e["manifest_sha256"])
            out.append({"seq": e["seq"], "gate": e["gate"], "release": e["release"], "decision": e["decision"],
                        "decided_at": m["decided_at"], "manifest_sha256": e["manifest_sha256"],
                        "approvers": m["approvers"], "notes": m["notes"], "checks": m["checks"], "missing": m["missing"],
                        "artifacts": [{**a, "status": ledger.live_status(a)} for a in m["artifacts"]]})
    return out


def item(item_id: str) -> dict:
    uc = usecase.current_id()
    if item_id == uc:
        card = epic(uc)
        if card is None:
            raise KeyError(f"no work item {item_id}")
        return {"card": card, "current_gate": _check(card["gate"]) if card.get("gate") else None,
                "decisions": _decisions(lambda e: e.get("item") == uc), "history": ledger.for_item(uc),
                "usecase": usecase.summary(uc)}
    rel = next((r for r in releases.history() if r["version"] == item_id), None)
    if rel:
        card = release_card(rel)
        current = _check(card["gate"]) if card.get("gate") and item_id == release() and not card.get("closed") else None
        history = [e for e in ledger.events() if e.get("item") == item_id or e.get("release") == item_id
                   or e.get("parent") == item_id]
        return {"card": card, "current_gate": current, "history": history,
                "decisions": _decisions(lambda e: e.get("release") == item_id)}
    cr = next((c for c in changes.crs() if c["id"] == item_id), None)
    if cr:
        return {"card": cr_card(cr), "current_gate": None, "decisions": [], "history": ledger.for_item(item_id)}
    inc = next((i for i in incidents.incidents() if i["id"] == item_id), None)
    if inc:
        return {"card": incident_card(inc), "incident": inc, "current_gate": None, "decisions": [],
                "history": ledger.for_item(item_id)}
    raise KeyError(f"no work item {item_id}")
