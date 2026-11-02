"""test:sim-foundations: simulation clock, isolated runs, G0/G1 intake, persona sign-off, evidence ledger, board."""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import pytest

from shared import board, clock, db, ledger, lifecycle, usecase
from sim import workspace

UC = "UC-TRV-CLM-001"


@pytest.fixture
def sim_run(tmp_path, monkeypatch):
    run = workspace.create("t", runs_dir=tmp_path, make_current=False)
    for k, v in run["env"].items():
        monkeypatch.setenv(k, v)
    return run


def g0_ready(by="po"):
    usecase.save(usecase.template(), by=by)
    usecase.submit(UC, by=by)
    usecase.confirm_classification(UC, by="dataowner")


def approve_g0():
    g0_ready()
    for p in ("hoc", "risk", "dataowner"):
        rec = lifecycle.sign("G0", p)
    return rec


def test_clock_is_real_time_without_a_clock_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SIM_CLOCK_FILE", str(tmp_path / "none.json"))
    assert not clock.is_simulated()
    assert abs((clock.now() - datetime.now(timezone.utc)).total_seconds()) < 2


def test_clock_rate_pause_and_skip(tmp_path, monkeypatch):
    monkeypatch.setenv("SIM_CLOCK_FILE", str(tmp_path / "clock.json"))
    d0 = datetime(2027, 1, 4, tzinfo=timezone.utc)
    clock.start(d0 - timedelta(days=60), d0, 720)
    time.sleep(0.05)
    assert clock.sim_day() > -60                                      # 0.05 s real = ~36 s simulated
    clock.pause()
    t = clock.now()
    time.sleep(0.02)
    assert clock.now() == t and clock.state()["paused"]
    clock.skip_to_day(-45)
    assert round(clock.sim_day(), 3) == -44.625                       # day -45, 09:00
    assert db.now().startswith(clock.at_day(-45).date().isoformat())  # business timestamps follow the clock
    with pytest.raises(ValueError):
        clock.skip_to_day(-50)                                        # never backwards
    clock.resume()
    assert clock.rate() == 720


def test_risk_tier_rules():
    base = dict(customer_outcome=False, takes_actions=False, personal_data=False, special_category=False,
                autonomy="none", regulatory_exposure=False)
    assert usecase.compute_tier(usecase.Risk(**base))[0] == "LOW"
    assert usecase.compute_tier(usecase.Risk(**{**base, "autonomy": "recommend"}))[0] == "MEDIUM"
    assert usecase.compute_tier(usecase.Risk(**{**base, "customer_outcome": True, "takes_actions": True}))[0] == "HIGH"
    assert usecase.compute_tier(usecase.Risk(**{**base, "personal_data": True, "autonomy": "decide_some"}))[0] == "HIGH"
    assert usecase.compute_tier(usecase.Risk(**{**base, "special_category": True}))[0] == "HIGH"


def test_run_is_isolated_from_the_repo(sim_run):
    ws = sim_run["env"]["PROJECT_ROOT"]
    assert os.path.isdir(os.path.join(ws, ".git")) and not os.path.exists(os.path.join(ws, "sim", "runs"))
    repo_record = os.path.join(os.path.dirname(os.path.dirname(__file__)), "usecase", f"{UC}.yaml")
    before = open(repo_record, "rb").read() if os.path.exists(repo_record) else None  # governed repo has its own
    usecase.save(usecase.template(), by="po")
    assert os.path.exists(os.path.join(ws, "usecase", f"{UC}.yaml"))
    after = open(repo_record, "rb").read() if os.path.exists(repo_record) else None
    assert before == after                                            # the run never writes into the repo


def test_g0_sign_off_freezes_evidence_and_moves_the_board(sim_run):
    usecase.save(usecase.template(), by="po")
    assert board.board()["items"][0]["column"] == "Backlog"
    usecase.submit(UC, by="po")
    usecase.confirm_classification(UC, by="dataowner")
    assert lifecycle.check_gate("G0")["ready"]
    with pytest.raises(ValueError, match="holds none"):
        lifecycle.sign("G0", "po")                                    # Product owner is not a G0 approver
    assert lifecycle.sign("G0", "hoc")["status"] == "pending"
    with pytest.raises(ValueError, match="already signed"):
        lifecycle.sign("G0", "hoc")
    lifecycle.sign("G0", "risk")
    rec = lifecycle.sign("G0", "dataowner")
    assert rec["status"] == "approved" and len(rec["manifest_sha256"]) == 64
    m = ledger.manifest(rec["manifest_sha256"])
    assert [a["role"] for a in m["approvers"]] == ["Sponsor (Head of Claims)", "Risk", "Data owner"]
    assert {a["name"] for a in m["artifacts"] if a["required"]} == {"use-case-record", "use-case-card", "risk-questionnaire"}
    assert all(ledger.live_status(a) == "verified" for a in m["artifacts"])
    v = ledger.verify()
    assert v["ok"] and v["manifests"] == 1 and v["artifacts_checked"] == len(m["artifacts"])
    card = board.board()["items"][0]
    assert card["column"] == "G1" and card["artifacts"] == len(m["artifacts"])


