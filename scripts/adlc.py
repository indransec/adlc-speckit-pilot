"""ADLC lifecycle CLI.

  python scripts/adlc.py status                       gate overview for the current release
  python scripts/adlc.py check G3                     automated evidence checks for one gate
  python scripts/adlc.py approve G3 --sim             sign with simulated named approvers
  python scripts/adlc.py approve G3 --approver "AI lead=Jane Doe" --approver "Product owner=Sam Lee"
  python scripts/adlc.py action evals                 run a lifecycle action (see `actions`)
  python scripts/adlc.py action shadow_run --n 20
  python scripts/adlc.py action set_phase --phase assisted
  python scripts/adlc.py simulate                     walk G0 -> G7 end to end with simulated approvers
  python scripts/adlc.py reset-gates [--from G3]
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "agents", "orchestrator", "src")]

from backend import service  # noqa: E402
from shared import lifecycle  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("actions")
    c = sub.add_parser("check"); c.add_argument("gate")
    a = sub.add_parser("approve"); a.add_argument("gate"); a.add_argument("--sim", action="store_true")
    a.add_argument("--approver", action="append", default=[], help='"Role=Name"'); a.add_argument("--notes", default="")
    a.add_argument("--decision", default="approved", choices=["approved", "rejected", "approved_with_conditions"])
    x = sub.add_parser("action"); x.add_argument("name"); x.add_argument("--n", type=int); x.add_argument("--phase")
    sub.add_parser("simulate")
    r = sub.add_parser("reset-gates"); r.add_argument("--from", dest="frm", default="G0")
    args = ap.parse_args()
    service.bootstrap()

    if args.cmd == "status":
        for g in lifecycle.all_gates():
            ok = sum(c["ok"] for c in g["checks"])
            print(f"{g['gate']}  {g['record']['status']:<10} checks {ok}/{len(g['checks'])}  ready={g['ready']!s:<5}  {g['stage']}")
    elif args.cmd == "actions":
        for k, (desc, _) in lifecycle.ACTIONS.items():
            print(f"{k:<20} {desc}")
    elif args.cmd == "check":
        g = lifecycle.check_gate(args.gate)
        for c in g["checks"]:
            print(f"[{'x' if c['ok'] else ' '}] {c['check']}: {c['detail']}")
        print(f"previous gate approved: {g['previous_gate_approved']}; approvers: {', '.join(g['approvers_required'])}")
        return 0 if g["ready"] else 1
    elif args.cmd == "approve":
        approvals = lifecycle.sim_approvals(args.gate) if args.sim else \
            [{"role": s.split("=", 1)[0], "name": s.split("=", 1)[1]} for s in args.approver]
        rec = lifecycle.approve(args.gate, approvals, args.decision, args.notes)
        print(f"{args.gate}: {rec['status']} by {[a['name'] for a in rec['approvers']]}")
    elif args.cmd == "action":
        kw = {k: v for k, v in {"n": args.n, "phase": args.phase}.items() if v is not None}
        r = lifecycle.run_action(args.name, **kw)
        print(r["output"])
        print(f"-> {r['status']}")
        return 0 if r["status"] == "succeeded" else 1
    elif args.cmd == "simulate":
        res = lifecycle.simulate()
        return 0 if all(g["record"]["status"] == "approved" for g in res) else 1
    elif args.cmd == "reset-gates":
        lifecycle.reset_gates(args.frm)
        print(f"gates from {args.frm} reset to pending")
    return 0


if __name__ == "__main__":
    sys.exit(main())
