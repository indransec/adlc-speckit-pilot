"""test:audit (REQ-N-006) and test:telemetry (REQ-N-007)."""
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from shared.config import release
from shared.telemetry import init_telemetry

EXPORTER = InMemorySpanExporter()


def test_audit_trail_complete_and_redacted(platform, make_claim):
    cid = make_claim("pii_request")
    s = platform.process(cid)
    events = [a["event"] for a in s["audit"]]
    for agent in ("intake", "document", "coverage", "verification", "fraud-screening", "assessment", "settlement", "communication"):
        assert any(e.startswith(f"agent_completed:{agent}.") for e in events), agent
    assert "routed" in events and "approval_token_issued" in events
    assert all(a["agent_version"] for a in s["audit"] if a["event"].startswith("agent_completed"))
    from shared import db
    rows = db.query("SELECT release, payload FROM audit_log WHERE claim_id = ?", [cid])
    assert {r["release"] for r in rows} == {release()}
    assert not any("P98765432" in r["payload"] or "daniel.reyes@example.com" in r["payload"] for r in rows)


def test_every_span_carries_release_and_agent_version(platform, make_claim):
    init_telemetry(extra_processors=[SimpleSpanProcessor(EXPORTER)])
    EXPORTER.clear()
    cid = make_claim("valuables")
    platform.process(cid)
    spans = EXPORTER.get_finished_spans()
    assert len(spans) > 20
    for sp in spans:
        a = sp.attributes
        assert a.get("adlc.system.release") == release(), sp.name
        assert a.get("adlc.agent.id") and a.get("adlc.agent.version"), sp.name
        assert a.get("claim.id", "c_").startswith("c_")              # hashed, never the raw claim id
    names = {sp.name for sp in spans}
    assert {"claim.process", "agent assessment.run", "tool TOOL-POLICY-ADMIN.get_policy"} <= names
    # LLM spans come from OpenInference instrumentation of the create_react_agent graphs
    llm = [sp for sp in spans if sp.attributes.get("openinference.span.kind") == "LLM"]
    assert llm and all(sp.attributes.get("llm.model_name") and sp.attributes.get("llm.token_count.prompt") for sp in llm)
    assert all(sp.attributes.get("adlc.task") for sp in llm)
    assert any(sp.attributes.get("tool.invoked_by") == "model" for sp in spans)

    # the telemetry sink recorded every LLM span of this claim, priced from the spans' token counts
    from shared import db
    from shared.telemetry import price_usd
    rows = db.query("SELECT * FROM llm_calls WHERE claim_id = ?", [cid])
    assert len(rows) == len(llm)
    for r in rows:
        assert r["cost_usd"] == price_usd(r["model"], r["prompt_tokens"], r["completion_tokens"])
        assert r["trace_id"] and r["span_id"] and r["latency_ms"] is not None
    assert db.scalar("SELECT COUNT(*) FROM tool_calls WHERE claim_id = ? AND invoked_by = 'model'", [cid]) > 0
