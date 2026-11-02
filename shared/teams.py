"""Dummy organisation for the simulation: teams, personas and the ADLC roles each persona holds (ops/teams.yaml)."""
from __future__ import annotations

import yaml

from shared.config import project_root


class PersonaError(ValueError):
    pass


def _load() -> dict:
    p = project_root() / "ops" / "teams.yaml"
    return yaml.safe_load(p.read_text())


def teams() -> list[dict]:
    return _load()["teams"]


def personas() -> list[dict]:
    t = {x["id"]: x["name"] for x in teams()}
    return [{**p, "team_name": t.get(p["team"], p["team"]), "email": f"{p['id']}@claims.sim"} for p in _load()["personas"]]


def persona(pid: str) -> dict:
    for p in personas():
        if p["id"] == pid:
            return p
    raise PersonaError(f"unknown persona {pid!r}")


def holders(role: str) -> list[dict]:
    return [p for p in personas() if role in p["roles"]]


def label(p: dict) -> str:
    return f"{p['name']} ({p['title']})"
