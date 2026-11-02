"""G0 / G1 intake: the structured use-case record, its computed risk tier and generated card.

Files in the project root (a simulation run's workspace), each written only by its form, so a
change made after approval shows up as drift at later gates:

  usecase/<id>.yaml        record captured by the G0 intake form
  usecase/<id>.risk.json   risk questionnaire answers + computed tier
  usecase/<id>-card.md     card rendered from the record
  usecase/<id>.g1.yaml     authority limits, KPI targets, thresholds and SLOs confirmed by the Business owner (G1)

Every save, submission and confirmation is an event in the evidence ledger.
"""
from __future__ import annotations

import ast
import json
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

import jinja2
import yaml
from pydantic import BaseModel

from shared import clock, ledger, teams
from shared.config import ROOT, project_root, system_manifest

AUTONOMY = ("none", "recommend", "decide_some", "decide_all")
ROUTING_CONSTANTS = {"STP_LIMIT_USD": "stp_limit_usd", "SIGNIFICANCE_NET_PCT": "significance_net_pct",
                     "CONFIDENCE_CUTOFF": "confidence_cutoff"}
BUSINESS_OWNER, DATA_OWNER = "Business owner (Head of Claims)", "Data owner"
TIER_RULES = [
    "HIGH if special-category data is processed",
    "HIGH if it affects customer outcomes and takes actions in systems or decides on its own",
    "HIGH if it processes personal data and decides on its own",
    "MEDIUM if it affects customer outcomes, processes personal data or has any autonomy",
    "LOW otherwise",
]


class UseCaseError(ValueError):
    pass


class ValueRow(BaseModel):
    metric: str
    baseline: str
    target: str


class DataRow(BaseModel):
    data: str
    classification: str
    handling: str


class Classification(BaseModel):
    rows: list[DataRow] = []
    confirmed_by: Optional[str] = None


class Risk(BaseModel):
    customer_outcome: bool
    takes_actions: bool
    personal_data: bool
    special_category: bool
    autonomy: Literal["none", "recommend", "decide_some", "decide_all"]
    max_autonomous_usd: float = 0
    regulatory_exposure: bool


class Condition(BaseModel):
    condition: str
    owner: str
    due: str


class Feasibility(BaseModel):
    verdict: Literal["Go", "Go with conditions", "No go"]
    rationale: str = ""
    conditions: list[Condition] = []


class RaciRow(BaseModel):
    activity: str
    A: str
    R: str = ""
    C: str = ""
    I: str = ""


class UseCase(BaseModel):
    id: str
    title: str
    sponsor: str
    business_owner: str
    product_owner: str
    problem: str
    proposed_solution: str
    value_hypothesis: list[ValueRow]
    scope_in: list[str]
    scope_out: list[str] = []
    data_classification: Classification
    risk: Risk
    feasibility: Feasibility
    raci: list[RaciRow]


class G1Confirmation(BaseModel):
    stp_limit_usd: float
    significance_net_pct: float
    confidence_cutoff: float
    hitl_points: list[str]
    kpi_targets: dict[str, str]
    thresholds_acknowledged: bool
    slos_acknowledged: bool
    confirmed_by: str


# ------------------------------------------------------------------------------------- files
def current_id() -> str:
    return system_manifest()["use_case"]


def paths(uc: str | None = None) -> dict:
    uc = uc or current_id()
    d = project_root() / "usecase"
    return {"record": d / f"{uc}.yaml", "risk": d / f"{uc}.risk.json", "card": d / f"{uc}-card.md", "g1": d / f"{uc}.g1.yaml"}


def exists(uc: str | None = None) -> bool:
    return paths(uc)["record"].exists()


def load(uc: str | None = None) -> UseCase | None:
    p = paths(uc)["record"]
    return UseCase.model_validate(yaml.safe_load(p.read_text())) if p.exists() else None


def load_g1(uc: str | None = None) -> G1Confirmation | None:
    p = paths(uc)["g1"]
    return G1Confirmation.model_validate(yaml.safe_load(p.read_text())) if p.exists() else None


def _dump(model: BaseModel) -> str:
    return yaml.safe_dump(model.model_dump(), sort_keys=False, allow_unicode=True, width=110)


def compute_tier(r: Risk) -> tuple[str, list[str]]:
    decides = r.autonomy in ("decide_some", "decide_all")
    reasons = []
    if r.special_category:
        reasons.append("processes special-category data")
    if r.customer_outcome and (r.takes_actions or decides):
        reasons.append("affects customer outcomes and " + ("takes actions in systems" if r.takes_actions else "decides on its own"))
    if r.personal_data and decides:
        reasons.append("processes personal data and decides on its own")
    if reasons:
        return "HIGH", reasons
    if r.customer_outcome or r.personal_data or r.autonomy != "none":
        return "MEDIUM", ["affects customer outcomes, processes personal data or has some autonomy"]
    return "LOW", ["no customer impact, no personal data, no autonomy"]


def _people() -> dict:
    return {p["id"]: f"{p['name']}, {p['title']}" for p in teams.personas()}


