"""Small GoAlert client (GraphQL over a basic-auth session) and Mailpit reader for the simulation stack."""
from __future__ import annotations

import calendar
import json
import re
import time

import httpx

from shared.config import ROOT

GOALERT, MAILPIT = "http://localhost:8081", "http://localhost:8025"
STATE = ROOT / "deploy" / "sim" / "generated" / "goalert-state.json"


def password(persona_id: str) -> str:
    return f"{persona_id}-sim-password"  # demo accounts created by scripts/sim_stack.py


class GoAlert:
    """Signed in as admin, or as an on-call persona so acknowledgements carry that person's name."""

    def __init__(self, username: str = "admin", pw: str = "admin123", base: str = GOALERT):
        tok = httpx.post(f"{base}/api/v2/identity/providers/basic?noRedirect=1", data={"username": username, "password": pw},
                         headers={"Referer": base}, timeout=30).text.strip()
        self.c = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {tok}"}, timeout=30)

    @classmethod
    def as_persona(cls, persona_id: str) -> "GoAlert":
        return cls(persona_id, password(persona_id))

    def gql(self, query: str, variables: dict | None = None) -> dict:
        r = self.c.post("/api/graphql", json={"query": query, "variables": variables or {}}).json()
        if r.get("errors"):
            raise RuntimeError(f"GoAlert: {r['errors'][0]['message']}")
        return r["data"]

    def alerts(self, service_id: str, open_only: bool = True) -> list[dict]:
        statuses = ["StatusUnacknowledged", "StatusAcknowledged"] + ([] if open_only else ["StatusClosed"])
        q = ("query($in:AlertSearchOptions){ alerts(input:$in){ nodes { id alertID status summary createdAt "
             "recentEvents { nodes { message timestamp } } } } }")
        return self.gql(q, {"in": {"filterByServiceID": [service_id], "filterByStatus": statuses, "first": 20}})["alerts"]["nodes"]

    def set_status(self, alert_id: int, status: str) -> None:
        self.gql("mutation($in:UpdateAlertsInput!){ updateAlerts(input:$in){ id } }",
                 {"in": {"alertIDs": [alert_id], "newStatus": status}})


def state() -> dict:
    if not STATE.exists():
        raise RuntimeError("GoAlert is not seeded: run `make sim-stack-up`")
    return json.loads(STATE.read_text())


def pages(since: float = 0.0) -> list[dict]:
    """Emails in Mailpit other than verification codes: who was paged or informed, about what, when."""
    out = []
    for m in httpx.get(f"{MAILPIT}/api/v1/messages", params={"limit": 200}, timeout=10).json().get("messages", []):
        if "erif" in m.get("Subject", ""):
            continue
        created = calendar.timegm(time.strptime(m["Created"][:19], "%Y-%m-%dT%H:%M:%S"))  # Mailpit reports UTC
        if created >= since:
            out.append({"to": [t["Address"] for t in m.get("To") or []], "subject": m.get("Subject", ""), "created": created})
    return out


def wait(condition, timeout: float, every: float = 2.0):
    end = time.time() + timeout
    while time.time() < end:
        value = condition()
        if value:
            return value
        time.sleep(every)
    return None


def verification_code(address: str, timeout: float = 60) -> int:
    def find():
        for m in httpx.get(f"{MAILPIT}/api/v1/messages", timeout=10).json().get("messages", []):
            if address in {t["Address"] for t in m.get("To") or []} and "erif" in m.get("Subject", "") + m.get("Snippet", ""):
                text = httpx.get(f"{MAILPIT}/api/v1/message/{m['ID']}", timeout=10).json().get("Text", "")
                code = re.search(r"\b(\d{6})\b", text) or re.search(r"\b(\d{6})\b", m.get("Snippet", ""))
                if code:
                    return int(code.group(1))
        return None
    code = wait(find, timeout, 1)
    if code is None:
        raise RuntimeError(f"no verification email for {address} in Mailpit")
    return code