def test_approval_needs_passing_checks_but_rejection_does_not(sim_run):
    usecase.save(usecase.template(), by="po")                         # not submitted, classification unconfirmed
    with pytest.raises(ValueError, match="not ready"):
        lifecycle.sign("G0", "hoc")
    rec = lifecycle.sign("G0", "risk", "rejected", "value hypothesis has no baseline")
    assert rec["status"] == "rejected"
    ev = ledger.events()[-1]
    assert ev["type"] == "gate_rejected" and ledger.manifest(ev["manifest_sha256"])["decision"] == "rejected"
    usecase.submit(UC, by="po")
    usecase.confirm_classification(UC, by="dataowner")
    card = board.board()["items"][0]
    assert card["blocked"] and card["sub_status"] == "Rejected"
    for p in ("hoc", "risk", "dataowner"):                            # a fresh round of signatures after rework
        rec = lifecycle.sign("G0", p)
    assert rec["status"] == "approved" and not board.board()["items"][0]["blocked"]


def test_ledger_detects_tampering(sim_run):
    approve_g0()
    p = ledger.ledger_path()
    lines = p.read_text().splitlines()
    original = p.read_text()
    tampered = json.loads(lines[2])
    tampered["by"] = "someone-else"
    lines[2] = json.dumps(tampered, sort_keys=True)
    p.write_text("\n".join(lines) + "\n")
    v = ledger.verify()
    assert not v["ok"] and any("event 3" in x for x in v["problems"])
    p.write_text(original)
    assert ledger.verify()["ok"]
    a = ledger.manifest(ledger.events()[-1]["manifest_sha256"])["artifacts"][0]
    copy = ledger._store_path(a["sha256"])
    os.chmod(copy, 0o644)
    copy.write_text("edited after approval")
    assert any("was modified" in x for x in ledger.verify()["problems"])


def test_drift_is_flagged_for_documents_and_blocks_for_authority_rules(sim_run):
    approve_g0()
    ws = sim_run["env"]["PROJECT_ROOT"]
    with open(os.path.join(ws, "usecase", f"{UC}.yaml"), "a") as f:
        f.write("# edited after G0 approval\n")
    g1 = lifecycle.check_gate("G1")
    drift = next(c for c in g1["checks"] if c["check"].startswith("Artifacts approved"))
    assert drift["ok"] and "flagged" in drift["detail"]               # documents: visible, not blocking
    usecase.save_g1(UC, {**usecase.g1_template(UC), "thresholds_acknowledged": True, "slos_acknowledged": True}, by="hoc")
    lifecycle.sign("G1", "hoc")
    assert lifecycle.sign("G1", "ailead")["status"] == "approved"
    routing = os.path.join(ws, "agents", "orchestrator", "src", "routing.py")
    with open(routing) as f:
        src = f.read()
    with open(routing, "w") as f:
        f.write(src.replace("STP_LIMIT_USD = 1000.0", "STP_LIMIT_USD = 5000.0"))
    g2 = {c["check"]: c for c in lifecycle.check_gate("G2")["checks"]}
    blocked = g2["Artifacts approved at earlier gates unchanged"]
    assert not blocked["ok"] and "routing.py" in blocked["detail"]    # authority rules: blocks until G1 is re-approved


def test_release_bump_is_not_drift_for_authority_rules(sim_run):
    approve_g0()
    usecase.save_g1(UC, {**usecase.g1_template(UC), "thresholds_acknowledged": True, "slos_acknowledged": True}, by="hoc")
    lifecycle.sign("G1", "hoc")
    lifecycle.sign("G1", "ailead")
    sysf = os.path.join(sim_run["env"]["PROJECT_ROOT"], "system.yaml")
    with open(sysf) as f:
        src = f.read()
    with open(sysf, "w") as f:
        f.write(re.sub(r"(?m)^release: .*$", "release: 0.3.0-rc1", src))
    assert lifecycle.ev_dir().name == "0.3.0-rc1"                     # release() re-reads the changed manifest
    assert not [d for d in ledger.drift(before_gate="G2") if d["on_drift"] == "block"]


def test_changed_classification_needs_the_data_owner_again(sim_run):
    g0_ready()
    rec = usecase.template()
    rec["data_classification"]["rows"].append({"data": "Phone number", "classification": "Confidential - PII", "handling": "Mask"})
    s = usecase.save(rec, by="po")
    assert s["record"]["data_classification"]["confirmed_by"] is None
    with pytest.raises(usecase.UseCaseError):
        usecase.confirm_classification(UC, by="po")


def test_api_board_personas_and_clock(sim_run):
    from fastapi.testclient import TestClient
    from backend.app import app
    c = TestClient(app)
    assert c.get("/clock").json()["simulated"]
    assert {"hoc", "risk", "dataowner"} <= {p["id"] for p in c.get("/personas").json()}
    r = c.post("/usecases", json={"by": "po", "record": c.get("/usecases/template").json()})
    assert r.status_code == 200 and r.json()["tier"] == "HIGH"
    assert c.get("/board").json()["items"][0]["column"] == "Backlog"
    assert c.post("/lifecycle/gates/G0/sign", json={"persona": "po"}).status_code == 409
