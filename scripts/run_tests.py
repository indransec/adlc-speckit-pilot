"""Run unit tests and write evidence/<release>/test-report.json (per test module pass/fail)."""
import json
import re
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _now() -> str:
    sys.path.insert(0, ROOT)
    from shared import clock  # simulation clock during a simulation run, else real UTC
    return clock.now_iso()


def main(release=None):
    release = release or re.search(r"^release:\s*(\S+)", open(os.path.join(ROOT, "system.yaml")).read(), re.M).group(1)
    xml = os.path.join(tempfile.mkdtemp(), "junit.xml")
    # the test suite brings its own isolated environment; a simulation run's settings must not leak into it
    env = {k: v for k, v in os.environ.items()
           if k not in ("APP_ENV", "PROJECT_ROOT", "EVIDENCE_DIR", "SIM_CLOCK_FILE", "SIM_RUN_ID", "DATA_DIR")}
    proc = subprocess.run([sys.executable, "-m", "pytest", f"--junitxml={xml}", "-o", "junit_family=xunit1", "-q"],
                          cwd=ROOT, capture_output=True, text=True, env=env)
    modules, total, failed = {}, 0, 0
    for tc in ET.parse(xml).getroot().iter("testcase"):
        f = tc.get("file") or tc.get("classname", "").replace(".", "/") + ".py"
        bad = tc.find("failure") is not None or tc.find("error") is not None
        total += 1
        failed += bad
        modules[f] = "failed" if bad or modules.get(f) == "failed" else "passed"
    report = {"release": release, "generated_at": _now(), "tests": total,
              "failed": failed, "passed": proc.returncode == 0, "modules": modules,
              "summary": next((l for l in reversed(proc.stdout.splitlines()) if " passed" in l or " failed" in l), "")}
    out = os.path.join(os.environ.get("EVIDENCE_DIR") or os.path.join(ROOT, "evidence"), release)
    os.makedirs(out, exist_ok=True)
    json.dump(report, open(os.path.join(out, "test-report.json"), "w"), indent=2)
    print(f"{report['summary']} -> evidence/{release}/test-report.json")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:2] or [])))
