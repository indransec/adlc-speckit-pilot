"""Responder bot: plays the on-call personas during a simulation.

It watches the same things a person would: the incident card (opened by Alertmanager), the page in
Mailpit and the alert in GoAlert. It acknowledges in GoAlert signed in as the persona (so GoAlert shows
"Acknowledged by Ana Lima"), records the acknowledgement on the incident and lets the story apply the
runbook remedy. In interactive mode it only waits and mirrors what you do in GoAlert.
"""
from __future__ import annotations

import re

from shared import incidents, teams
from sim import goalert


class ResponderError(RuntimeError):
    pass


def on_call(team: str) -> list[str]:
    return next((t.get("on_call", []) for t in teams.teams() if t["id"] == team), [])


def wait_incident(alert: str, timeout: float = 180, opened_after: str | None = None) -> dict:
    """An incident for this alert opened after the fault went in (an older, unrelated one does not count)."""
    inc = goalert.wait(lambda: incidents.find(alert, opened_after=opened_after), timeout)
    if inc is None:
        raise ResponderError(f"no incident for alert {alert} within {timeout:.0f} s: is the stack up (make sim-stack-up) "
                             "and the API serving this run on :8000 (make sim-api)?")
    return inc


def wait_page(persona_id: str, since: float, timeout: float = 150) -> dict | None:
    email = teams.persona(persona_id)["email"]
    hits = goalert.wait(lambda: [p for p in goalert.pages(since) if email in p["to"]], timeout)
    return hits[0] if hits else None


def _goalert_alert(team: str) -> dict | None:
    svc = goalert.state()["teams"][team]["service_id"]
    alerts = goalert.GoAlert().alerts(svc)
    return alerts[0] if alerts else None


def acknowledge(inc: dict, persona_id: str, timeout: float = 60) -> dict:
    """Acknowledge the team's open GoAlert alert as the persona, then on the incident card."""
    alert = goalert.wait(lambda: _goalert_alert(inc["team"]), timeout)
    if alert is None:
        raise ResponderError(f"no open GoAlert alert for {inc['team']}")
    goalert.GoAlert.as_persona(persona_id).set_status(alert["alertID"], "StatusAcknowledged")
    return incidents.acknowledge(inc["id"], persona_id, f"acknowledged GoAlert alert #{alert['alertID']}")


def mirror_ack(inc: dict, timeout: float = 3600) -> dict:
    """Interactive mode: wait until someone acknowledges in GoAlert, then record who on the incident."""
    names = {p["name"]: p["id"] for p in teams.personas()}

    def acked():
        a = _goalert_alert(inc["team"])
        if not a or a["status"] != "StatusAcknowledged":
            return None
        for e in a["recentEvents"]["nodes"]:
            m = re.search(r"Acknowledged by (.+?)(?: \(|$)", e["message"])
            if m and m.group(1) in names:
                return names[m.group(1)], a["alertID"]
        return None
    hit = goalert.wait(acked, timeout, 3)
    if hit is None:
        raise ResponderError(f"{inc['id']} was not acknowledged in GoAlert")
    return incidents.acknowledge(inc["id"], hit[0], f"acknowledged GoAlert alert #{hit[1]}")


def wait_resolved(inc_id: str, timeout: float = 180) -> dict:
    if not goalert.wait(lambda: incidents.get(inc_id)["status"] == "resolved", timeout):
        raise ResponderError(f"{inc_id} did not resolve within {timeout:.0f} s (alert still firing?)")
    return incidents.get(inc_id)
