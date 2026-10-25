"""Run unit tests and write evidence/<release>/test-report.json (per test module pass/fail)."""
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(release="0.1.0"):
    xml = os.path.join(tempfile.mkdtemp(), "junit.xml")
    proc = subprocess.run([sys.executable, "-m", "pytest", f"--junitxml={xml}", "-o", "junit_family=xunit1", "-q"],
                          cwd=ROOT, capture_output=True, text=True)
    modules, total, failed = {}, 0, 0
    for tc in ET.parse(xml).getroot().iter("testcase"):
        f = tc.get("file") or tc.get("classname", "").replace(".", "/") + ".py"
        bad = tc.find("failure") is not None or tc.find("error") is not None
        total += 1
        failed += bad
        modules[f] = "failed" if bad or modules.get(f) == "failed" else "passed"
    report = {"release": release, "generated_at": datetime.now(timezone.utc).isoformat(), "tests": total,
              "failed": failed, "passed": proc.returncode == 0, "modules": modules,
              "summary": next((l for l in reversed(proc.stdout.splitlines()) if " passed" in l or " failed" in l), "")}
    out = os.path.join(ROOT, "evidence", release)
    os.makedirs(out, exist_ok=True)
    json.dump(report, open(os.path.join(out, "test-report.json"), "w"), indent=2)
    print(f"{report['summary']} -> evidence/{release}/test-report.json")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:2] or [])))
