"""test:sim-story: fault injection, releases as commits, change requests, shadow traffic, story runner, board cards."""
import subprocess

import pytest

from shared import board, changes, clock, faults, ledger, lifecycle, releases, usecase
from shared.runtime import get_runtime
from sim import workspace
from sim.story import Story, load

UC = "UC-TRV-CLM-001"
FX = {"USD": 1.0, "JPY": 0.0067, "EUR": 1.08}


@pytest.fixture
def sim_run(tmp_path, monkeypatch):
    run = workspace.create("t", runs_dir=tmp_path, make_current=False)
    for k, v in run["env"].items():
        monkeypatch.setenv(k, v)
    clock.pause()
    return run


def test_faults_never_fire_outside_a_simulation(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("SIM_FAULTS", "F2")
    assert faults.apply("policy_fx_rates", dict(FX)) == FX


def test_f2_drops_jpy_only_while_its_release_is_current(sim_run):
    assert faults.apply("policy_fx_rates", dict(FX)) == FX                    # 0.2.0
    releases.cut("0.3.0", by="relmgr", message="FX table refactor")
    assert "JPY" not in faults.apply("policy_fx_rates", dict(FX))
    releases.cut("0.3.1", by="relmgr", message="restore JPY")
    assert faults.apply("policy_fx_rates", dict(FX)) == FX


def test_release_is_a_commit_and_use_case_gates_carry_over(sim_run):
    usecase.save(usecase.template(), by="po")
    usecase.submit(UC, by="po")
    usecase.confirm_classification(UC, by="dataowner")
    for p in ("hoc", "risk", "dataowner"):
        lifecycle.sign("G0", p)
    r = releases.cut("0.3.0-rc1", by="relmgr", message="raise the STP limit",
                     edits=[("agents/orchestrator/src/routing.py", "STP_LIMIT_USD = 1000.0", "STP_LIMIT_USD = 5000.0")])
    ws = sim_run["env"]["PROJECT_ROOT"]
    log = subprocess.run(["git", "log", "--format=%an|%s", "-1"], cwd=ws, capture_output=True, text=True).stdout
    assert log.startswith("Chris Novak|release 0.3.0-rc1") and "v0.3.0-rc1" in subprocess.run(
        ["git", "tag"], cwd=ws, capture_output=True, text=True).stdout
    assert r["event"]["changed"] == ["agents/orchestrator/src/routing.py", "system.yaml"]
    gates = {g["gate"]: g["status"] for g in lifecycle.records()["gates"]}
    assert gates["G0"] == "approved" and gates["G3"] == "pending"            # G0 is the use case's, G3 the release's
    with pytest.raises(releases.ReleaseError):
        releases.cut("0.3.0-rc1", by="relmgr", message="again")
    releases.close("0.3.0-rc1", state="rejected_at_G3", reason="authority change outside G1", by="ailead")
    card = next(c for c in board.board()["items"] if c["id"] == "0.3.0-rc1")
    assert card["type"] == "release" and card["closed"] and card["column"] == "G3"


def test_change_requests_gate_the_deployment(sim_run):
    releases.cut("0.3.0", by="relmgr", message="first release")
    with pytest.raises(changes.ChangeError, match="needs an approved"):
        changes.deploy("shadow", by="relmgr")
    cr = changes.raise_cr("deploy_shadow", by="relmgr")
    with pytest.raises(changes.ChangeError):
        changes.decide(cr["id"], "po")                                         # not CAB
    assert changes.decide(cr["id"], "cab")["status"] == "approved"
    d = changes.deploy("shadow", by="relmgr")
    assert d["cr"] == cr["id"] and get_runtime()["mode"] == "shadow" and not get_runtime()["payments_enabled"]
    go = changes.raise_cr("go_live", by="relmgr")
    with pytest.raises(changes.ChangeError, match="G5 sign-off"):
        changes.decide(go["id"], "cab")
    changes.on_gate_decision("G5", "0.3.0", "rejected")
    assert changes.get(go["id"])["status"] == "rejected"
    kinds = {c["id"]: c["type"] for c in board.board()["items"]}
    assert kinds[cr["id"]] == kinds[go["id"]] == "change"


def test_shadow_traffic_exposes_f2_and_the_fix_passes(sim_run):
    from backend import service
    from sim import traffic
    from synthetic.generate import setup_platform
    setup_platform()
    service.bootstrap()
    clock.set_time(clock.at_day(-1))
    mix = {"bagloss_fx_jpy": 1, "bagloss_fx": 1, "delay_stp": 1}
    releases.cut("0.3.0", by="relmgr", message="FX table refactor")
    lifecycle.set_phase("shadow")
    traffic.run(0, 1, 12, mix, log=lambda _: None)
    r = lifecycle.shadow_report(min_claims=5)
    rep = __import__("json").loads((lifecycle.ev_dir() / "shadow-report.json").read_text())
    assert not r["ok"] and rep["by_scenario"]["bagloss_fx_jpy"]["route_agree"] == 0
    assert {d["scenario"] for d in rep["disagreements"]} == {"bagloss_fx_jpy"}
    assert all(d["agent_route"] == "row1_straight_through" for d in rep["disagreements"])  # wrong routing: no human
    releases.cut("0.3.1", by="relmgr", message="restore JPY")
    traffic.run(1, 1, 12, mix, log=lambda _: None)
    assert lifecycle.shadow_report(min_claims=5)["ok"]
    assert lifecycle.shadow_report(min_claims=500)["ok"] is False             # too few claims to claim 99%


def test_story_runs_g0_to_g2_and_resumes(sim_run):
    lines = []
    Story(load(), log=lines.append).run(until_day=-22)
    gates = {g["gate"]: g["status"] for g in lifecycle.records()["gates"]}
    assert [gates[g] for g in ("G0", "G1", "G2")] == ["approved"] * 3
    assert round(clock.sim_day()) == -30 and ledger.verify()["ok"]
    lines.clear()
    Story(load(), log=lines.append).run(until_day=-22)                       # second call resumes: nothing to redo
    assert lines[0] == "resuming: 8 steps already done in this run" and not any(x.startswith("[day") for x in lines)
