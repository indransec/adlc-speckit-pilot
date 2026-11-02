"""Change requests for releases (the CAB process) and deployments.

  deploy_shadow  CR to deploy a release to production in shadow mode: no customer impact. Approved by CAB.
  go_live        CR to go live (assisted mode). Its decision IS the G5 sign-off (CAB + Head of Claims),
                 so it closes when G5 is approved or rejected.
  emergency      expedited change for a control fix during hypercare (simulation step 3).

A deployment needs an approved CR for that release and phase. Everything is an event in the ledger.
"""
from __future__ import annotations

from shared import ledger, teams
from shared.config import release as current_release

KINDS = {
    "deploy_shadow": {"approvers": ["CAB"], "title": "Deploy {release} to production in shadow mode"},
    "go_live": {"approvers": ["CAB", "Head of Claims"], "title": "Go live with {release} (assisted mode)", "gate": "G5"},
    "phase_full": {"approvers": ["Head of Claims"], "title": "Move {release} from assisted to full mode (straight-through on)"},
    "emergency": {"approvers": ["CAB", "Head of Claims"], "title": "Emergency change for {release}"},
}
PHASE_CR = {"shadow": "deploy_shadow", "assisted": "go_live", "full": "phase_full"}
MAX_OVERRIDE_FOR_FULL = 0.15  # deploy/rollout-plan.md: assisted -> full needs override rate < 15%


class ChangeError(ValueError):
    pass


def crs(release: str | None = None) -> list[dict]:
    out: dict[str, dict] = {}
    for e in ledger.events():
        if e["type"] == "cr_raised":
            out[e["item"]] = {"id": e["item"], "release": e["parent"], "kind": e["kind"], "title": e["title"],
                              "raised_by": e["by"], "raised": e["ts"], "status": "raised", "signatures": [],
                              "approvers": KINDS[e["kind"]]["approvers"]}
        elif e["type"] in ("cr_signed", "cr_approved", "cr_rejected") and e["item"] in out:
            c = out[e["item"]]
            if e["type"] == "cr_signed":
                c["signatures"].append({"persona": e["by"], "role": e["role"], "at": e["ts"]})
            else:
                c.update(status=e["type"][3:], decided=e["ts"], notes=e.get("notes", ""), via=e.get("via"))
    return [c for c in out.values() if release is None or c["release"] == release]


def get(cr_id: str) -> dict:
    for c in crs():
        if c["id"] == cr_id:
            return c
    raise ChangeError(f"unknown change request {cr_id}")


def raise_cr(kind: str, *, by: str, release: str | None = None, summary: str = "") -> dict:
    if kind not in KINDS:
        raise ChangeError(f"unknown change kind {kind}")
    teams.persona(by)
    rel = release or current_release()
    if any(c["kind"] == kind and c["status"] == "raised" for c in crs(rel)):
        raise ChangeError(f"an open {kind} change request already exists for {rel}")
    cr_id = f"CR-{sum(e['type'] == 'cr_raised' for e in ledger.events()) + 1:03d}"
    ledger.append("cr_raised", item=cr_id, parent=rel, kind=kind, title=KINDS[kind]["title"].format(release=rel),
                  by=by, summary=summary)
    return get(cr_id)


def decide(cr_id: str, persona_id: str, decision: str = "approved", notes: str = "") -> dict:
    c = get(cr_id)
    if c["status"] != "raised":
        raise ChangeError(f"{cr_id} is already {c['status']}")
    if KINDS[c["kind"]].get("gate"):
        raise ChangeError(f"{cr_id} is decided through the {KINDS[c['kind']]['gate']} sign-off")
    p = teams.persona(persona_id)
    open_roles = [r for r in c["approvers"] if r not in {s["role"] for s in c["signatures"]}]
    roles = [r for r in open_roles if r in p["roles"]]
    if not roles or any(s["persona"] == persona_id for s in c["signatures"]):
        raise ChangeError(f"{p['name']} cannot decide {cr_id}: open roles {open_roles}")
    if decision == "rejected":
        ledger.append("cr_rejected", item=cr_id, parent=c["release"], by=persona_id, notes=notes)
        return get(cr_id)
    if c["kind"] == "phase_full":
        rate = override_rate(days=7)
        if rate is None or rate >= MAX_OVERRIDE_FOR_FULL:
            raise ChangeError(f"{cr_id}: override rate over the last 7 days is {rate} (needs < {MAX_OVERRIDE_FOR_FULL})")
    ledger.append("cr_signed", item=cr_id, parent=c["release"], by=persona_id, role=roles[0])
    if len(open_roles) == 1:
        ledger.append("cr_approved", item=cr_id, parent=c["release"], by=persona_id, notes=notes)
    return get(cr_id)


def override_rate(days: int = 7) -> float | None:
    from datetime import timedelta
    from shared import clock, db
    since = (clock.now() - timedelta(days=days)).isoformat(timespec="seconds")
    r = db.one("SELECT COUNT(*) n, AVG(override) r FROM approvals WHERE level IN ('L1', 'L2') AND decided_at >= ?", [since])
    return round(r["r"], 4) if r["n"] else None


def on_gate_decision(gate: str, release: str, decision: str) -> None:
    """A gate-linked CR (go_live / G5) closes with the gate decision."""
    for c in crs(release):
        if c["status"] == "raised" and KINDS[c["kind"]].get("gate") == gate:
            ledger.append("cr_approved" if decision != "rejected" else "cr_rejected", item=c["id"], parent=release,
                          by="lifecycle", via=gate)


def deploy(phase: str, *, by: str, release: str | None = None) -> dict:
    from shared import lifecycle  # late import: lifecycle closes gate-linked CRs through this module
    teams.persona(by)
    rel = release or current_release()
    needed = PHASE_CR[phase]
    cr = next((c for c in crs(rel) if c["kind"] in (needed, "emergency") and c["status"] == "approved"), None)
    if cr is None:
        raise ChangeError(f"deploying {rel} in {phase} mode needs an approved {needed} change request")
    out = lifecycle.set_phase(phase)
    ledger.append("deployed", item=rel, phase=phase, cr=cr["id"], by=by)
    return {"release": rel, "phase": phase, "cr": cr["id"], "runtime": out["output"]}


def replay_shadow(n: int, *, by: str, release: str | None = None) -> dict:
    """Emergency path: G5 shadow evidence from a replay of recent traffic, under an approved emergency change."""
    from shared import lifecycle
    teams.persona(by)
    rel = release or current_release()
    cr = next((c for c in crs(rel) if c["kind"] == "emergency" and c["status"] == "approved"), None)
    if cr is None:
        raise ChangeError(f"a shadow replay for {rel} needs an approved emergency change request")
    r = lifecycle.replay_shadow(n)
    ledger.append("deployed", item=rel, phase="shadow-replay", cr=cr["id"], by=by, claims=n)
    return {"release": rel, "cr": cr["id"], "output": r["output"], "ok": r["ok"]}


def deployments(release: str | None = None) -> list[dict]:
    return [e for e in ledger.events() if e["type"] == "deployed" and (release is None or e["item"] == release)]
