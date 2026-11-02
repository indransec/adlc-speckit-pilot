"""CI gate status for a pull request (ADLC, pilot finding 2026-11-02).

Reads the engine's committed gate records (evidence/<release>/gate-records.json, generated, never
hand-edited) for the release in system.yaml and fails unless the required gates were decided by people.
No application dependencies, so it runs on a clean checkout.

    python scripts/gate_status.py --require G3 --show G4 --show G5

The hash-chained ledger and manifests are not in git (evidence store), so this checks recorded decisions,
not the ledger chain; `adlc.py verify-ledger` stays the authority on the evidence store.
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECIDED_OK = {"approved", "approved_with_conditions"}


def release() -> str:
    text = open(os.path.join(ROOT, "system.yaml")).read()
    m = re.search(r"(?m)^release:\s*([\w.\-]+)", text)
    if not m:
        sys.exit("system.yaml has no release")
    return m.group(1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--require", action="append", default=[], help="gate that must be approved, e.g. G3")
    ap.add_argument("--show", action="append", default=[], help="gate to report without failing")
    a = ap.parse_args()
    rel = release()
    path = os.path.join(ROOT, "evidence", rel, "gate-records.json")
    if not os.path.exists(path):
        print(f"::error::no gate records for release {rel} ({path})")
        return 1
    gates = {g["gate"]: g for g in json.load(open(path))["gates"]}
    failed = False
    for gate in a.require + a.show:
        g = gates.get(gate, {"status": "missing"})
        who = ", ".join(f"{x.get('role')}: {x.get('name')} ({x.get('decision')})" for x in g.get("approvers") or [])
        line = f"{rel} {gate}: {g['status']}" + (f" at {g.get('decided_at')} by {who}" if who else "")
        required = gate in a.require
        if required and g["status"] not in DECIDED_OK:
            print(f"::error::{line} - required before merging")
            failed = True
        else:
            print(("::notice::" if required else "") + line + ("" if required else " (informational)"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
