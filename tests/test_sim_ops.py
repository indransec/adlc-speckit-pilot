"""test:sim-ops: runtime faults, business-time metrics, incidents from Alertmanager, rollout guard, stack config."""
import os

import pytest
import yaml

from shared import changes, clock, db, faults, incidents, ledger, prom, releases
from shared.runtime import get_runtime
from sim import workspace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def sim_run(tmp_path, monkeypatch):
    run = workspace.create("t", runs_dir=tmp_path, make_current=False)
    for k, v in run["env"].items():
        monkeypatch.setenv(k, v)
    clock.pause()
    return run


def alert(status, name="payment_without_token", fp="abc123", **labels):
    return {"alerts": [{"status": status, "fingerprint": fp,
                        "labels": {"alertname": name, "severity": "sev1", "team": "sre-oncall", "release": "0.2.0",
                                   "mode": "full", **labels},
                        "annotations": {"summary": "payment without token", "value": "2", "inform": "risk"}}]}


def test_runtime_faults_toggle_and_never_fire_outside_a_run(sim_run, monkeypatch):
    assert faults.apply("stp_limit", 1000.0) == 1000.0
    faults.inject("F4", by="simulator")
    assert faults.apply("stp_limit", 1000.0) == 5000.0
    faults.inject("F7", by="simulator")
    with pytest.raises(faults.ToolUnavailable):  # half of the flight-status calls fail while F7 is on
        for i in range(50):
            faults.apply("tool_call", None, tool_id="TOOL-FLIGHT-STATUS", claim_id=f"C{i}", op="get_flight")
    assert faults.apply("tool_call", "ok", tool_id="TOOL-POLICY-ADMIN", claim_id="C1", op="get_policy") == "ok"
    with pytest.raises(ValueError, match="through a release"):
        faults.inject("F2", by="simulator")
    faults.clear("F4", by="ana")
    assert faults.apply("stp_limit", 1000.0) == 1000.0
    assert [e["fault"] for e in ledger.events() if e["type"] == "fault_cleared"] == ["F4"]
    monkeypatch.setenv("APP_ENV", "dev")
    assert faults.apply("tool_call", "ok", tool_id="TOOL-FLIGHT-STATUS", claim_id="C1", op="x") == "ok"


def test_f3_rejects_a_stable_share_of_payable_claims(sim_run):
    faults.inject("F3", by="simulator")
    out = {"recommendation": "approve", "recommended_usd": 200.0}
    hit = [faults.apply("assessment_result", out, claim_id=f"CLM-{i}")["recommendation"] == "reject" for i in range(400)]
    assert 0.25 < sum(hit) / len(hit) < 0.45
    assert hit == [faults.apply("assessment_result", out, claim_id=f"CLM-{i}")["recommendation"] == "reject" for i in range(400)]


def test_metrics_use_business_time_windows(sim_run):
    db.init_db()
    clock.set_time(clock.at_day(30, 10))
    db.insert("payments", {"payment_id": "P1", "claim_id": "C1", "amount_usd": 100, "token_verified": 0, "created_at": db.now()})
    assert prom.values()["adlc_payments_without_valid_token"] == 1
    clock.set_time(clock.at_day(31, 11))                                   # a business day later: out of the window
    assert prom.values()["adlc_payments_without_valid_token"] == 0


def test_alertmanager_opens_incident_with_automatic_action_and_resolves(sim_run):
    db.init_db()
    assert get_runtime()["kill_switch"] is False
    [inc] = incidents.from_alertmanager(alert("firing", auto_action="kill_switch"))
    assert inc["id"] == "INC-001" and get_runtime()["kill_switch"] is True and inc["actions"]
    assert incidents.from_alertmanager(alert("firing", auto_action="kill_switch")) == []   # repeat notification: same incident
    with pytest.raises(incidents.IncidentError, match="not on call"):
        incidents.acknowledge("INC-001", "ana")                            # AI on-call, not SRE
    incidents.acknowledge("INC-001", "elena")
    with pytest.raises(incidents.IncidentError, match="once the alert has cleared"):
        incidents.close("INC-001", "elena", "hotfix")
    incidents.from_alertmanager(alert("resolved"))
    with pytest.raises(incidents.IncidentError, match="root cause"):
        incidents.close("INC-001", "elena", " ")
    i = incidents.close("INC-001", "elena", "hotfix bypassed token verification")
    assert i["status"] == "closed" and [t["event"] for t in i["timeline"]] == ["opened", "action", "acknowledged", "resolved", "closed"]
    assert ledger.verify()["ok"]


def test_full_mode_needs_the_phase_change_and_a_low_override_rate(sim_run):
    db.init_db()
    releases.cut("0.3.1", by="relmgr", message="first release")
    with pytest.raises(changes.ChangeError, match="phase_full"):
        changes.deploy("full", by="relmgr")
    cr = changes.raise_cr("phase_full", by="relmgr")
    clock.set_time(clock.at_day(29))
    for n in range(10):
        db.insert("approvals", {"claim_id": f"C{n}", "level": "L1", "user_id": "l1.priya", "decision": "x",
                                "override": int(n < 3), "decided_at": db.now()})
    with pytest.raises(changes.ChangeError, match="override rate"):
        changes.decide(cr["id"], "hoc")                                    # 30% overrides: not ready for full mode
    db.execute("UPDATE approvals SET override = 0")
    assert changes.decide(cr["id"], "hoc")["status"] == "approved"
    assert changes.deploy("full", by="relmgr")["phase"] == "full" and get_runtime()["straight_through_enabled"]


def test_stack_config_is_generated_from_alerts_and_teams(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("sim_stack", os.path.join(ROOT, "scripts", "sim_stack.py"))
    sim_stack = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sim_stack)
    monkeypatch.setattr(sim_stack, "GEN", str(tmp_path))
    sim_stack.prometheus_config()
    team_ids = {t["id"] for t in yaml.safe_load(open(os.path.join(ROOT, "ops", "teams.yaml")))["teams"]}
    rules = yaml.safe_load(open(tmp_path / "rules.yml"))["groups"][0]["rules"]
    alerts = yaml.safe_load(open(os.path.join(ROOT, "observability", "alerts.yaml")))["alerts"]
    assert [r["alert"] for r in rules] == [a["name"] for a in alerts]
    assert all(r["labels"]["team"] in team_ids and r["expr"].startswith("adlc_") for r in rules)
    expected = {f["expected"]["alert"] for f in faults.catalog().values() if f["expected"].get("alert")}
    assert expected <= {r["alert"] for r in rules}                         # every fault has an alert to catch it
    sim_stack.alertmanager_config({"sre-oncall": {"integration_url": "http://goalert:8081/x"}})
    am = yaml.safe_load(open(tmp_path / "alertmanager.yml"))
    assert am["route"]["routes"][0] == {"receiver": "adlc-api", "continue": True}
    assert am["route"]["routes"][1]["matchers"] == ['team="sre-oncall"']
