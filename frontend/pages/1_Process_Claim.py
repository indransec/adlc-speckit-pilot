import json

import pandas as pd
import streamlit as st

from common import get, money, page, post, status_label, try_action

page("Process a claim", "▶️")

STEPS = [("Intake", "agent_completed:intake."), ("Documents", "agent_completed:document."),
         ("Coverage", "agent_completed:coverage."), ("Verification", "agent_completed:verification."),
         ("Fraud screening", "agent_completed:fraud-screening."), ("Assessment", "agent_completed:assessment."),
         ("Routing (code)", "routed"), ("Human review", "human_decision:"), ("Approval token", "approval_token_issued"),
         ("Settlement", "agent_completed:settlement."), ("Decision letter", "agent_completed:communication.decision_letter")]
REPROCESSABLE = {"new", "pending_documents", "error", "shadow_complete", "manual_queue", "registered"}
QUEUE_ROLES = {"L1": {"L1", "L2"}, "INVESTIGATION": {"L1", "L2"}, "L2": {"L2"}, "SIU": {"SIU"}}
DECISION_HELP = {"accept": "Accept the agent recommendation", "set_amount": "Approve a different amount",
                 "remove_type_b": "Remove all type B (judgement) deductions", "reject": "Reject the claim",
                 "accept_l1": "Confirm the L1 view", "accept_recommendation": "Confirm the agent recommendation",
                 "clear": "No fraud: return to normal routing", "refer": "Refer to SIU investigation (closes here)"}

# ------------------------------------------------------------------ selection
with st.sidebar:
    st.subheader("Select a claim")
    status_filter = st.selectbox("Status", ["all", "new", "awaiting_l1", "awaiting_l2", "awaiting_investigation",
                                            "awaiting_siu", "pending_documents", "closed_paid", "closed_rejected",
                                            "shadow_complete", "manual_queue", "siu_referred"])
    claims = get("/claims", params={} if status_filter == "all" else {"status": status_filter})
    scen = {s["key"]: s["title"] for s in get("/scenarios")}
    if not claims:
        st.info("No claims with this status.")
    labels = {c["claim_id"]: f"{c['claim_id']} · {status_label(c['status'])} · {scen.get(c['scenario'], c['scenario'])}"
              for c in claims}
    ids = list(labels)
    default = st.session_state.get("claim_id")
    claim_id = st.selectbox("Claim", ids, index=ids.index(default) if default in ids else 0,
                            format_func=lambda i: labels[i]) if ids else None
    with st.expander("➕ New synthetic claim"):
        sc = st.selectbox("Scenario", list(scen), format_func=lambda k: scen[k])
        if st.button("Generate claim"):
            r = try_action(post, "/claims/generate", {"scenario": sc})
            if r:
                st.session_state["claim_id"] = r["claim_ids"][0]
                st.rerun()

if not claim_id:
    st.stop()
st.session_state["claim_id"] = claim_id


# ------------------------------------------------------------------ live view
@st.fragment(run_every=1.5)
def live():
    s = get(f"/claims/{claim_id}")
    c = s["claim"]
    events = [a["event"] for a in s["audit"]]
    cols = st.columns(len(STEPS))
    for col, (label, prefix) in zip(cols, STEPS):
        done = any(e.startswith(prefix) for e in events)
        col.markdown(f"{'✅' if done else '⬜'}<br><small>{label}</small>", unsafe_allow_html=True)
    if c["status"] == "processing":
        st.caption("⏳ Agents running… (refreshing)")
    elif st.session_state.get("_was_processing"):
        st.session_state["_was_processing"] = False
        st.rerun()
    if c["status"] == "processing":
        st.session_state["_was_processing"] = True


s = get(f"/claims/{claim_id}")
c, pkg, outs = s["claim"], s["package"] or {}, s["agent_outputs"]
gt = c.get("ground_truth") or {}

st.subheader(f"{c['claim_id']} · {c['claimant_name']}")
m = st.columns(6)
m[0].metric("Status", status_label(c["status"]))
m[1].metric("Type", c["claim_type"] or c["claim_type_declared"])
m[2].metric("Claimed", money(c["claimed_usd"]))
m[3].metric("Recommended", money(c["recommended_usd"]))
m[4].metric("Route", (c["route"] or "–").replace("_", " "))
m[5].metric("Confidence", "–" if c["confidence"] is None else f"{c['confidence']:.2f}")

b = st.columns([1, 1, 1, 3])
if c["status"] in REPROCESSABLE and b[0].button("▶ Run agents", type="primary"):
    if try_action(post, f"/claims/{claim_id}/process", success="Submitted to the orchestrator"):
        st.session_state["_was_processing"] = True
        st.rerun()
