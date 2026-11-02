"""Evidence ledger: content-addressed artifact store + hash-chained, append-only event log.

  <EVIDENCE_DIR>/artifacts/sha256/ab/abcd…   read-only copy of every artifact a decision was made on
  <EVIDENCE_DIR>/ledger.jsonl                one JSON event per line, each carrying the previous line's hash
  <EVIDENCE_DIR>/manifests/<release>/…       human-readable copy of each approval / rejection manifest

Freezing a gate decision hashes every artifact listed for that gate in adlc/gate_artifacts.yaml,
copies the bytes into the store (the file name is the hash, so identical content is stored once),
writes a manifest of the decision (approvers, check results, artifacts) and appends one ledger
event that points at the manifest's hash. Editing any earlier event breaks every later link;
editing a stored copy no longer matches its name. verify() checks both. drift() re-hashes the
live files behind earlier approvals, so a change made after approval is visible at later gates.

The ledger detects tampering; it does not prevent it (anyone with write access could rebuild a
consistent chain). Production adds WORM storage (S3 Object Lock) and signatures (cosign / Rekor).
"""
from __future__ import annotations

import fcntl
import glob
import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml

from shared import clock
from shared.config import evidence_root, project_root

GENESIS = "0" * 64
GATE_ORDER = ["G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7"]
DECISION_EVENTS = {"approved": "gate_approved", "approved_with_conditions": "gate_approved", "rejected": "gate_rejected"}

_tlock = threading.RLock()
_depth = threading.local()


class LedgerError(ValueError):
    pass


def ledger_path() -> Path:
    return evidence_root() / "ledger.jsonl"


def store_dir() -> Path:
    return evidence_root() / "artifacts" / "sha256"


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@contextmanager
def locked():
    """Serialise ledger writers across threads and processes (re-entrant within a thread)."""
    with _tlock:
        depth = getattr(_depth, "n", 0)
        if depth:
            _depth.n = depth + 1
            try:
                yield
            finally:
                _depth.n -= 1
            return
        p = ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p.with_suffix(".lock"), "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            _depth.n = 1
            try:
                yield
            finally:
                _depth.n = 0
                fcntl.flock(fh, fcntl.LOCK_UN)


