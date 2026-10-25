"""LLM-as-judge for customer letters (rubric: evals/judges/letter_rubric.md).

Live mode uses the reasoning-tier model through the gateway; mock mode applies the same
rubric deterministically. Calibrate against adjuster/compliance labels before relying on
live scores (rubric note).
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from shared.agent_runtime import run_agent
from shared.config import ROOT
from shared.registry import AgentContext

RUBRIC = (ROOT / "evals" / "judges" / "letter_rubric.md").read_text()
JUDGE_MANIFEST = {"id": "EVAL-JUDGE", "name": "letter-judge", "version": "1", "tools": [],
                  "model": {"name": "anthropic/claude-sonnet-5.5", "params": {"temperature": 0}}}


class LetterScores(BaseModel):
    outcome_clear: int = Field(ge=1, le=5)
    deductions_explained_with_clause: int = Field(ge=1, le=5)
    appeal_rights_where_required: int = Field(ge=1, le=5)
    plain_respectful_no_jargon: int = Field(ge=1, le=5)
    comment: str = ""


def rubric_heuristic(body: str, outcome: str, clauses: list[str]) -> LetterScores:
    outcome_ok = bool(re.search(r"approved in full|approved in part|unable to pay", body))
    clause_ok = all(re.search(rf"clause {re.escape(c)}\b", body, re.I) for c in clauses)
    appeal_needed = outcome != "approve"
    appeal_ok = (not appeal_needed) or "appeal" in body.lower()
    jargon = re.search(r"\b(SIU|fraud|score|routing|type [AB]|confidence)\b", body, re.I)
    return LetterScores(outcome_clear=5 if outcome_ok else 2, deductions_explained_with_clause=5 if clause_ok else 1,
                        appeal_rights_where_required=5 if appeal_ok else 1,
                        plain_respectful_no_jargon=2 if jargon else 5, comment="deterministic rubric")


def judge(body: str, outcome: str, clauses: list[str], claim_id: str | None = None) -> LetterScores:
    prompt = (f"Rubric:\n{RUBRIC}\n\nExpected outcome: {outcome}. Clauses that must be cited: {clauses}.\n"
              f"Letter:\n<letter>\n{body}\n</letter>\nScore each rubric item 1-5.")
    return run_agent(AgentContext(JUDGE_MANIFEST, claim_id, 0), task="judge_letter", schema=LetterScores,
                     prompt="You are a strict compliance reviewer of insurance customer letters.",
                     user_content=prompt, fallback=lambda: rubric_heuristic(body, outcome, clauses))


def passed(s: LetterScores) -> bool:
    return min(s.outcome_clear, s.deductions_explained_with_clause, s.appeal_rights_where_required,
               s.plain_respectful_no_jargon) >= 4
