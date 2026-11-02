"""PII redaction guardrail (guardrails/pii-redaction.yaml, REQ-B-009).

Three layers, strongest first:
1. Known values: the claimant's own passport, bank, email, phone, address (exact match).
2. Label-anchored rules for form fields ("Passport number: ...", "Account number: ...").
3. Pattern detection: Microsoft Presidio (model inputs) or fast regexes (logs).
Bank / card data is also caught by a final gateway check before any model call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from shared.config import load_yaml

POLICY = load_yaml("guardrails/pii-redaction.yaml")

TOKEN = {
    "passport_number": "[REDACTED:PASSPORT]",
    "bank_account": "[REDACTED:BANK_ACCOUNT]",
    "card_number": "[REDACTED:CARD]",
    "phone": "[REDACTED:PHONE]",
    "email": "[REDACTED:EMAIL]",
    "address": "[REDACTED:ADDRESS]",
    "booking_reference": "[REDACTED:BOOKING_REF]",  # 0.4.0: itinerary booking references, logs only (data-flows.md)
}

LABELS = [
    ("passport_number", r"passport(?:\s*(?:number|no\.?|#))?"),
    ("bank_account", r"(?:bank\s*)?account\s*(?:number|no\.?|#)|iban|sort\s*code|bank\s*code|routing\s*number|account\s*holder"),
    ("card_number", r"card\s*(?:number|no\.?)"),
    ("phone", r"phone|mobile|tel(?:ephone)?"),
    ("email", r"e-?mail"),
    ("address", r"(?:home\s*|postal\s*)?address"),
]
LABEL_RE = [(k, re.compile(rf"(?im)^(\s*(?:{lbl})\s*[:=]\s*)(\S.*)$")) for k, lbl in LABELS]

PATTERNS = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("card_number", re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")),
    ("bank_account", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),          # IBAN
    ("bank_account", re.compile(r"(?<![\d.,-])\d{8,12}(?![\d.,-])")),            # bare account numbers
    ("phone", re.compile(r"(?<![\w-])\+\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?![\w-])")),
    ("passport_number", re.compile(r"\b[A-Z]{1,2}\d{7,8}\b")),
    ("address", re.compile(r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s){1,4}(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Boulevard|Blvd|Drive|Dr|Way|Close|Court|Place)\b")),
]

PRESIDIO_MAP = {"EMAIL_ADDRESS": "email", "PHONE_NUMBER": "phone", "CREDIT_CARD": "card_number",
                "IBAN_CODE": "bank_account", "US_BANK_NUMBER": "bank_account", "PASSPORT": "passport_number"}

# Business identifiers and dates are never PII; pattern detectors must not touch them.
PROTECTED = re.compile(r"\b(?:TRV|CLM|TRIP|PAY|GLD|ADV)-[A-Z0-9-]+\b|\b\d{4}-\d{2}-\d{2}\b|\b[A-Z]{2}\d{2,4}\b")

KNOWN_FIELDS = {"passport_no": "passport_number", "bank_account_no": "bank_account", "bank_code": "bank_account",
                "claimant_email": "email", "claimant_phone": "phone", "claimant_address": "address",
                "booking_ref": "booking_reference", "booking_reference": "booking_reference"}


@dataclass
class Redaction:
    text: str
    counts: dict = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@lru_cache
def _analyzer():
    import logging
    logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    nlp = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]}).create_engine()
    engine = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
    engine.registry.add_recognizer(PatternRecognizer(
        supported_entity="PASSPORT", context=["passport"],
        patterns=[Pattern("passport", r"\b[A-Z]{1,2}\d{7,8}\b", 0.6)]))
    return engine


def _bump(counts: dict, kind: str, n: int = 1):
    if n:
        counts[kind] = counts.get(kind, 0) + n


def redact(text: str, *, purpose: str = "model", known: dict | None = None) -> Redaction:
    """purpose='model' uses Presidio; purpose='log' uses fast regexes only."""
    if not text:
        return Redaction(text or "")
    counts: dict = {}
    out = text
    for fld, kind in KNOWN_FIELDS.items():
        val = (known or {}).get(fld)
        if val and len(str(val)) >= 4 and str(val) in out:
            _bump(counts, kind, out.count(str(val)))
            out = out.replace(str(val), TOKEN[kind])
    for kind, rx in LABEL_RE:
        def _sub(m, kind=kind):
            if m.group(2).startswith("[REDACTED"):
                return m.group(0)
            _bump(counts, kind)
            return m.group(1) + TOKEN[kind]
        out = rx.sub(_sub, out)
    if purpose == "model":
        results = _analyzer().analyze(text=out, language="en", entities=list(PRESIDIO_MAP), score_threshold=0.5)
        protected = [(m.start(), m.end()) for m in PROTECTED.finditer(out)]
        for r in sorted(results, key=lambda r: r.start, reverse=True):
            span = out[r.start:r.end]
            if "[REDACTED" in span or "REDACTED" in out[max(0, r.start - 10):r.start]:
                continue
            if any(a < r.end and r.start < b for a, b in protected):
                continue
            if r.entity_type == "PHONE_NUMBER" and sum(ch.isdigit() for ch in span) < 9:
                continue
            kind = PRESIDIO_MAP[r.entity_type]
            _bump(counts, kind)
            out = out[:r.start] + TOKEN[kind] + out[r.end:]
    for kind, rx in PATTERNS:
        n = len(rx.findall(out))
        if n:
            _bump(counts, kind, n)
            out = rx.sub(TOKEN[kind], out)
    return Redaction(out, counts)


def redact_for_model(text: str, *, known: dict | None = None, claim_id: str | None = None,
                     agent_id: str | None = None) -> str:
    r = redact(text, purpose="model", known=known)
    if r.total:
        from shared.audit import guardrail_event
        guardrail_event(claim_id, "pii-redaction", "redact-and-log-event",
                        ", ".join(f"{k}={v}" for k, v in sorted(r.counts.items())), agent_id)
    return r.text


def restricted_present(text: str) -> list[str]:
    """never_send_to_model check (bank_account, card_number) used by the LLM gateway."""
    found = []
    for kind, rx in PATTERNS:
        if kind in POLICY["never_send_to_model"] and rx.search(text or ""):
            found.append(kind)
    for kind, rx in LABEL_RE:
        if kind in POLICY["never_send_to_model"]:
            for m in rx.finditer(text or ""):
                if not m.group(2).startswith("[REDACTED"):
                    found.append(kind)
    return found


def redact_obj_for_log(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact(obj, purpose="log").text
    if isinstance(obj, dict):
        return {k: (TOKEN.get(KNOWN_FIELDS.get(k, ""), None) or redact_obj_for_log(v)) if k in KNOWN_FIELDS
                else redact_obj_for_log(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj_for_log(v) for v in obj]
    return obj


def find_leaks(text: str, sensitive_values: list[str]) -> list[str]:
    """Eval helper: which known sensitive values appear verbatim in text."""
    return [v for v in sensitive_values if v and len(v) >= 4 and v in (text or "")]
