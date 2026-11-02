"""test:sim-close: windowed hypercare report, G6 extension on the board, detection matrix, evidence packs."""
import hashlib
import io
import json
import zipfile

import pytest

from shared import board, changes, clock, db, incidents, ledger, lifecycle, releases, usecase
from sim import report, workspace

UC = "UC-TRV-CLM-001"


@pytest.fixture
def sim_run(tmp_path, monkeypatch):
    run = workspace.create("t", runs_dir=tmp_path, make_current=False)
    for k, v in run["env"].items():
        monkeypatch.setenv(k, v)
    clock.pause()
    return run


def _claim(cid, day, **kw):
    db.insert("claims", {"claim_id": cid, "historical": 0, "mode": "full", "status": "closed_paid", "package_latency_s": 3.0,
                         "package_at": clock.at_day(day, 10).isoformat(timespec="seconds"), **kw})


def test_hypercare_report_counts_only_its_window(sim_run):
    db.init_db()
    clock.set_time(clock.at_day(80))
    for n in range(25):
        _claim(f"A{n}", 40)
        _claim(f"B{n}", 65)
    db.insert("payments", {"payment_id": "P1", "claim_id": "A1", "amount_usd": 10, "token_verified": 0,
                           "created_at": clock.at_day(47, 12).isoformat(timespec="seconds")})
    bad = lifecycle.hypercare_report(29, 59)
    rep = json.loads((lifecycle.ev_dir() / "hypercare-report.json").read_text())
    assert not bad["ok"] and rep["claims"] == 25 and rep["controls"]["payments_without_valid_token"] == 1
    assert lifecycle.hypercare_report(61, 75)["ok"]                         # the extension window is clean


def test_detection_matrix_compares_expected_and_actual(sim_run):
    db.init_db()
    ledger.append("fault_injected", item=None, fault="F1", release="0.3.0-rc1", simulation=True)
    ledger.append("fault_detected", item=None, fault="F1", gate="G3", release="0.3.0-rc1", simulation=True)
    incidents.from_alertmanager({"alerts": [{"status": "firing", "fingerprint": "f5", "labels": {
        "alertname": "payment_without_token", "severity": "sev1", "team": "sre-oncall", "auto_action": "kill_switch"},
        "annotations": {"summary": "x"}}]})
    ledger.append("fault_injected", item=None, fault="F5", release="0.3.1", simulation=True)
    ledger.append("fault_detected", item=None, fault="F5", alert="payment_without_token", incident="INC-001",
                  team="sre-oncall", paged=["ravi"], acknowledged_by="elena", simulation=True)
    rows = {r["fault"]: r for r in report.detection_matrix()}
    assert rows["F1"]["passed"] and rows["F1"]["caught_by"] == "G3"
    assert not rows["F5"]["passed"]                                         # not escalated, not closed yet
    assert {k for k, ok in rows["F5"]["checks"].items() if not ok} == {"escalated", "closed_with_rca"}
    assert not rows["F3"]["passed"] and rows["F3"]["checks"] == {"caught": False}
    r = report.write()
    assert "| F1 Wrong routing configuration in a release |" in open(r["path"]).read()


def test_evidence_pack_lets_an_auditor_recheck_every_hash(sim_run):
    usecase.save(usecase.template(), by="po")
    usecase.submit(UC, by="po")
    usecase.confirm_classification(UC, by="dataowner")
    for p in ("hoc", "risk", "dataowner"):
        lifecycle.sign("G0", p)
    z = zipfile.ZipFile(io.BytesIO(ledger.evidence_pack(gate="G0")))
    [m] = [n for n in z.namelist() if n.startswith("manifests/")]
    man = json.loads(z.read(m))
    for a in man["artifacts"]:
        name = next(n for n in z.namelist() if n.startswith("artifacts/") and n.endswith(a["path"]))
        assert hashlib.sha256(z.read(name)).hexdigest() == a["sha256"]
    ev = [json.loads(x) for x in z.read("ledger.jsonl").decode().splitlines()]
    assert hashlib.sha256(z.read(m)).hexdigest() in {e.get("manifest_sha256") for e in ev}
    assert json.loads(z.read("verify.json"))["ok"]
    assert not [n for n in zipfile.ZipFile(io.BytesIO(ledger.evidence_pack(gate="G3"))).namelist() if n.startswith("manifests/")]


def test_epic_follows_production_and_shows_the_extension(sim_run):
    db.init_db()
    usecase.save(usecase.template(), by="po")
    usecase.submit(UC, by="po")
    releases.cut("0.3.1", by="relmgr", message="live release")
    ledger.append("deployed", item="0.3.1", phase="full", cr="CR-001", by="relmgr")
    data = lifecycle.records()
    for g in data["gates"]:
        if g["gate"] in ("G0", "G1", "G2", "G3", "G4", "G5"):
            g["status"] = "approved"
    g6 = next(g for g in data["gates"] if g["gate"] == "G6")
    g6.update(status="rejected", notes="extend")
    lifecycle._save_records(data)
    ledger.append("hypercare_extended", item="0.3.1", by="hoc", until_day=75, conditions="fix release first")
    releases.cut("0.3.2", by="relmgr", message="fix")                      # candidate: the epic stays with production
    card = board.epic()
    assert card["release"] == "0.3.1" and card["column"] == "G6" and card["sub_status"] == "Hypercare extended to day 75"
    assert changes.deployments("0.3.1")
