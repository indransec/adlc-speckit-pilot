"""Portfolio view: every claim, its status, route and money."""
import pandas as pd
import streamlit as st

from common import bar, get, grouped_bar, page, post, status_label, try_action

page("Claims status", "📊")
claims = pd.DataFrame(get("/claims"))
dash = get("/metrics/dashboard")
ov = dash["overview"]

c = st.columns(6)
c[0].metric("Claims", ov["claims"])
c[1].metric("Processed", ov["processed"])
c[2].metric("Decided", ov["decided"])
c[3].metric("Waiting on humans", ov["in_queue"])
c[4].metric("Claimed", f"${ov['claimed_usd']:,.0f}")
c[5].metric("Paid", f"${ov['paid_usd']:,.0f}")

a, b = st.columns([1, 4])
if a.button("▶ Process all new claims", help="Submits every 'new' claim to the orchestrator in the background"):
    r = try_action(post, "/claims/process-all")
    if r:
        st.success(f"Submitted {len(r['submitted'])} claims - refresh in a few seconds")
n = b.number_input("Generate production-like traffic (claims)", 1, 100, 10, label_visibility="collapsed")
if b.button(f"➕ Generate {n} synthetic claims"):
    r = try_action(post, "/claims/generate", {"count": int(n)})
    if r:
        st.success(f"Created {len(r['claim_ids'])} claims")
        st.rerun()

if claims.empty:
    st.stop()
left, right = st.columns(2)
with left:
    st.subheader("Claims by status")
    s = claims.groupby("status").size().reset_index(name="claims")
    s["status"] = s["status"].map(status_label)
    bar(s.sort_values("claims", ascending=False), "status", "claims")
with right:
    st.subheader("Routing mix (% of routed claims)")
    bar(pd.DataFrame(dash["routing_mix"]), "label", "pct", fmt=".1f", tooltip=["label", "claims", "pct"])

st.subheader("Claimed vs approved by claim type (decided claims)")
decided = claims[claims["status"].isin(["closed_paid", "closed_rejected", "payment_on_hold", "approved"])].copy()
if not decided.empty:
    decided["claim_type"] = decided["claim_type"].fillna(decided["claim_type_declared"])
    g = decided.groupby("claim_type")[["claimed_usd", "approved_usd"]].sum().reset_index()
    long = g.melt("claim_type", var_name="measure", value_name="USD").replace(
        {"claimed_usd": "Claimed", "approved_usd": "Approved"})
    grouped_bar(long, "claim_type", "measure", "USD", ["Claimed", "Approved"])
    st.caption("Table view: " + "; ".join(f"{r.claim_type}: claimed ${r.claimed_usd:,.0f}, approved ${r.approved_usd:,.0f}"
                                         for r in g.itertuples()))

st.subheader("All claims")
f1, f2 = st.columns(2)
sel_status = f1.multiselect("Status", sorted(claims["status"].unique()), format_func=status_label)
sel_route = f2.multiselect("Route", sorted(claims["route"].dropna().unique()))
view = claims
if sel_status:
    view = view[view["status"].isin(sel_status)]
if sel_route:
    view = view[view["route"].isin(sel_route)]
view = view.assign(status=view["status"].map(status_label))
st.dataframe(view[["claim_id", "scenario", "claimant_name", "claim_type_declared", "status", "route", "claimed_usd",
                   "recommended_usd", "approved_usd", "confidence", "fraud_risk", "mode", "updated_at"]],
             hide_index=True, width="stretch",
             column_config={"confidence": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f")})