# ------------------------------------------------------------------------------- artifact store
def _store_path(digest: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        raise LedgerError(f"not a sha256 digest: {digest!r}")
    return store_dir() / digest[:2] / digest


def put_bytes(data: bytes) -> str:
    digest = sha256(data)
    p = _store_path(digest)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.chmod(tmp, 0o444)
        os.replace(tmp, p)
    if sha256(p.read_bytes()) != digest:
        raise LedgerError(f"stored copy {digest[:12]} does not match its content")
    return digest


def get_bytes(digest: str) -> bytes:
    p = _store_path(digest)
    if not p.exists():
        raise LedgerError(f"artifact {digest[:12]} not in the store")
    return p.read_bytes()


def manifest(digest: str) -> dict:
    return json.loads(get_bytes(digest))


# ------------------------------------------------------------------------------- artifact paths
def _base(path: str) -> tuple[Path, str]:
    return (evidence_root(), path[len("evidence:"):]) if path.startswith("evidence:") else (project_root(), path)


def read_artifact(path: str) -> bytes | None:
    """Bytes that represent an artifact: the file, or for 'file.yaml#a,b' the canonical JSON of those keys."""
    rel, _, selector = path.partition("#")
    base, rel = _base(rel)
    p = base / rel
    if not p.is_file():
        return None
    if not selector:
        return p.read_bytes()
    data = yaml.safe_load(p.read_text()) or {}
    return canonical({k: data.get(k) for k in selector.split(",")})


def expand(spec_path: str, ctx: dict) -> list[str]:
    """Placeholders ({use_case}, {release}) and globs -> concrete artifact paths (missing files kept as-is)."""
    path = spec_path.format(**ctx)
    rel, hash_, selector = path.partition("#")
    if "*" not in rel:
        return [path]
    base, pattern = _base(rel)
    prefix = "evidence:" if rel.startswith("evidence:") else ""
    hits = sorted(os.path.relpath(h, base) for h in glob.glob(str(base / pattern)))
    return [f"{prefix}{h}{hash_}{selector}" for h in hits]


def catalog() -> dict:
    return yaml.safe_load((project_root() / "adlc" / "gate_artifacts.yaml").read_text())["gates"]


def specs(gate: str, structured: bool) -> list[dict]:
    """Catalogue entries for a gate. structured = the use case was captured through the intake form;
    legacy = hand-written documents (the repo outside a simulation run)."""
    mode = "structured" if structured else "legacy"
    return [{**s, "required": s.get("required", False) in (True, mode), "on_drift": s.get("on_drift", "flag")}
            for s in catalog().get(gate, []) if s.get("skip_when") != mode]


# ------------------------------------------------------------------------------- event log
def events() -> list[dict]:
    p = ledger_path()
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def append(type_: str, **fields) -> dict:
    with locked():
        evs = events()
        prev = evs[-1]["hash"] if evs else GENESIS
        rec = {"seq": len(evs) + 1, "type": type_, "ts": clock.now_iso(),
               "wall": datetime.now(timezone.utc).isoformat(timespec="seconds"), **fields, "prev_hash": prev}
        rec["hash"] = sha256(canonical(rec))
        with open(ledger_path(), "a") as f:
            f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return rec


def for_item(item: str) -> list[dict]:
    return [e for e in events() if e.get("item") == item]


def head() -> str:
    evs = events()
    return evs[-1]["hash"] if evs else GENESIS


# ------------------------------------------------------------------------------- freeze
def freeze(*, gate: str, item: str, release: str, decision: str, approvers: list[dict], checks: list[dict],
           notes: str, ctx: dict, structured: bool) -> dict:
    """Snapshot the artifacts behind a gate decision and record it. Approvals need every required artifact."""
    artifacts, missing = [], []
    for s in specs(gate, structured):
        paths = expand(s["path"], ctx)
        if not paths and s["required"]:
            missing.append(s["path"].format(**ctx))
        for path in paths:
            data = read_artifact(path)
            if data is None:
                if s["required"]:
                    missing.append(path)
                continue
            artifacts.append({"name": s["name"], "path": path, "sha256": put_bytes(data), "bytes": len(data),
                              "required": s["required"], "on_drift": s["on_drift"]})
    if missing and decision != "rejected":
        raise LedgerError(f"{gate}: required artifacts missing: {', '.join(missing)}")
    doc = {"kind": "adlc-gate-decision", "version": 1, "gate": gate, "item": item, "release": release,
           "decision": decision, "approvers": approvers, "notes": notes, "checks": checks,
           "artifacts": artifacts, "missing": missing, "decided_at": clock.now_iso(),
           "wall_time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with locked():
        digest = put_bytes(canonical(doc))
        readable = evidence_root() / "manifests" / release / f"{gate}-{decision}-{digest[:12]}.json"
        readable.parent.mkdir(parents=True, exist_ok=True)
        readable.write_text(json.dumps(doc, indent=2, default=str))
        ev = append(DECISION_EVENTS[decision], item=item, gate=gate, release=release, decision=decision,
                    manifest_sha256=digest, artifacts=len(artifacts),
                    approvers=[a.get("persona") or a.get("name") for a in approvers])
    return {"manifest": doc, "manifest_sha256": digest, "event": ev}


def live_status(artifact: dict) -> str:
    data = read_artifact(artifact["path"])
    if data is None:
        return "missing"
    return "verified" if sha256(data) == artifact["sha256"] else "modified"


def latest_approvals(item: str | None = None) -> dict[str, dict]:
    out = {}
    for e in events():
        if e["type"] == "gate_approved" and (item is None or e.get("item") == item):
            out[e["gate"]] = e
    return out


def drift(before_gate: str | None = None, item: str | None = None) -> list[dict]:
    """Artifacts of the latest approval of each earlier gate whose live content no longer matches."""
    limit = GATE_ORDER.index(before_gate) if before_gate in GATE_ORDER else len(GATE_ORDER)
    out = []
    for gate, ev in latest_approvals(item).items():
        if GATE_ORDER.index(gate) >= limit:
            continue
        for a in manifest(ev["manifest_sha256"])["artifacts"]:
            status = live_status(a)
            if status != "verified":
                out.append({"gate": gate, "name": a["name"], "path": a["path"], "status": status, "on_drift": a["on_drift"]})
    return out


# ------------------------------------------------------------------------------- verify
def verify() -> dict:
    problems, prev, n_manifests, n_artifacts = [], GENESIS, 0, 0
    p = ledger_path()
    lines = p.read_text().splitlines() if p.exists() else []
    for i, line in enumerate(lines, start=1):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"event {i}: not valid JSON")
            prev = None
            continue
        if rec.get("seq") != i:
            problems.append(f"event {i}: sequence number {rec.get('seq')} out of order")
        if prev is not None and rec.get("prev_hash") != prev:
            problems.append(f"event {i}: previous-hash link broken")
        body = {k: v for k, v in rec.items() if k != "hash"}
        if sha256(canonical(body)) != rec.get("hash"):
            problems.append(f"event {i} ({rec.get('type')}): content does not match its hash")
        prev = rec.get("hash")
        if "manifest_sha256" in rec:
            n_manifests += 1
            try:
                data = get_bytes(rec["manifest_sha256"])
            except LedgerError as e:
                problems.append(f"event {i}: {e}")
                continue
            if sha256(data) != rec["manifest_sha256"]:
                problems.append(f"event {i}: manifest {rec['manifest_sha256'][:12]} was modified")
                continue
            for a in json.loads(data)["artifacts"]:
                n_artifacts += 1
                try:
                    if sha256(get_bytes(a["sha256"])) != a["sha256"]:
                        problems.append(f"event {i}: stored copy of {a['path']} was modified")
                except LedgerError as e:
                    problems.append(f"event {i}: {a['path']}: {e}")
    return {"ok": not problems, "events": len(lines), "manifests": n_manifests, "artifacts_checked": n_artifacts,
            "head": prev if lines else GENESIS, "problems": problems}


# ------------------------------------------------------------------------------- evidence packs
PACK_README = """Evidence pack - ADLC gate decisions

manifests/   one JSON manifest per gate decision (approvers, check results, artifacts with SHA-256)
artifacts/   the exact bytes each decision was made on, as frozen at decision time
ledger.jsonl the complete hash-chained event log; each line carries the hash of the line before it
verify.json  the ledger check at export time

To verify independently: every file under artifacts/ must hash (sha256sum) to the sha256 its manifest lists;
every manifest must hash to the manifest_sha256 of its ledger event; each ledger line's "hash" is the SHA-256
of the line's canonical JSON without "hash", and equals the next line's "prev_hash".
"""


def evidence_pack(release: str | None = None, gate: str | None = None) -> bytes:
    """A zip an auditor can check without the application: manifests, artifact copies, the ledger, its verification."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", PACK_README)
        for e in events():
            if e["type"] not in DECISION_EVENTS.values() or (release and e.get("release") != release) or (gate and e.get("gate") != gate):
                continue
            name = f"{e['release']}/{e['gate']}-{e['decision']}-{e['manifest_sha256'][:12]}"
            z.writestr(f"manifests/{name}.json", get_bytes(e["manifest_sha256"]))
            for a in manifest(e["manifest_sha256"])["artifacts"]:
                z.writestr(f"artifacts/{name}/{a['path'].replace('evidence:', 'evidence/').replace('#', '__')}", get_bytes(a["sha256"]))
        if ledger_path().exists():
            z.writestr("ledger.jsonl", ledger_path().read_bytes())
        z.writestr("verify.json", json.dumps(verify(), indent=2))
    return buf.getvalue()
