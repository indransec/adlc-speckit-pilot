"""Synthetic data generator.

  python -m synthetic.generate                 # reset data/ with demo claims + policy index
  python -m synthetic.generate --datasets      # (re)write versioned eval datasets under evals/datasets
"""
from __future__ import annotations

import argparse
import json
import random
import shutil

from shared import db, object_store
from shared.config import ROOT, data_dir
from shared.rag import WORDING_KEY, build_index
from synthetic import pdfs
from synthetic.materialize import materialize, seed_users
from synthetic.scenarios import SCENARIOS, TRAFFIC_WEIGHTS, build_case, build_split_trio

GOLDEN = ROOT / "evals" / "datasets" / "golden" / "claims_cases.jsonl"
ADVERSARIAL_E2E = ROOT / "evals" / "datasets" / "adversarial" / "e2e_cases.jsonl"
DATASET_SEED = 2026
ADVERSARIAL_SCENARIOS = {"injection": "ADV-001", "pii_request": "ADV-004",
                         # 0.4.0 missed connection (T046); used once labelled and added to SCENARIOS (T048)
                         "mc_injection": "ADV-005", "mc_altered_times": "ADV-006",
                         "mc_upgraded_class": "ADV-007", "mc_ambiguous": "ADV-008"}


def reset_data_dir() -> None:
    d = data_dir()
    for name in ("claims.db", "claims.db-wal", "claims.db-shm", "checkpoints.db", "checkpoints.db-wal",
                 "checkpoints.db-shm"):
        (d / name).unlink(missing_ok=True)
    for sub in ("object_store", "faiss"):
        shutil.rmtree(d / sub, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)


def setup_platform() -> int:
    """Schema, approvers, policy wording PDF and FAISS clause index."""
    db.init_db()
    seed_users()
    text = (ROOT / "synthetic" / "policy_wording.md").read_text().strip().splitlines()
    object_store.put(WORDING_KEY, pdfs.render_lines(text[0], text[1:]))
    return build_index()


def next_claim_ids(n: int) -> list[str]:
    last = db.scalar("SELECT MAX(CAST(SUBSTR(claim_id, 10, 5) AS INTEGER)) FROM claims "
                     "WHERE claim_id LIKE 'CLM-2026-_____'") or 0
    return [f"CLM-2026-{last + i:05d}" for i in range(1, n + 1)]


def used_flights() -> set:
    return {(r["flight_no"], r["flight_date"]) for r in db.query("SELECT flight_no, flight_date FROM flights")}


def add_traffic(n: int, seed: int | None = None, scenarios: dict | None = None) -> list[str]:
    """New claims with a production-like scenario mix (used by traffic / shadow simulations)."""
    rng = random.Random(seed)
    keys, weights = zip(*(scenarios or TRAFFIC_WEIGHTS).items())
    ids, used = next_claim_ids(n), used_flights()
    for cid in ids:
        materialize(build_case(rng.choices(keys, weights)[0], rng, cid, used_flights=used))
    return ids


def seed_demo(per_scenario: int = 2, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    ids, used = [], used_flights()
    for key in SCENARIOS:
        for _ in range(per_scenario):
            cid = next_claim_ids(1)[0]
            ids.append(materialize(build_case(key, rng, cid, used_flights=used)))
    for c in build_split_trio(rng, next_claim_ids(3), used):
        ids.append(materialize(c))
    return ids


def dataset_cases() -> tuple[list[dict], list[dict]]:
    rng = random.Random(DATASET_SEED)
    golden, adversarial, used = [], [], set()
    n = 0
    for key in SCENARIOS:
        if key in ADVERSARIAL_SCENARIOS:
            cid = ADVERSARIAL_SCENARIOS[key]
            adversarial.append({"case_id": cid, **build_case(key, rng, f"CLM-{cid}", used_flights=used)})
        else:
            n += 1
            cid = f"GLD-{n:03d}"
            golden.append({"case_id": cid, **build_case(key, rng, f"CLM-{cid}", used_flights=used)})
    for i, c in enumerate(build_split_trio(rng, ["CLM-ADV-002a", "CLM-ADV-002b", "CLM-ADV-002c"], used)):
        adversarial.append({"case_id": f"ADV-002{'abc'[i]}", "sequence": "ADV-002", **c})
    return golden, adversarial


def write_datasets() -> None:
    golden, adversarial = dataset_cases()
    GOLDEN.write_text("".join(json.dumps(c) + "\n" for c in golden))
    ADVERSARIAL_E2E.write_text("".join(json.dumps(c) + "\n" for c in adversarial))
    print(f"wrote {len(golden)} golden cases -> {GOLDEN.relative_to(ROOT)}")
    print(f"wrote {len(adversarial)} adversarial e2e cases -> {ADVERSARIAL_E2E.relative_to(ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", action="store_true", help="write eval datasets only")
    ap.add_argument("--per-scenario", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    if a.datasets:
        write_datasets()
        return
    reset_data_dir()
    n_clauses = setup_platform()
    ids = seed_demo(a.per_scenario, a.seed)
    print(f"data dir: {data_dir()}\n  policy clauses indexed in FAISS: {n_clauses}\n  demo claims: {len(ids)}")


if __name__ == "__main__":
    main()
