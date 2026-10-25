"""Mock customer notification service (TOOL-NOTIFY). Sends only approved templates
(agents/communication/templates/approved.yaml) and enforces guardrails/output-policy.yaml."""
from __future__ import annotations

from functools import lru_cache

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from shared import db
from shared.config import ROOT, load_yaml
from shared.guardrails.output_policy import check_message

OPERATIONS = {"send": "send-from-template"}
TEMPLATE_DIR = ROOT / "agents" / "communication" / "templates"


class TemplateNotApproved(PermissionError):
    pass


@lru_cache
def approved() -> dict:
    return {t["id"]: t for t in load_yaml(TEMPLATE_DIR / "approved.yaml")["templates"]}


@lru_cache
def _env():
    return Environment(loader=FileSystemLoader(TEMPLATE_DIR), undefined=StrictUndefined,
                       trim_blocks=True, lstrip_blocks=True)


def render(template: str, context: dict) -> tuple[str, str]:
    t = approved().get(template)
    if not t or t.get("status") != "approved":
        raise TemplateNotApproved(f"template {template} is not compliance-approved")
    body = _env().get_template(t["file"]).render(**context)
    return t["subject"].format(**context), body


def send(claim_id: str, template: str, context: dict) -> dict:
    subject, body = render(template, context)
    t = approved()[template]
    violations = check_message(body, outcome=context.get("outcome", ""),
                               deduction_clauses=[d["clause"] for d in context.get("deductions", [])],
                               is_decision=t.get("decision", False))
    ok = not violations
    db.insert("notifications", {"claim_id": claim_id, "template": template, "template_version": t["version"],
                                "subject": subject, "body": body, "channel": "email",
                                "sent_at": db.now() if ok else None, "policy_ok": int(ok),
                                "violations": db.jdump(violations)})
    return {"subject": subject, "body": body, "sent": ok, "violations": violations}
