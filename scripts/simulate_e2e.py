"""Play the end-to-end ADLC story (sim/story_e2e.yaml) in a simulation run.

  python scripts/simulate_e2e.py                      new run, autopilot (personas are played by the runner)
  python scripts/simulate_e2e.py --interactive        you sign gates and decide change requests in the UI
  python scripts/simulate_e2e.py --run current --until-day -30
  python scripts/simulate_e2e.py --per-day 20 --min-shadow 100     smaller, faster shadow windows
  python scripts/simulate_e2e.py --live-evals         G3 eval runs on live Claude via OpenRouter (slow, costs credit)

Watch it in the UI: in other terminals run `make sim-api` and `make sim-ui` (the new run becomes current).
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "agents", "orchestrator", "src")]

from sim import workspace  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="new", help="new | current | <run id>")
    ap.add_argument("--story", default=os.path.join(ROOT, "sim", "story_e2e.yaml"))
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--until-day", type=float)
    ap.add_argument("--per-day", type=int)
    ap.add_argument("--min-shadow", type=int)
    ap.add_argument("--live-evals", action="store_true")
    a = ap.parse_args()

    if a.run == "new":
        run_id = workspace.create()["id"]
        print(f"created simulation run {run_id} (now current) - watch it with `make sim-api` + `make sim-ui`")
    else:
        run_id = workspace.current() if a.run == "current" else a.run
        if not run_id:
            sys.exit("no current simulation run: use --run new")
    os.environ.setdefault("TELEMETRY_ENABLED", "false")
    with workspace.run_env(workspace.load_env(run_id)):
        from backend import service
        from shared import ledger
        from sim.story import Story, StoryError, load
        from synthetic.generate import setup_platform
        setup_platform()          # policy wording PDF + clause index in the run's data dir
        service.bootstrap()
        overrides = {k: v for k, v in {"per_day": a.per_day, "min_shadow_claims": a.min_shadow,
                                       "live_evals": a.live_evals}.items() if v}
        try:
            Story(load(a.story), interactive=a.interactive, overrides=overrides).run(until_day=a.until_day)
        except StoryError as e:
            print(f"\nSTORY STOPPED: {e}")
            return 1
        v = ledger.verify()
        print(f"\nstory complete · ledger {'intact' if v['ok'] else 'BROKEN'}: {v['events']} events, "
              f"{v['manifests']} manifests, {v['artifacts_checked']} artifact copies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
