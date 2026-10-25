"""test:fraud-integration (REQ-F-016) and flag-only behaviour (REQ-B-007)."""
from shared.tool_gateway import call_tool


def test_fraud_levels(platform, make_claim):
    low = call_tool("fraud-screening", "TOOL-FRAUD-SCORE", "score", claim_id=make_claim("delay_stp"), signals={})
    high = call_tool("fraud-screening", "TOOL-FRAUD-SCORE", "score", claim_id=make_claim("fraud"), signals={})
    assert low["level"] == "low" and high["level"] == "high" and high["reasons"]


def test_fraud_flag_routes_to_siu_never_rejects(platform, make_claim):
    s = platform.process(make_claim("fraud"))
    assert s["claim"]["route"] == "row7_siu" and s["claim"]["status"] == "awaiting_siu"
    assert "recommendation" not in s["agent_outputs"]["AGT-TRV-FRAUD"]