def _write(uc: UseCase) -> str:
    """Record, risk file and card; deterministic, so re-saving unchanged content does not look like a change."""
    p = paths(uc.id)
    p["record"].parent.mkdir(parents=True, exist_ok=True)
    p["record"].write_text(_dump(uc))
    record_sha = ledger.sha256(p["record"].read_bytes())
    tier, reasons = compute_tier(uc.risk)
    p["risk"].write_text(json.dumps({"use_case": uc.id, "answers": uc.risk.model_dump(), "tier": tier,
                                     "reasons": reasons, "rules": TIER_RULES}, indent=2) + "\n")
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / "templates"), keep_trailing_newline=True,
                             undefined=jinja2.StrictUndefined)
    p["card"].write_text(env.get_template("use-case-card.md.j2").render(
        uc=uc, tier=tier, tier_reasons=reasons, people=_people(), record_sha=record_sha))
    return record_sha


def _require_role(by: str, role: str) -> dict:
    p = teams.persona(by)
    if role not in p["roles"]:
        raise UseCaseError(f"{p['name']} ({p['title']}) does not hold the role '{role}'")
    return p


# ------------------------------------------------------------------------------------- G0 actions
def template(uc: str | None = None) -> dict:
    """The current record, or the seed with due days turned into dates on the simulation calendar."""
    uc = uc or current_id()
    if exists(uc):
        return load(uc).model_dump()
    seed = yaml.safe_load((ROOT / "sim" / "seeds" / f"{uc}.yaml").read_text())
    d0 = clock.day0() or datetime.now(timezone.utc)
    for c in seed["feasibility"]["conditions"]:
        c["due"] = (d0 + timedelta(days=c.pop("due_day"))).date().isoformat()
    return seed


def save(data: dict, by: str) -> dict:
    teams.persona(by)
    uc = UseCase.model_validate(data)
    if uc.id != current_id():
        raise UseCaseError(f"system.yaml names use case {current_id()}, not {uc.id}")
    for pid in [uc.sponsor, uc.business_owner, uc.product_owner, *(c.owner for c in uc.feasibility.conditions)]:
        teams.persona(pid)
    old = load(uc.id)
    # Saving after G0 approval is allowed; the change is drift against the approved snapshot and shows on the board.
    # A changed classification needs the Data owner again.
    same_rows = old is not None and old.data_classification.rows == uc.data_classification.rows
    uc.data_classification.confirmed_by = old.data_classification.confirmed_by if same_rows else None
    sha = _write(uc)
    tier, _ = compute_tier(uc.risk)
    ledger.append("use_case_created" if old is None else "use_case_updated", item=uc.id, by=by,
                  title=uc.title, tier=tier, record_sha256=sha)
    return summary(uc.id)


def submitted(uc: str | None = None) -> bool:
    uc = uc or current_id()
    return any(e["type"] == "use_case_submitted" for e in ledger.for_item(uc))


def submit(uc: str, by: str) -> dict:
    teams.persona(by)
    if not exists(uc):
        raise UseCaseError(f"no record for {uc}: save the intake form first")
    if submitted(uc):
        raise UseCaseError(f"{uc} is already submitted for G0")
    ledger.append("use_case_submitted", item=uc, by=by, gate="G0")
    return summary(uc)


def confirm_classification(uc: str, by: str) -> dict:
    _require_role(by, DATA_OWNER)
    rec = load(uc)
    if rec is None:
        raise UseCaseError(f"no record for {uc}")
    rec.data_classification.confirmed_by = by
    sha = _write(rec)
    ledger.append("classification_confirmed", item=uc, by=by, record_sha256=sha)
    return summary(uc)


# ------------------------------------------------------------------------------------- G1 actions
def routing_constants() -> dict:
    """Authority limits as written in the project's routing.py (parsed, not imported: the file may be a candidate release)."""
    tree = ast.parse((project_root() / "agents" / "orchestrator" / "src" / "routing.py").read_text())
    out = {}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        for t in targets:
            if isinstance(t, ast.Name) and t.id in ROUTING_CONSTANTS and node.value is not None:
                out[ROUTING_CONSTANTS[t.id]] = float(ast.literal_eval(node.value))
    return out


def g1_template(uc: str | None = None) -> dict:
    g1 = load_g1(uc)
    if g1:
        return g1.model_dump()
    m = system_manifest()
    kpis = yaml.safe_load((project_root() / "docs" / "kpis.yaml").read_text())["kpis"]
    return {"stp_limit_usd": m["routing"]["stp_limit_usd"], "significance_net_pct": m["routing"]["significance_net_pct"],
            "confidence_cutoff": m["routing"]["confidence_cutoff"], "hitl_points": m.get("hitl_points", []),
            "kpi_targets": {k["id"]: str(k["target"]) for k in kpis}, "thresholds_acknowledged": False,
            "slos_acknowledged": False, "confirmed_by": ""}


def save_g1(uc: str, data: dict, by: str) -> dict:
    _require_role(by, BUSINESS_OWNER)
    g1 = G1Confirmation.model_validate({**data, "confirmed_by": by})
    p = paths(uc)["g1"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_dump(g1))
    ledger.append("g1_confirmed", item=uc, by=by, gate="G1", file_sha256=ledger.sha256(p.read_bytes()))
    return summary(uc)


