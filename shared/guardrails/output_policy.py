"""Customer-message output policy (guardrails/output-policy.yaml, REQ-B-008)."""
from __future__ import annotations

import re

from shared.config import load_yaml

POLICY = load_yaml("guardrails/output-policy.yaml")

BANNED = [r"\bfraud", r"\bSIU\b", r"\bconfidence\b", r"\bscore\b", r"\brouting\b", r"\brow \d\b",
          r"\brisk level\b", r"\banomal", r"\btype [AB]\b", r"\bL[12] (queue|adjuster)\b", r"\binjection\b"]


def check_message(body: str, *, outcome: str, deduction_clauses: list[str], is_decision: bool) -> list[str]:
    violations = []
    for clause in sorted(set(deduction_clauses)):
        if not re.search(rf"(clause|section)\s+{re.escape(clause)}\b", body, re.I):
            violations.append(f"must-cite-clause-for-deduction: {clause}")
    if is_decision and (deduction_clauses or outcome == "reject"):
        if not re.search(r"\bappeal\b", body, re.I):
            violations.append("must-include-appeal-rights-on-deduction-or-rejection")
    for pat in BANNED:
        if re.search(pat, body, re.I):
            violations.append(f"no-internal-scores-or-fraud-flags-in-customer-text: /{pat}/")
    return violations


def check_slot_text(text: str) -> list[str]:
    """LLM-written slot text (inside an approved template) must not leak internal terms."""
    return [f"banned term /{p}/" for p in BANNED if re.search(p, text or "", re.I)]
