"""SQLite relational store (claims system of record, audit, telemetry, gate records)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable

from shared.config import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  user_id TEXT PRIMARY KEY, name TEXT, role TEXT, authority INTEGER);

CREATE TABLE IF NOT EXISTS policies(
  policy_no TEXT PRIMARY KEY, plan TEXT, holder_name TEXT, insured_persons TEXT,
  start_date TEXT, end_date TEXT, purchased_at TEXT, region TEXT);

CREATE TABLE IF NOT EXISTS flights(
  flight_no TEXT, flight_date TEXT, origin TEXT, destination TEXT,
  scheduled_dep TEXT, actual_dep TEXT, delay_minutes INTEGER, status TEXT,
  cause TEXT, cause_announced_at TEXT, PRIMARY KEY(flight_no, flight_date));

CREATE TABLE IF NOT EXISTS claims(
  claim_id TEXT PRIMARY KEY, policy_no TEXT, trip_id TEXT,
  claimant_name TEXT, claimant_email TEXT, claimant_phone TEXT, claimant_address TEXT,
  passport_no TEXT, bank_account_holder TEXT, bank_account_no TEXT, bank_code TEXT,
  nationality_group TEXT, destination_region TEXT, age_band TEXT, channel TEXT,
  flight_no TEXT, flight_date TEXT, booking_date TEXT,
  claim_type_declared TEXT, claim_type TEXT, category TEXT,
  claimed_usd REAL, compensation_received_usd REAL DEFAULT 0, lines_json TEXT, description TEXT,
  scenario TEXT, ground_truth TEXT, historical INTEGER DEFAULT 0,
  submitted_at TEXT, status TEXT, queue TEXT, queue_since TEXT,
  route TEXT, route_row INTEGER, approvals_required INTEGER, recommendation TEXT,
  recommended_usd REAL, approved_usd REAL, confidence REAL, fraud_risk TEXT,
  mode TEXT, thread_id TEXT, run_no INTEGER DEFAULT 0,
  processing_started_at TEXT, package_at TEXT, package_latency_s REAL, closed_at TEXT,
  docs_requested_at TEXT, reminders_sent INTEGER DEFAULT 0, sim_days_waiting INTEGER DEFAULT 0,
  decided_by TEXT, release TEXT, error TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS documents(
  doc_id TEXT PRIMARY KEY, claim_id TEXT, doc_type TEXT, filename TEXT, object_key TEXT,
  uploaded_at TEXT, extracted TEXT, extraction_certainty REAL);

CREATE TABLE IF NOT EXISTS agent_outputs(
  claim_id TEXT, run_no INTEGER, agent_id TEXT, agent_version TEXT, output TEXT, created_at TEXT,
  PRIMARY KEY(claim_id, run_no, agent_id));

CREATE TABLE IF NOT EXISTS decision_packages(
  claim_id TEXT, run_no INTEGER, package TEXT, created_at TEXT, PRIMARY KEY(claim_id, run_no));

CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT, level TEXT, user_id TEXT, decision TEXT,
  outcome TEXT, amount_usd REAL, override INTEGER, notes TEXT, queued_at TEXT, decided_at TEXT, release TEXT);

CREATE TABLE IF NOT EXISTS payments(
  payment_id TEXT PRIMARY KEY, claim_id TEXT, amount_usd REAL, payee_name TEXT, account_masked TEXT,
  token_jti TEXT, token_verified INTEGER, approvers TEXT, status TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS used_tokens(jti TEXT PRIMARY KEY, claim_id TEXT, used_at TEXT);

CREATE TABLE IF NOT EXISTS control_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, claim_id TEXT, control TEXT, outcome TEXT, detail TEXT);

CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT, template TEXT, template_version TEXT,
  subject TEXT, body TEXT, channel TEXT, sent_at TEXT, policy_ok INTEGER, violations TEXT);

CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, claim_id TEXT, run_no INTEGER, actor TEXT,
  agent_id TEXT, agent_version TEXT, release TEXT, event TEXT, payload TEXT, duration_ms REAL);

-- llm_calls and tool_calls are written by the telemetry sink from OpenInference / gateway spans
CREATE TABLE IF NOT EXISTS llm_calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, claim_id TEXT, agent_id TEXT, task TEXT, model TEXT,
  tier TEXT, mode TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, cost_usd REAL,
  latency_ms REAL, status TEXT, degraded INTEGER, input_excerpt TEXT, run_no INTEGER,
  agent_version TEXT, trace_id TEXT, span_id TEXT);

CREATE TABLE IF NOT EXISTS tool_calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, claim_id TEXT, run_no INTEGER, agent_id TEXT, caller TEXT,
  tool_id TEXT, operation TEXT, access TEXT, invoked_by TEXT, status TEXT, latency_ms REAL, trace_id TEXT);

CREATE TABLE IF NOT EXISTS guardrail_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, claim_id TEXT, agent_id TEXT, guardrail TEXT,
  action TEXT, detail TEXT);

CREATE TABLE IF NOT EXISTS runtime_config(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT, updated_by TEXT);

CREATE TABLE IF NOT EXISTS gate_records(
  release TEXT, gate TEXT, status TEXT, approvers TEXT, decided_at TEXT, evidence TEXT, checks TEXT,
  PRIMARY KEY(release, gate));

CREATE TABLE IF NOT EXISTS jobs(
  job_id TEXT PRIMARY KEY, action TEXT, status TEXT, started_at TEXT, finished_at TEXT, output TEXT);

CREATE INDEX IF NOT EXISTS ix_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS ix_claims_trip ON claims(trip_id);
CREATE INDEX IF NOT EXISTS ix_audit_claim ON audit_log(claim_id);
CREATE INDEX IF NOT EXISTS ix_llm_claim ON llm_calls(claim_id);
CREATE INDEX IF NOT EXISTS ix_tool_claim ON tool_calls(claim_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


MIGRATIONS = {"llm_calls": {"run_no": "INTEGER", "agent_version": "TEXT", "trace_id": "TEXT", "span_id": "TEXT"}}


def init_db() -> None:
    with tx() as c:
        for table, cols in MIGRATIONS.items():  # add columns to data dirs created by an earlier release
            have = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
            for col, typ in cols.items():
                if have and col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        c.executescript(SCHEMA)


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(sql, tuple(params)).fetchall()]


def one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def scalar(sql: str, params: Iterable[Any] = ()):
    with tx() as c:
        r = c.execute(sql, tuple(params)).fetchone()
        return r[0] if r else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with tx() as c:
        return c.execute(sql, tuple(params)).lastrowid


def insert(table: str, row: dict) -> int:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    return execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", row.values())


def upsert(table: str, row: dict) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})", row.values())


def update(table: str, key: str, key_value: Any, fields: dict) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE {table} SET {sets} WHERE {key} = ?", [*fields.values(), key_value])


def jdump(v: Any) -> str:
    return json.dumps(v, default=str)


def jload(v: str | None, default: Any = None) -> Any:
    return json.loads(v) if v else default
