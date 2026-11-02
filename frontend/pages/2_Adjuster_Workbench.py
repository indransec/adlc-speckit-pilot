"""Simulated adjuster workbench (ADR-004 stand-in): L1, investigation, L2, SIU and manual queues."""
from datetime import datetime

import pandas as pd
import streamlit as st

from common import call, get, money, page, post, status_label, try_action

page("Adjuster workbench", "🧑‍💼")
q = get("/queues")
now = datetime.fromisoformat(get("/clock")["now"])  # simulation clock during a run
for r in q:
    r["age_min"] = round((now - datetime.fromisoformat(r["queue_since"])).total_seconds() / 60, 1) if r["queue_since"] else None

cols = st.columns(5)
for col, name in zip(cols, ["L1", "INVESTIGATION", "L2", "SIU", "MANUAL"]):
    col.metric(f"{name} queue", sum(1 for r in q if r["queue"] == name))

a, b, c = st.columns([2, 2, 3])
if a.button("🤖 Let simulated adjusters clear the queues", help="Bot decisions follow the synthetic ground truth"):
    r = try_action(post, "/simulate/adjusters")
    if r:
        st.success(f"{r['decisions']} decisions made")
        st.rerun()
days = b.number_input("Advance clock for document chasing (days)", 1, 40, 3)
if c.button("⏩ Run document chasing job"):
    r = try_action(call, "POST", f"/jobs/document-chasing?days={days}")
    if r is not None:
        st.success(", ".join(f"{x['claim_id']} {x['action']} (day {x['day']})" for x in r) if r else "Nothing due yet.")

queue = st.radio("Queue", ["L1", "INVESTIGATION", "L2", "SIU", "MANUAL"], horizontal=True)
rows = [r for r in q if r["queue"] == queue]
if not rows:
    st.info("Queue is empty.")
    st.stop()
df = pd.DataFrame(rows)[["claim_id", "claimant_name", "claim_type", "route", "claimed_usd", "recommended_usd",
                         "confidence", "fraud_risk", "age_min"]]
st.dataframe(df, hide_index=True, width="stretch")

cid = st.selectbox("Open claim", [r["claim_id"] for r in rows])
s = get(f"/claims/{cid}")
pkg, claim = s["package"] or {}, s["claim"]
st.subheader(f"{cid} · {claim['claimant_name']} · {status_label(claim['status'])}")
if pkg.get("case_note"):
    st.info(pkg["case_note"]["summary"])
    for f in pkg["case_note"].get("focus_points", []):
        st.write("→", f)
left, right = st.columns([3, 2])
with left:
    st.write(f"Recommendation **{pkg.get('recommended_outcome')}** · {money(pkg.get('recommended_usd'))} of "
             f"{money(pkg.get('claimed_usd'))} · confidence {pkg.get('confidence', {}).get('score')}")
    if pkg.get("deductions"):
        st.dataframe(pd.DataFrame(pkg["deductions"])[["type", "clause", "clause_title", "amount_usd", "reason", "evidence_refs"]],
                     hide_index=True, width="stretch")
    for r in pkg.get("rejection_reasons", []):
        st.error(f"Clause {r['clause']} ({r['clause_title']}): {r['reason']}")
    for m in (pkg.get("documents") or {}).get("mismatches", []):
        st.warning(m)
with right:
    p = s.get("pending")
    if queue == "MANUAL":
        st.warning("Kill switch / manual handling. Re-run the agents from the Process page once the kill switch is off.")
    elif p:
        roles = {"L1": {"L1", "L2"}, "INVESTIGATION": {"L1", "L2"}, "L2": {"L2"}, "SIU": {"SIU"}}[p["queue"]]
        users = [u for u in get("/users") if u["role"] in roles]
        with st.form("wb-decision"):
            user = st.selectbox("Acting as", [u["user_id"] for u in users])
            decision = st.selectbox("Decision", p["options"])
            amount = st.number_input("Amount USD (set_amount)", 0.0, value=float(claim["recommended_usd"] or 0))
            notes = st.text_input("Notes")
            if st.form_submit_button("Submit", type="primary"):
                if try_action(post, f"/claims/{cid}/decision", {"user_id": user, "decision": decision, "notes": notes,
                                                               "amount_usd": amount if decision == "set_amount" else None},
                              success="Decision recorded"):
                    st.rerun()
