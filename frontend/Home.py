"""Travel Claims Assistant - Streamlit front end (simulated adjuster workbench + ADLC console)."""
import pandas as pd
import streamlit as st

from common import get, page, status_label

page("Travel Claims Assistant", "🧳")
sysinfo = get("/system")
rt = sysinfo["runtime"]
ov = get("/metrics/dashboard")["overview"]

st.caption(f"{sysinfo['id']} · release **{sysinfo['release']}** · risk tier **{sysinfo['risk_tier'].upper()}** · "
           f"scope: {', '.join(sysinfo['scope'])} · ADLC pilot with synthetic data")

c = st.columns(5)
c[0].metric("Rollout mode", rt["mode"])
c[1].metric("Straight-through", "on" if rt["straight_through_enabled"] else "off")
c[2].metric("Payments", "on" if rt["payments_enabled"] else "off")
c[3].metric("Kill switch", "ACTIVE" if rt["kill_switch"] else "off")
c[4].metric("LLM gateway", f"{rt['llm_effective']}", help="live = OpenRouter; mock = deterministic offline agents")
if rt["kill_switch"]:
    st.error("⛔ Kill switch active: new claims go to the manual queue and payment instructions are stopped.")
if rt["llm_effective"] == "mock":
    st.info("Running in **mock LLM mode** (no OPENROUTER_API_KEY or llm_mode=mock). Agents use their deterministic "
            "fallbacks; set the key in `.env` and restart the API for live OpenRouter calls.")

c = st.columns(5)
c[0].metric("Claims", ov["claims"])
c[1].metric("Decided", ov["decided"])
c[2].metric("In human queues", ov["in_queue"])
c[3].metric("Claimed", f"${ov['claimed_usd']:,.0f}")
c[4].metric("Paid", f"${ov['paid_usd']:,.0f}")

left, right = st.columns([3, 2])
with left:
    st.subheader("Claim flow (LangGraph orchestrator)")
    st.graphviz_chart("""
digraph G { rankdir=LR; node [shape=box style="rounded,filled" fillcolor="#eef4fc" color="#2a78d6" fontname=Helvetica fontsize=10];
  edge [color="#8a8984" fontsize=9 fontname=Helvetica];
  intake [label="Intake\\nclassify + ack"]; docs [label="Document\\nextract + cross-check"];
  cov [label="Coverage\\nFAISS clauses"]; ver [label="Verification\\nflight status"]; fraud [label="Fraud screening\\nflag only"];
  assess [label="Assessment\\nrules engine + A/B"]; route [label="Routing\\n(code, ADR-001)" fillcolor="#fdf0e9" color="#eb6834"];
  l1 [label="L1 / investigation" shape=oval fillcolor="#f4f4f2" color="#52514e"]; l2 [label="L2" shape=oval fillcolor="#f4f4f2" color="#52514e"];
  siu [label="SIU" shape=oval fillcolor="#f4f4f2" color="#52514e"]; token [label="Approval token\\n(ADR-003)" fillcolor="#fdf0e9" color="#eb6834"];
  settle [label="Settlement\\nno model"]; comms [label="Communication\\napproved templates"];
  intake -> docs -> cov; docs -> ver; docs -> fraud; cov -> assess; ver -> assess; fraud -> assess; assess -> route;
  route -> token [label="row 1"]; route -> l1 [label="rows 2-6"]; route -> siu [label="row 7"]; siu -> l1 [label="clear"];
  l1 -> l2 [label="2-level"]; l1 -> token; l2 -> token; token -> settle -> comms; l1 -> comms [label="reject" style=dashed];
}""")
with right:
    st.subheader("Routing matrix (first match wins)")
    st.dataframe(pd.DataFrame([
        ["7", "Fraud risk medium/high", "SIU"], ["6", "Confidence < 0.7 or type B without evidence", "L1 investigates"],
        ["5", "Full rejection", "L1 → L2"], ["4", "Type B, or reduction > 25%", "L1 → L2"],
        ["2", "Type A deductions only", "L1"], ["3", "Aggregate > USD 1,000, no deduction", "L1"],
        ["1", "Otherwise (≤ USD 1,000, low risk, conf ≥ 0.7)", "Agent (STP)"]], columns=["Row", "Condition", "Decision"]),
        hide_index=True, width="stretch")
    st.subheader("Claims by status")
    st.dataframe(pd.DataFrame([{"Status": status_label(k), "Claims": v} for k, v in sorted(ov["by_status"].items())]),
                 hide_index=True, width="stretch")

st.subheader("Agents (pinned in system.yaml)")
st.dataframe(pd.DataFrame(sysinfo["agents"])[["id", "name", "version", "autonomy", "model", "tools", "requirements"]],
             hide_index=True, width="stretch")
st.caption(f"Traces: [Arize Phoenix]({sysinfo['phoenix_url']}) · API docs: /docs on the backend · "
           "Pages: Process Claim · Adjuster Workbench · Claims Status · Observability · ADLC Lifecycle")
