"""Verification agent (AGT-TRV-VER): verify delay facts against the flight-status source (REQ-F-014).

Facts and thresholds are checked in code; the model only writes the adjuster-facing summary.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from shared.tool_gateway import ToolPermissionError

DELAY_THRESHOLD_H = 6      # clause 3.1 / 3.3
BAG_DELAY_THRESHOLD_H = 12  # clause 4.1


class VerificationSummary(BaseModel):
    summary: str = Field(description="2-3 sentences for the adjuster, facts only")
    discrepancies: list[str] = Field(default_factory=list)


def run(ctx, *, claim_type: str, facts: dict) -> dict:
    unavailable = None
    try:
        f = ctx.tool("TOOL-FLIGHT-STATUS", "lookup", flight_no=facts.get("flight_no"), flight_date=facts.get("flight_date"))
    except ToolPermissionError:
        raise  # a control, never swallowed
    except Exception as e:  # source down: verify nothing, degrade, and let routing send the claim to a person
        f, unavailable = None, f"flight-status source unavailable ({type(e).__name__})"
        ctx.degrade(unavailable)
    out = {"flight_found": bool(f), "delay_hours_verified": None, "cause": None, "meets_threshold": None,
           "strike_announced_before_booking": False, "discrepancies": []}
    if f:
        hours = round(f["delay_minutes"] / 60, 2)
        out.update(delay_hours_verified=hours, cause=f["cause"], scheduled_dep=f["scheduled_dep"], actual_dep=f["actual_dep"])
        if claim_type == "delay":
            out["meets_threshold"] = hours >= DELAY_THRESHOLD_H
            claimed = facts.get("delay_hours_claimed")
            if claimed is not None and abs(claimed - hours) > 1:
                out["discrepancies"].append(f"claimed delay {claimed:g}h but flight data shows {hours:g}h")
            if f["cause"] == "industrial_action" and f["cause_announced_at"] and facts.get("booking_date") \
                    and f["cause_announced_at"][:10] < facts["booking_date"]:
                out["strike_announced_before_booking"] = True
                out["discrepancies"].append(f"strike announced {f['cause_announced_at'][:10]}, before booking on {facts['booking_date']}")
    else:
        out["discrepancies"].append(unavailable or "flight not found in the flight-status source")
    if claim_type == "baggage_delay":
        bh = facts.get("bag_delay_hours")
        out["bag_delay_hours"] = bh
        out["meets_threshold"] = bh is not None and bh > BAG_DELAY_THRESHOLD_H
        if bh is None:
            out["discrepancies"].append("bag delay duration not evidenced by a PIR")
    out["verification_success"] = 1.0 if f and (claim_type != "baggage_delay" or out.get("bag_delay_hours") is not None) else 0.5

    def fallback():
        if not f:
            return VerificationSummary(summary="The flight could not be found in the flight-status source.",
                                       discrepancies=out["discrepancies"])
        s = f"Flight {facts.get('flight_no')} on {facts.get('flight_date')} departed {out['delay_hours_verified']:g}h late"
        s += f" (cause: {out['cause']})." if out["cause"] else "."
        if claim_type == "baggage_delay":
            s += f" PIR shows the bag was delivered after {out.get('bag_delay_hours')} hours."
        return VerificationSummary(summary=s, discrepancies=out["discrepancies"])

    prompt = (f"Claim type: {claim_type}\nClaim facts: flight {facts.get('flight_no')} on {facts.get('flight_date')}, "
              f"claimed delay {facts.get('delay_hours_claimed')}h, booking date {facts.get('booking_date')}, "
              f"bag delay {facts.get('bag_delay_hours')}h\nThreshold checks done in code: {out}\n"
              "Look up the flight with your flight-status tool, then summarise the verified facts for the "
              "adjuster and list discrepancies.")
    summary = ctx.agent("summarise_verification", VerificationSummary, prompt, fallback=fallback, mock_plan=[
        ("flight_status_lookup", {"flight_no": facts.get("flight_no") or "", "flight_date": facts.get("flight_date") or ""})])
    out["summary"] = summary.summary
    return out
