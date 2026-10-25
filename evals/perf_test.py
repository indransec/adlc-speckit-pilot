"""Performance test (REQ-N-001 / REQ-N-004): 3x expected peak, p95 submission -> decision package <= 10 min.

  python evals/perf_test.py [release] [--claims 30] [--workers 10] [--latency-ms 400]

Isolated DATA_DIR; mock LLM with simulated model latency (or live models if a key is set
and --live is passed). Writes evidence/<release>/perf-report.json.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, "agents", "orchestrator", "src")):
    sys.path.insert(0, p)

EXPECTED_PEAK_CONCURRENT = 10   # [assumption] claims arriving together at peak
SLO_SECONDS = 600


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("release", nargs="?", default=None)
    ap.add_argument("--claims", type=int, default=3 * EXPECTED_PEAK_CONCURRENT)
    ap.add_argument("--workers", type=int, default=EXPECTED_PEAK_CONCURRENT)
    ap.add_argument("--latency-ms", type=int, default=400)
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    from shared.config import release
    a.release = a.release or release()
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="adlc-perf-")
    os.environ["MOCK_LLM_LATENCY_MS"] = str(a.latency_ms)
    os.environ.setdefault("TELEMETRY_ENABLED", "false")
    if not a.live:
        os.environ["OPENROUTER_API_KEY"] = ""  # empty, so load_dotenv cannot re-add a key from .env
    from backend import service
    from shared import db
    from shared.runtime import effective_llm_mode
    from synthetic.generate import add_traffic, setup_platform
    setup_platform()
    service.bootstrap()
    ids = add_traffic(a.claims, seed=99)
    service.graph()
    t0 = time.perf_counter()
    errors = 0
    with ThreadPoolExecutor(a.workers) as ex:
        for f in [ex.submit(service.process, cid) for cid in ids]:
            try:
                f.result()
            except Exception:
                errors += 1
    wall = time.perf_counter() - t0
    lat = sorted(r["package_latency_s"] for r in db.query("SELECT package_latency_s FROM claims WHERE package_latency_s IS NOT NULL"))
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))] if lat else None
    report = {"release": a.release, "generated_at": datetime.now(timezone.utc).isoformat(), "llm_mode": effective_llm_mode(),
              "claims": a.claims, "concurrency": a.workers, "load_factor_vs_peak": round(a.workers / EXPECTED_PEAK_CONCURRENT * 3, 1),
              "simulated_model_latency_ms": a.latency_ms if effective_llm_mode() == "mock" else None,
              "p95_package_latency_s": p95, "max_package_latency_s": lat[-1] if lat else None, "errors": errors,
              "throughput_claims_per_min": round(60 * a.claims / wall, 1), "wall_s": round(wall, 1),
              "slo_seconds": SLO_SECONDS, "passed": errors == 0 and p95 is not None and p95 <= SLO_SECONDS}
    out = os.path.join(ROOT, "evidence", a.release)
    os.makedirs(out, exist_ok=True)
    json.dump(report, open(os.path.join(out, "perf-report.json"), "w"), indent=2)
    print(json.dumps(report, indent=1))
    print(f"p95 {p95}s at {a.workers} concurrent ({a.claims} claims), errors {errors}, passed={report['passed']} "
          f"-> evidence/{a.release}/perf-report.json")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