if c["status"] == "pending_documents" and b[1].button("📤 Customer uploads missing docs"):
    try_action(post, f"/claims/{claim_id}/documents/upload-missing", success="Documents received; re-processing")
    st.rerun()
if b[2].button("🔄 Refresh"):
    st.rerun()
with b[3].expander("Simulation ground truth (hidden from agents)"):
    st.write(f"**{gt.get('title', '')}** · expected route `{gt.get('route')}` · expected amount {money(gt.get('amount'))} · "
             f"adjuster final: {gt.get('final_outcome')} {money(gt.get('final_amount'))}")

live()

# ------------------------------------------------------------------ human decision
if s.get("pending"):
    p = s["pending"]
    st.warning(f"🧑‍💼 Waiting for a human decision in the **{p['queue']}** queue")
    users = [u for u in get("/users") if u["role"] in QUEUE_ROLES[p["queue"]]]
    with st.form("decision"):
        cols = st.columns([2, 2, 1, 3])
        user = cols[0].selectbox("Acting as", [u["user_id"] for u in users],
                                 format_func=lambda u: next(x["name"] for x in users if x["user_id"] == u))
        decision = cols[1].selectbox("Decision", p["options"], format_func=lambda o: DECISION_HELP.get(o, o))
        amount = cols[2].number_input("Amount USD", min_value=0.0, value=float(c["recommended_usd"] or 0), step=10.0)
        notes = cols[3].text_input("Notes", placeholder="reason for the decision")
        if p.get("l1_view"):
            st.caption(f"L1 view: {p['l1_view']['user_id']} → {p['l1_view']['outcome']} {money(p['l1_view']['amount_usd'])}")
        if st.form_submit_button("Submit decision", type="primary"):
            r = try_action(post, f"/claims/{claim_id}/decision",
                           {"user_id": user, "decision": decision, "notes": notes,
                            "amount_usd": amount if decision == "set_amount" else None}, success="Decision recorded")
            if r:
                st.rerun()

# ------------------------------------------------------------------ details
tabs = st.tabs(["📄 Documents", "🧾 Extraction", "📘 Coverage", "✈️ Verification & fraud", "🧮 Assessment",
                "🚦 Routing & confidence", "✉️ Letters & payment", "🧾 Audit trail", "🤖 Agents, models & tools"])
with tabs[0]:
    for d in s["documents"]:
        with st.expander(f"{d['doc_id']} · {d['doc_type']} · certainty {d['extraction_certainty']}"):
            st.code(get(f"/claims/{claim_id}/documents/{d['doc_id']}/text")["text"], language=None)
    st.dataframe(pd.DataFrame(c["lines"]).drop(columns=["receipt", "receipt_extra", "scan"], errors="ignore"),
                 hide_index=True, width="stretch")
with tabs[1]:
    doc = outs.get("AGT-TRV-DOC")
    if doc:
        st.write("**Intake**", outs.get("AGT-TRV-INTAKE", {}).get("rationale"))
        cols = st.columns(4)
        cols[0].metric("Complete", "yes" if doc["complete"] else "no")
        cols[1].metric("Consistent", "yes" if doc["consistent"] else "no")
        cols[2].metric("Extraction certainty", doc["extraction_certainty"])
        cols[3].metric("PII redactions", doc["pii_redactions"])
        for mm in doc["mismatches"]:
            st.error(f"Mismatch: {mm}")
        for inj in doc["injection_docs"]:
            st.warning(f"⚠️ Instruction-like text in {inj['doc_id']} treated as data: {inj['matches']}")
        if doc["missing_documents"]:
            st.warning("Missing: " + ", ".join(doc["missing_labels"]))
        st.json(doc["facts"], expanded=False)
        st.dataframe(pd.DataFrame(doc["lines"]), hide_index=True, width="stretch")
    else:
        st.caption("Not processed yet.")
with tabs[2]:
    cov = outs.get("AGT-TRV-COV")
    if cov:
        cols = st.columns(4)
        cols[0].metric("Policy active", "yes" if cov["policy_active"] else "no")
        cols[1].metric("Insured", "yes" if cov["claimant_insured"] else "no")
        cols[2].metric("Plan", cov["plan"] or "–")
        cols[3].metric("Duplicate of", cov["duplicate_of"] or "–")
        st.write(cov["rationale"])
        st.markdown("**FAISS-retrieved clauses**")
        st.dataframe(pd.DataFrame(cov["retrieved"]), hide_index=True, width="stretch")
with tabs[3]:
    ver, fr = outs.get("AGT-TRV-VER"), outs.get("AGT-TRV-FRAUD")
    if ver:
        st.write(ver.get("summary"))
        for d in ver.get("discrepancies", []):
            st.warning(d)
    if fr:
        st.metric("Fraud risk (SIU model)", f"{fr['risk_level']} ({fr['score']})")
        st.write(fr["reasons"] or "No risk factors.")
        if fr["ai_flags"]:
            st.write("AI anomaly flags:", fr["ai_flags"])