# ------------------------------------------------------------------------------------- read model
def summary(uc: str | None = None) -> dict:
    uc = uc or current_id()
    rec = load(uc)
    if rec is None:
        return {"id": uc, "exists": False, "submitted": False}
    tier, reasons = compute_tier(rec.risk)
    p = paths(uc)
    g1 = load_g1(uc)
    return {"id": uc, "exists": True, "submitted": submitted(uc), "record": rec.model_dump(), "tier": tier,
            "tier_reasons": reasons, "tier_rules": TIER_RULES, "card": p["card"].read_text() if p["card"].exists() else "",
            "g1": g1.model_dump() if g1 else None,
            "files": {k: {"path": str(v.relative_to(project_root())), "sha256": ledger.sha256(v.read_bytes())}
                      for k, v in p.items() if v.exists()}}


# ------------------------------------------------------------------------------------- gate checks
def _c_record():
    uc = load()
    for pid in [uc.sponsor, uc.business_owner, uc.product_owner]:
        teams.persona(pid)
    ok = bool(uc.problem.strip() and uc.proposed_solution.strip() and uc.value_hypothesis and uc.scope_in)
    return ok, (f"{len(uc.value_hypothesis)} value metrics, {len(uc.scope_in)} in-scope items, owners named"
                if ok else "problem, solution, value hypothesis or scope missing")


def _c_tier():
    uc, p = load(), paths()["risk"]
    if not p.exists():
        return False, "risk questionnaire not saved"
    tier, _ = compute_tier(uc.risk)
    saved, declared = json.loads(p.read_text())["tier"], system_manifest()["risk_tier"].upper()
    ok = tier == saved == declared
    return ok, f"questionnaire tier {tier} = system.yaml risk_tier {declared}" if ok else \
        f"questionnaire {tier}, saved {saved}, system.yaml {declared}"


def _c_classification():
    c = load().data_classification
    if not c.rows:
        return False, "no data classified"
    if not c.confirmed_by or DATA_OWNER not in teams.persona(c.confirmed_by)["roles"]:
        return False, f"{len(c.rows)} data types classified; not yet confirmed by the Data owner"
    return True, f"{len(c.rows)} data types, confirmed by {teams.persona(c.confirmed_by)['name']}"


def _c_feasibility():
    f = load().feasibility
    if f.verdict == "No go":
        return False, "verdict: No go"
    for c in f.conditions:
        teams.persona(c.owner)
        date.fromisoformat(c.due)
    ok = f.verdict == "Go" or bool(f.conditions)
    return ok, f"'{f.verdict}', {len(f.conditions)} conditions, each with an owner and due date" if ok else \
        "'Go with conditions' needs at least one owned condition"


def _c_raci():
    rows = load().raci
    bad = [r.activity for r in rows if not r.A.strip() or any(s in r.A for s in ",;")]
    return bool(rows) and not bad, f"{len(rows)} activities, one Accountable each" if rows and not bad else \
        f"needs exactly one Accountable: {bad or 'no activities'}"


def _c_card():
    p = paths()
    if not p["card"].exists():
        return False, "card not generated"
    sha = ledger.sha256(p["record"].read_bytes())
    ok = f"Record sha256: {sha}" in p["card"].read_text()
    return ok, "card generated from the current record" if ok else "card is out of date: save the record again"


def _c_submitted():
    ok = submitted()
    return ok, "submitted for G0" if ok else "draft: not yet submitted for G0"


def g0_checks() -> list:
    return [("Use-case record complete (problem, value, scope, owners)", _c_record),
            ("Risk tier assessed (questionnaire = system.yaml)", _c_tier),
            ("Data classification agreed with the Data owner", _c_classification),
            ("Feasibility verdict with owned conditions", _c_feasibility),
            ("RACI agreed (one Accountable per activity)", _c_raci),
            ("Use-case card generated from the current record", _c_card),
            ("Submitted for G0", _c_submitted)]


def _c_g1():
    g1 = load_g1()
    if g1 is None:
        return False, "authority limits and KPI targets not yet confirmed by the Business owner"
    if BUSINESS_OWNER not in teams.persona(g1.confirmed_by)["roles"]:
        return False, "confirmed by someone who is not the Business owner"
    m = system_manifest()["routing"]
    code = routing_constants()
    diff = [k for k in ROUTING_CONSTANTS.values() if not (getattr(g1, k) == m[k] == code.get(k))]
    if diff:
        return False, f"confirmed limits differ from system.yaml / routing.py: {diff}"
    if not (g1.thresholds_acknowledged and g1.slos_acknowledged):
        return False, "eval thresholds and SLOs not acknowledged"
    return True, (f"STP ≤ USD {g1.stp_limit_usd:,.0f}, significance {g1.significance_net_pct:g}%, confidence "
                  f"{g1.confidence_cutoff:g}; {len(g1.kpi_targets)} KPI targets; confirmed by {teams.persona(g1.confirmed_by)['name']}")


def g1_check() -> tuple:
    return ("Authority limits, KPI targets and thresholds confirmed by the Business owner", _c_g1)
