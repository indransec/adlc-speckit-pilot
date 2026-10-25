"""Audit trail (REQ-N-006): every step with release id and agent version; PII redacted."""
from __future__ import annotations

from shared import db
from shared.config import release
from shared.guardrails.pii import redact_obj_for_log


def audit(claim_id: str | None, event: str, payload: dict | None = None, *, actor: str = "system",
          agent_id: str | None = None, agent_version: str | None = None, run_no: int | None = None,
          duration_ms: float | None = None) -> None:
    db.insert("audit_log", {
        "ts": db.now(), "claim_id": claim_id, "run_no": run_no, "actor": actor,
        "agent_id": agent_id, "agent_version": agent_version, "release": release(),
        "event": event, "payload": db.jdump(redact_obj_for_log(payload or {})), "duration_ms": duration_ms,
    })


def control_event(claim_id: str | None, control: str, outcome: str, detail: str = "") -> None:
    db.insert("control_events", {"ts": db.now(), "claim_id": claim_id, "control": control,
                                 "outcome": outcome, "detail": detail})


def guardrail_event(claim_id: str | None, guardrail: str, action: str, detail: str = "",
                    agent_id: str | None = None) -> None:
    db.insert("guardrail_events", {"ts": db.now(), "claim_id": claim_id, "agent_id": agent_id,
                                   "guardrail": guardrail, "action": action, "detail": detail})
