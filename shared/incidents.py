"""Incidents: one per firing alert, opened by the Alertmanager webhook (POST /webhooks/alertmanager).

  opened        the alert fired; Sev1 automatic actions apply at once (kill switch, straight-through off)
  acknowledged  the on-call persona took it (in GoAlert, or here)
  resolved      Alertmanager reported the alert cleared
  closed        a person recorded the root cause; only after it is resolved

Every step is an event in the evidence ledger, so incidents sit beside gate decisions in the audit trail.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

from shared import clock, ledger, teams
from shared.config import release
from shared.runtime import get_runtime, set_runtime

AUTO_ACTIONS = {
    "kill_switch": ("kill_switch", True, "kill switch on: straight-through and payment instructions stopped"),
    "stp_off": ("straight_through_enabled", False, "straight-through processing off: every claim goes to an adjuster"),
}


log = logging.getLogger(__name__)


class IncidentError(ValueError):
    pass


def _inform(inc: dict) -> None:
    """Email the people named in also_inform (Mailpit in the simulation stack); best effort, never blocks the incident."""
    host, _, port = os.environ.get("SIM_SMTP", "localhost:1025").partition(":")
    for pid in inc["inform"]:
        try:
            p = teams.persona(pid)
            msg = EmailMessage()
            msg["From"], msg["To"] = "incidents@claims.sim", p["email"]
            msg["Subject"] = f"[{inc['severity'].upper()}] {inc['id']} {inc['alert']} - for your information"
            msg.set_content(f"{inc['summary']}\nValue: {inc['value']}\nOwning team: {inc['team']}\nRelease: {inc['release']}\n"
                            f"Actions taken: {'; '.join(inc['actions']) or 'none'}\n")
            with smtplib.SMTP(host, int(port or 25), timeout=5) as smtp:
                smtp.send_message(msg)
        except Exception as e:
            log.warning("inform %s about %s: %s", pid, inc["id"], e)


def incidents() -> list[dict]:
    out: dict[str, dict] = {}
    for e in ledger.events():
        t, i = e["type"], e.get("item")
        if t == "incident_opened":
            out[i] = {"id": i, "alert": e["alert"], "severity": e["severity"], "team": e["team"], "release": e["release"],
                      "mode": e["mode"], "summary": e["summary"], "value": e.get("value"), "fingerprint": e["fingerprint"],
                      "inform": e.get("inform", []), "opened": e["ts"], "status": "open", "actions": [], "timeline": []}
        if i not in out or not t.startswith("incident_"):
            continue
        inc = out[i]
        inc["timeline"].append({"at": e["ts"], "event": t[9:], "by": e.get("by"), "note": e.get("note", "")})
        if t == "incident_action":
            inc["actions"].append(e["note"])
        elif t == "incident_acknowledged":
            inc.update(status="acknowledged" if inc["status"] == "open" else inc["status"], acknowledged=e["ts"], ack_by=e["by"])
        elif t == "incident_resolved":
            inc.update(status="resolved", resolved=e["ts"])
        elif t == "incident_closed":
            inc.update(status="closed", closed=e["ts"], closed_by=e["by"], rca=e["note"])
    return list(out.values())


def get(inc_id: str) -> dict:
    for i in incidents():
        if i["id"] == inc_id:
            return i
    raise IncidentError(f"unknown incident {inc_id}")


def find(alert: str, *, open_only: bool = True, opened_after: str | None = None) -> dict | None:
    hits = [i for i in incidents() if i["alert"] == alert and (not open_only or i["status"] != "closed")
            and (opened_after is None or i["opened"] >= opened_after)]
    return hits[-1] if hits else None


def from_alertmanager(payload: dict) -> list[dict]:
    """Open an incident per newly firing alert (with its automatic action) and mark resolved ones."""
    changed = []
    with ledger.locked():
        current = {i["fingerprint"]: i for i in incidents() if i["status"] != "closed"}
        for a in payload.get("alerts", []):
            fp, labels, ann = a["fingerprint"], a.get("labels", {}), a.get("annotations", {})
            inc = current.get(fp)
            if a["status"] == "firing" and inc is None:
                inc_id = f"INC-{sum(e['type'] == 'incident_opened' for e in ledger.events()) + 1:03d}"
                ledger.append("incident_opened", item=inc_id, parent=labels.get("release") or release(), alert=labels.get("alertname"),
                              severity=labels.get("severity"), team=labels.get("team"), release=labels.get("release") or release(),
                              mode=labels.get("mode") or get_runtime()["mode"], summary=ann.get("summary", ""), value=ann.get("value"),
                              fingerprint=fp, inform=[x for x in ann.get("inform", "").split(",") if x], by="alertmanager")
                act = AUTO_ACTIONS.get(labels.get("auto_action", ""))
                if act:
                    set_runtime(act[0], act[1], by=f"incident:{inc_id}")
                    ledger.append("incident_action", item=inc_id, by="automation", note=act[2])
                current[fp] = get(inc_id)
                _inform(current[fp])
                changed.append(current[fp])
            elif a["status"] == "resolved" and inc is not None and inc["status"] in ("open", "acknowledged"):
                ledger.append("incident_resolved", item=inc["id"], by="alertmanager", note="alert cleared")
                changed.append(get(inc["id"]))
    return changed


def acknowledge(inc_id: str, persona_id: str, note: str = "") -> dict:
    inc, p = get(inc_id), teams.persona(persona_id)
    on_call = next((t.get("on_call", []) for t in teams.teams() if t["id"] == inc["team"]), [])
    if persona_id not in on_call:
        raise IncidentError(f"{p['name']} is not on call for {inc['team']}")
    if inc["status"] not in ("open", "resolved") or inc.get("ack_by"):
        raise IncidentError(f"{inc_id} is already {inc['status']}" + (f" (acknowledged by {inc['ack_by']})" if inc.get("ack_by") else ""))
    ledger.append("incident_acknowledged", item=inc_id, by=persona_id, note=note)
    return get(inc_id)


def note(inc_id: str, persona_id: str, text: str) -> dict:
    teams.persona(persona_id)
    ledger.append("incident_action", item=inc_id, by=persona_id, note=text)
    return get(inc_id)


def close(inc_id: str, persona_id: str, rca: str) -> dict:
    inc = get(inc_id)
    teams.persona(persona_id)
    if inc["status"] != "resolved":
        raise IncidentError(f"{inc_id} is {inc['status']}: close it once the alert has cleared")
    if not rca.strip():
        raise IncidentError("a root cause is required to close an incident")
    ledger.append("incident_closed", item=inc_id, by=persona_id, note=rca)
    return get(inc_id)


def summary() -> dict:
    inc = incidents()
    return {"open": [i["id"] for i in inc if i["status"] != "closed"], "total": len(inc),
            "by_severity": {s: sum(i["severity"] == s for i in inc) for s in ("sev1", "sev2", "sev3")}, "at": clock.now_iso()}
