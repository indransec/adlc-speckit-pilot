"""Releases of the governed project: each release is a commit and tag in the project root's git history.

Cutting a release applies its file changes in the project root (a simulation run's workspace), sets
`release:` in system.yaml, commits as the persona who cut it, tags v<version> and logs `release_created`
in the evidence ledger. A release rejected at a gate and replaced by a newer one is closed
(`release_closed`), so the board shows where and why it stopped.
"""
from __future__ import annotations

import re
import subprocess

from shared import ledger, teams, usecase
from shared.config import project_root


class ReleaseError(ValueError):
    pass


def _git(*args: str, by: str | None = None) -> str:
    who = teams.persona(by) if by else {"name": "ADLC Simulator", "email": "simulator@claims.sim"}
    cmd = ["git", "-c", "commit.gpgsign=false", "-c", f"user.name={who['name']}", "-c", f"user.email={who['email']}", *args]
    return subprocess.run(cmd, cwd=project_root(), capture_output=True, text=True, check=True).stdout.strip()


def versions() -> list[str]:
    return [e["item"] for e in ledger.events() if e["type"] == "release_created"]


def cut(version: str, *, by: str, message: str, edits: list | None = None, files: dict | None = None) -> dict:
    """edits: [(path, old text, new text)] replaced once each; files: {path: full content}."""
    root = project_root()
    if not (root / ".git").exists():
        raise ReleaseError("releases are cut only in a simulation run's workspace")
    if version in versions():
        raise ReleaseError(f"release {version} already exists")
    teams.persona(by)
    touched = []
    for path, old, new in edits or []:
        text = (root / path).read_text()
        if old not in text:
            raise ReleaseError(f"{path}: text to change not found: {old!r}")
        (root / path).write_text(text.replace(old, new, 1))
        touched.append(path)
    for path, content in (files or {}).items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(content)
        touched.append(path)
    sysf = root / "system.yaml"
    sysf.write_text(re.sub(r"^release:\s*\S+", f"release: {version}", sysf.read_text(), count=1, flags=re.M))
    _git("add", "-A")
    _git("commit", "-q", "-m", f"release {version}: {message}", by=by)
    _git("tag", f"v{version}")
    commit = _git("rev-parse", "HEAD")
    ev = ledger.append("release_created", item=version, parent=usecase.current_id(), by=by, commit=commit,
                       message=message, changed=sorted({*touched, "system.yaml"}))
    return {"version": version, "commit": commit, "event": ev}


def close(version: str, *, state: str, reason: str, by: str) -> dict:
    """state: rejected_at_G3 / superseded / retired …"""
    if version not in versions():
        raise ReleaseError(f"unknown release {version}")
    teams.persona(by)
    return ledger.append("release_closed", item=version, parent=usecase.current_id(), state=state, reason=reason, by=by)


def history() -> list[dict]:
    closed = {e["item"]: e for e in ledger.events() if e["type"] == "release_closed"}
    return [{"version": e["item"], "commit": e["commit"], "by": e["by"], "message": e["message"], "created": e["ts"],
             "changed": e["changed"], "closed": closed.get(e["item"])}
            for e in ledger.events() if e["type"] == "release_created"]
