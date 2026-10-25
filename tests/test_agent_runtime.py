"""Agent runtime (decision D-12): create_react_agent with manifest-attached, claim-scoped read tools."""
import json

import pytest
from pydantic import BaseModel

from shared import db, registry
from shared.agent_runtime import build_tools, check_attachments, run_agent
from shared.tool_gateway import ToolPermissionError


class Answer(BaseModel):
    text: str


def _ctx(agent_id: str, claim_id: str) -> registry.AgentContext:
    return registry.AgentContext(registry.manifest(agent_id), claim_id, run_no=1)


def test_every_manifest_attaches_only_described_read_operations():
    for m in registry.manifests().values():
        assert check_attachments(m) == [], m["name"]


def test_write_operations_cannot_be_attached(platform, make_claim):
    ctx = _ctx("AGT-TRV-DOC", make_claim("delay_stp"))
    ctx.manifest = {**ctx.manifest, "tools": [{"id": "TOOL-DOCUMENT-STORE", "attach": ["write_extracted"]}]}
    with pytest.raises(ToolPermissionError, match="only read can be attached"):
        build_tools(ctx)


def test_claim_scoped_arguments_are_hidden_from_the_model(platform, make_claim):
    ctx = _ctx("AGT-TRV-COV", make_claim("delay_stp"))
    ctx.bindings = {"policy_no": "POL-X"}
    tools = {t.name: t for t in build_tools(ctx)}
    assert set(tools) == {"policy_admin_get_policy", "policy_admin_claims_history",
                          "policy_wording_search", "policy_wording_get_clause"}
    assert tools["policy_admin_get_policy"].args == {}
    assert tools["policy_admin_claims_history"].args == {}
    assert set(tools["policy_wording_search"].args) == {"query", "k"}
    doc_tools = {t.name: t for t in build_tools(_ctx("AGT-TRV-DOC", ctx.claim_id))}
    assert set(doc_tools["document_store_read_text"].args) == {"doc_id"}


def test_model_cannot_read_another_claims_document(platform, make_claim):
    mine, other = make_claim("delay_stp"), make_claim("bagdelay_stp")
    other_doc = db.scalar("SELECT doc_id FROM documents WHERE claim_id = ?", [other])
    read = {t.name: t for t in build_tools(_ctx("AGT-TRV-DOC", mine))}["document_store_read_text"]
    out = read.invoke({"doc_id": other_doc})
    assert out.startswith("ERROR: PermissionError")


def test_tool_results_are_redacted_and_documents_wrapped(platform, make_claim):
    cid = make_claim("pii_request")
    claim = db.one("SELECT claimant_email, claimant_phone FROM claims WHERE claim_id = ?", [cid])
    ctx = _ctx("AGT-TRV-INTAKE", cid)
    ctx.known_pii = {"claimant_email": claim["claimant_email"], "claimant_phone": claim["claimant_phone"]}
    record = {t.name: t for t in build_tools(ctx)}["claims_system_get_claim"].invoke({})
    assert claim["claimant_email"] not in record and "bank_account_no" not in record
    doc_ctx = _ctx("AGT-TRV-DOC", cid)
    doc = db.scalar("SELECT doc_id FROM documents WHERE claim_id = ?", [cid])
    text = {t.name: t for t in build_tools(doc_ctx)}["document_store_read_text"].invoke({"doc_id": doc})
    assert text.startswith(f'<untrusted_document id="{doc}"')
    assert db.scalar("SELECT COUNT(*) FROM tool_calls WHERE claim_id = ? AND invoked_by = 'model'", [cid]) == 2


def test_mock_run_follows_plan_and_returns_structured_answer(platform, make_claim):
    ctx = _ctx("AGT-TRV-COMMS", make_claim("delay_stp"))
    out = run_agent(ctx, task="t", schema=Answer, user_content="explain clause 3.1",
                    fallback=lambda: Answer(text="ok"),
                    mock_plan=[[("policy_wording_get_clause", {"clause_id": "3.1"}), ("not_attached", {})]])
    assert out == Answer(text="ok") and not ctx.degraded
    assert [c["tool"] for c in ctx.runs[0].tool_calls] == ["policy_wording_get_clause"]


def test_model_failure_degrades_to_fallback(platform, make_claim, monkeypatch):
    import shared.agent_runtime as rt

    def boom(*a, **k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(rt, "chat_model", boom)
    ctx = _ctx("AGT-TRV-VER", make_claim("delay_stp"))
    out = run_agent(ctx, task="t", schema=Answer, user_content="x", fallback=lambda: Answer(text="fallback"))
    assert out.text == "fallback" and ctx.degraded and "provider down" in ctx.runs[0].error


def test_runaway_tool_loop_is_cut_and_degrades(platform, make_claim):
    ctx = _ctx("AGT-TRV-VER", make_claim("delay_stp"))
    loop = [("flight_status_lookup", {"flight_no": "XX1", "flight_date": "2026-01-01"})] * 20
    out = run_agent(ctx, task="t", schema=Answer, user_content="x", fallback=lambda: Answer(text="fb"), mock_plan=loop)
    assert out.text == "fb" and ctx.degraded and "Recursion" in ctx.runs[0].error


def test_agent_meta_reports_framework_tokens_and_tool_calls(platform, make_claim):
    cid = make_claim("delay_stp")
    platform.process(cid)
    meta = json.loads(db.scalar("SELECT output FROM agent_outputs WHERE claim_id = ? AND agent_id = 'AGT-TRV-VER'",
                                [cid]))["_meta"]
    assert meta["framework"] == "langgraph.prebuilt.create_react_agent"
    assert meta["llm_calls"] >= 2 and meta["tokens"] > 0 and meta["cost_usd"] > 0
    assert meta["tool_calls"][0]["tool"] == "flight_status_lookup"
    settle = json.loads(db.scalar("SELECT output FROM agent_outputs WHERE claim_id = ? AND agent_id = 'AGT-TRV-SETTLE'",
                                  [cid]))["_meta"]
    assert settle["framework"] == "deterministic" and settle["llm_calls"] == 0
