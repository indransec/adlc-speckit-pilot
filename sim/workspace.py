"""Simulation runs: an isolated workspace, data directory, evidence store and clock per run.

  sim/runs/<id>/workspace/   copy of the project content with its own git history (releases are commits)
  sim/runs/<id>/data/        SQLite, checkpoints, object store
  sim/runs/<id>/evidence/    evidence ledger, artifact store, gate records, reports
  sim/runs/<id>/clock.json   simulation clock shared by every process of the run
  sim/runs/<id>/run.env      environment for `make sim-api` / `make sim-ui`
  sim/runs/CURRENT           id of the run the make targets use

The code runs from the repo; the governed content (system.yaml, docs, prompts, routing rules, eval
thresholds) is read from the workspace, so the repo's own files and evidence are never modified.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from shared.config import ROOT

RUNS = ROOT / "sim" / "runs"
# usecase/: a governed repo's own intake record must not leak into a run, which starts from a blank intake
SKIP = {".git", ".venv", "data", ".cache", "evidence", "__pycache__", ".pytest_cache", "node_modules", ".DS_Store",
        ".env", ".idea", ".vscode", "usecase"}
GIT = ["git", "-c", "user.name=ADLC Simulator", "-c", "user.email=simulator@claims.sim", "-c", "commit.gpgsign=false"]
START_DAY = -60  # G0 intake, relative to day 0 (shadow start)


def _ignore(src_dir: str, names: list[str]) -> set[str]:
    skip = {n for n in names if n in SKIP or n.endswith(".pyc")}
    if Path(src_dir).resolve() == (ROOT / "sim").resolve():
        skip.add("runs")
    if Path(src_dir).resolve() == (ROOT / "deploy" / "sim").resolve():
        skip.add("generated")  # the alerting stack's generated config and GoAlert state are not project content
    return skip


def default_day0() -> datetime:
    """A Monday a little over 60 days out, so G0 intake (day -60) starts around now."""
    d = datetime.now(timezone.utc).date() + timedelta(days=61)
    d += timedelta(days=(7 - d.weekday()) % 7)
    return datetime.combine(d, time(0, 0), tzinfo=timezone.utc)


@contextmanager
def run_env(env: dict):
    """Apply a run's environment to this process for the duration of the block."""
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def load_env(run_id: str, runs_dir: Path = RUNS) -> dict:
    env = {}
    for line in (runs_dir / run_id / "run.env").read_text().splitlines():
        k, _, v = line.partition("=")
        env[k] = v.strip('"')
    return env


def current(runs_dir: Path = RUNS) -> str | None:
    p = runs_dir / "CURRENT"
    return p.read_text().strip() if p.exists() and (runs_dir / p.read_text().strip()).exists() else None


def list_runs(runs_dir: Path = RUNS) -> list[str]:
    return sorted(p.name for p in runs_dir.iterdir() if (p / "run.env").exists()) if runs_dir.exists() else []


def create(run_id: str | None = None, *, day0: datetime | None = None, rate: float = 1.0, start_day: float = START_DAY,
           runs_dir: Path = RUNS, make_current: bool = True) -> dict:
    run_id = run_id or datetime.now().strftime("run-%Y%m%d-%H%M%S")
    base = runs_dir / run_id
    if base.exists():
        raise FileExistsError(f"simulation run {run_id} already exists")
    ws = base / "workspace"
    shutil.copytree(ROOT, ws, ignore=_ignore)
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", f"baseline: project snapshot for simulation run {run_id}"]):
        subprocess.run([*GIT, *cmd], cwd=ws, check=True, capture_output=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws, capture_output=True, text=True).stdout.strip()
    (base / "data").mkdir()
    (base / "evidence").mkdir()
    env = {"APP_ENV": "sim", "SIM_RUN_ID": run_id, "PROJECT_ROOT": str(ws), "DATA_DIR": str(base / "data"),
           "EVIDENCE_DIR": str(base / "evidence"), "SIM_CLOCK_FILE": str(base / "clock.json")}
    (base / "run.env").write_text("".join(f'{k}="{v}"\n' for k, v in env.items()))
    d0 = day0 or default_day0()
    with run_env(env):
        from shared import clock, db, ledger
        from synthetic.materialize import seed_users
        clock.start(d0 + timedelta(days=start_day, hours=9), d0, rate)
        db.init_db()
        seed_users()
        ledger.append("simulation_run_created", item=None, run=run_id, workspace_commit=commit,
                      day0=d0.isoformat(), rate=rate)
    if make_current:
        (runs_dir / "CURRENT").write_text(run_id)
    return info(run_id, runs_dir)


def info(run_id: str, runs_dir: Path = RUNS) -> dict:
    env = load_env(run_id, runs_dir)
    with run_env(env):
        from shared import clock
        return {"id": run_id, "path": str(runs_dir / run_id), "env": env, "clock": clock.state()}


def remove(run_id: str, runs_dir: Path = RUNS) -> None:
    shutil.rmtree(runs_dir / run_id)
    if current(runs_dir) is None and (runs_dir / "CURRENT").exists():
        (runs_dir / "CURRENT").unlink()