with tabs[4]:
    if pkg:
        st.write(f"**Recommendation:** {pkg['recommended_outcome']} · {money(pkg['recommended_usd'])} of "
                 f"{money(pkg['claimed_usd'])} ({pkg['reduction_pct']}% reduction)")
        if pkg["deductions"]:
            st.dataframe(pd.DataFrame(pkg["deductions"])[["line_id", "type", "clause", "clause_title", "amount_usd", "reason",
                                                          "evidence_refs"]], hide_index=True, width="stretch")
        for r in pkg["rejection_reasons"]:
            st.error(f"Clause {r['clause']} ({r['clause_title']}): {r['reason']}")
        st.write(pkg["rationale"])
        st.caption("Calculation order: " + pkg["calculation_order"])
        with st.expander("Clauses cited"):
            for k, v in pkg["clauses_cited"].items():
                st.markdown(f"**{k}** {v}")
with tabs[5]:
    if pkg:
        r, cf = pkg["routing"], pkg["confidence"]
        st.write(f"**{r['route']}** · approvals required: {r['approvals_required']} · overlay: {pkg['overlay']} "
                 f"(mode {pkg['mode']}) · linked aggregate {money(r.get('aggregate_usd'))}")
        for reason in r["reasons"]:
            st.write("•", reason)
        st.markdown(f"**Confidence {cf['score']}** = min(weighted mean {cf['weighted_mean']}, weakest signal + 0.35 = "
                    f"{cf['cap']})" + (" · degraded (model fallback)" if cf["degraded"] else ""))
        st.dataframe(pd.DataFrame([{"signal": k, "value": v, "weight": cf["weights"][k]} for k, v in cf["components"].items()]),
                     hide_index=True)
        if pkg.get("case_note"):
            st.info("**Adjuster handover note:** " + pkg["case_note"]["summary"])
            for f in pkg["case_note"].get("focus_points", []):
                st.write("→", f)
with tabs[6]:
    for p in s["payments"]:
        st.success(f"Payment {p['payment_id']}: {money(p['amount_usd'])} to {p['payee_name']} ({p['account_masked']}) · "
                   f"approvers {p['approvers']}")
    for a in s["approvals"]:
        st.write(f"**{a['level']}** {a['user_id']}: {a['decision']} → {a['outcome']} {money(a['amount_usd'])}"
                 f"{' (override)' if a['override'] else ''} · {a['notes']}")
    for n in s["notifications"]:
        with st.expander(f"✉️ {n['subject']} · {'sent' if n['sent_at'] else 'BLOCKED by output policy'}"):
            st.text(n["body"])
with tabs[7]:
    st.dataframe(pd.DataFrame(s["audit"]), hide_index=True, width="stretch")
    if s["guardrails"]:
        st.markdown("**Guardrail events**")
        st.dataframe(pd.DataFrame(s["guardrails"]), hide_index=True, width="stretch")
with tabs[8]:
    st.caption(f"{s['llm']['calls']} model calls · {s['llm']['prompt_tokens']} in / {s['llm']['completion_tokens']} out "
               f"tokens · cost USD {s['llm']['cost_usd']:.5f}. Tokens and latency come from the OpenInference spans; "
               "cost = tokens × deploy/models.yaml price, as in Phoenix. Mock-mode tokens are estimates.")
    agents = [{"agent": o["_meta"]["agent_id"] + ("" if o["_meta"]["entry"] == "run" else f".{o['_meta']['entry']}"),
               "framework": o["_meta"].get("framework", "–"), "model": ", ".join(o["_meta"]["models"]) or "–",
               "model calls": o["_meta"].get("llm_calls", 0), "tokens": o["_meta"].get("tokens", 0),
               "cost_usd": o["_meta"]["cost_usd"], "degraded": o["_meta"]["degraded"],
               "tools chosen by the model": " → ".join(t["tool"] for t in o["_meta"].get("tool_calls", [])) or "–"}
              for o in outs.values() if "_meta" in o]
    st.markdown("**Agents** (each LLM task is a LangGraph `create_react_agent`)")
    st.dataframe(pd.DataFrame(agents), hide_index=True, width="stretch")
    st.markdown("**Tool calls** (through the permission gateway; `model` = chosen by the agent's LLM)")
    st.dataframe(pd.DataFrame(get(f"/claims/{claim_id}/tool-calls")), hide_index=True, width="stretch")
    st.markdown("**Model calls** (one row per LLM span; search Phoenix by trace_id)")
    st.dataframe(pd.DataFrame(get(f"/claims/{claim_id}/llm-calls")), hide_index=True, width="stretch")
