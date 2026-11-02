"""Simulation runs and the simulation clock.

  python scripts/sim_run.py new [--id RUN] [--day0 2027-01-04] [--rate 1]   isolated run, made current
  python scripts/sim_run.py list | current | info [--id RUN] | rm --id RUN
  python scripts/sim_run.py clock                                          show the clock of the current run
  python scripts/sim_run.py clock --pause | --resume | --rate 720
  python scripts/sim_run.py clock --skip-days 15 | --skip-to-day -45
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "agents", "orchestrator", "src")]

from sim import workspace  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("--id")
    n.add_argument("--day0", help="day 0 = shadow start (YYYY-MM-DD); default: a Monday ~61 days out")
    n.add_argument("--rate", type=float, default=1.0, help="simulated seconds per real second (720 = 1 day / 2 min)")
    sub.add_parser("list")
    sub.add_parser("current")
    i = sub.add_parser("info")
    i.add_argument("--id")
    r = sub.add_parser("rm")
    r.add_argument("--id", required=True)
    c = sub.add_parser("clock")
    c.add_argument("--id")
    g = c.add_mutually_exclusive_group()
    g.add_argument("--pause", action="store_true")
    g.add_argument("--resume", action="store_true")
    g.add_argument("--rate", type=float)
    g.add_argument("--skip-days", type=float)
    g.add_argument("--skip-to-day", type=float)
    a = ap.parse_args()

    if a.cmd == "new":
        day0 = datetime.fromisoformat(a.day0).replace(tzinfo=timezone.utc) if a.day0 else None
        run = workspace.create(a.id, day0=day0, rate=a.rate)
        print(f"created {run['id']} (now current)\n  {run['path']}\n  clock: {run['clock']['now']} "
              f"(sim day {run['clock']['sim_day']:.0f}, rate {run['clock']['rate']:g})\n"
              "next: make sim-api  and  make sim-ui")
    elif a.cmd == "list":
        cur = workspace.current()
        for rid in workspace.list_runs():
            print(f"{'*' if rid == cur else ' '} {rid}")
    elif a.cmd == "current":
        print(workspace.current() or "")
    elif a.cmd == "info":
        print(json.dumps(workspace.info(_run(a.id)), indent=2))
    elif a.cmd == "rm":
        workspace.remove(a.id)
        print(f"removed {a.id}")
    elif a.cmd == "clock":
        with workspace.run_env(workspace.load_env(_run(a.id))):
            from shared import clock
            if a.pause:
                clock.pause()
            elif a.resume:
                clock.resume()
            elif a.rate is not None:
                clock.set_rate(a.rate)
            elif a.skip_days is not None:
                clock.advance(a.skip_days * clock.DAY)
            elif a.skip_to_day is not None:
                clock.skip_to_day(a.skip_to_day)
            s = clock.state()
            print(f"{s['now']}  sim day {s['sim_day']:.2f}  rate {s['rate']:g}{'  (paused)' if s['paused'] else ''}")
    return 0


def _run(run_id):
    rid = run_id or workspace.current()
    if not rid:
        sys.exit("no simulation run: python scripts/sim_run.py new")
    return rid


if __name__ == "__main__":
    sys.exit(main())
