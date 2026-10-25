"""Rules engine for payable amounts (REQ-F-012). Exact arithmetic is code, never a model.

Order of application (documented in the decision package):
 per line : currency conversion at policy rate (6.2, A) -> judgement deductions (B) -> single item limit (5.2, A)
 per claim: valuables limit (5.3, A) -> compensation offset (3.4, A) -> baggage excess (5.6, A)
            -> section limit (3.6 / 4.3 / 5.7, A)
Type A clauses are produced only here; every judgement from the model is type B, so a
type B deduction can never be relabelled as type A (threat T9).
"""
from __future__ import annotations

from dataclasses import dataclass, field

TYPE_A_CLAUSES = {"3.4", "3.6", "4.3", "5.2", "5.3", "5.6", "5.7", "6.2"}
TYPE_B_CLAUSES = {"3.5", "5.4", "6.1", "6.3"}
SECTION_LIMIT = {"delay": ("delay_limit", "3.6"), "baggage_delay": ("bag_delay_limit", "4.3"),
                 "baggage_loss": ("bag_loss_limit", "5.7")}


@dataclass
class Judgement:
    line_id: str
    decision: str                # eligible | ineligible | depreciate
    clause: str | None = None
    reason: str = ""
    evidence_refs: list = field(default_factory=list)
    depreciation_pct: float = 0.0


def _r(x: float) -> float:
    return round(x + 1e-9, 2)


def calculate(claim_type: str, lines: list[dict], limits: dict, fx_rates: dict, judgements: dict[str, Judgement],
              compensation_usd: float = 0.0, evidence: dict | None = None) -> dict:
    evidence = evidence or {}
    deductions, remaining = [], {}

    def deduct(amount, dtype, clause, reason, refs, line_id=None):
        amount = _r(amount)
        if amount > 0:
            deductions.append({"line_id": line_id, "amount_usd": amount, "type": dtype, "clause": clause,
                               "reason": reason, "evidence_refs": list(refs)})
        return amount

    for ln in lines:
        lid, base = ln["line_id"], float(ln["claimed_usd"])
        if ln["currency"] != "USD" and ln["currency"] in fx_rates:
            policy_usd = _r(ln["amount_orig"] * fx_rates[ln["currency"]])
            if base > policy_usd + 0.005:
                deduct(base - policy_usd, "A", "6.2",
                       f"{ln['amount_orig']:.2f} {ln['currency']} at policy rate {fx_rates[ln['currency']]} = USD {policy_usd:.2f}",
                       [f"policy:6.2", *([ln["receipt_doc_id"]] if ln.get("receipt_doc_id") else [])], lid)
                base = policy_usd
        j = judgements.get(lid)
        if j and j.decision == "ineligible":
            base -= deduct(base, "B", j.clause or "?", j.reason, j.evidence_refs, lid)
        elif j and j.decision == "depreciate" and j.depreciation_pct > 0:
            base -= deduct(base * min(j.depreciation_pct, 50) / 100, "B", j.clause or "5.4", j.reason, j.evidence_refs, lid)
        if claim_type == "baggage_loss" and base > limits["single_item_limit"]:
            base -= deduct(base - limits["single_item_limit"], "A", "5.2",
                           f"single item limit USD {limits['single_item_limit']}", ["policy:5.2"], lid)
        remaining[lid] = _r(base)

    total = sum(remaining.values())
    if claim_type == "baggage_loss":
        val = sum(remaining[l["line_id"]] for l in lines if l.get("valuable"))
        if val > limits["valuables_limit"]:
            total -= deduct(val - limits["valuables_limit"], "A", "5.3",
                            f"valuables total USD {val:.2f} above limit USD {limits['valuables_limit']}", ["policy:5.3"])
    if claim_type == "delay" and compensation_usd > 0 and total > 0:
        total -= deduct(min(compensation_usd, total), "A", "3.4",
                        f"airline compensation USD {compensation_usd:.2f} already received",
                        ["policy:3.4", *evidence.get("compensation", [])])
    if claim_type == "baggage_loss" and limits.get("baggage_excess", 0) > 0 and total > 0:
        total -= deduct(min(limits["baggage_excess"], total), "A", "5.6",
                        f"baggage excess USD {limits['baggage_excess']}", ["policy:5.6"])
    key, clause = SECTION_LIMIT[claim_type]
    if total > limits[key]:
        total -= deduct(total - limits[key], "A", clause, f"section limit USD {limits[key]}", [f"policy:{clause}"])
    claimed = _r(sum(float(l["claimed_usd"]) for l in lines))
    payable = _r(max(total, 0.0))
    return {"claimed_usd": claimed, "payable_usd": payable, "deductions": deductions,
            "line_payable": remaining, "reduction_pct": _r(100 * (claimed - payable) / claimed) if claimed else 0.0}
