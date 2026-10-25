"""Observability (observability/dashboards/claims-agent.md): KPIs, SLOs, alerts, controls, LLM cost, RAI."""
import pandas as pd
import streamlit as st

from common import bar, get, ok_badge, page

page("Observability", "📈")
d = get("/metrics/dashboard")
sysinfo = get("/system")
st.caption(f"Traces and LLM spans (OpenTelemetry / OpenInference) in [Arize Phoenix]({sysinfo['phoenix_url']}) · "
           "every span carries adlc.system.release, adlc.agent.id/version and a hashed claim id")

alerts = d["alerts"]
firing = [a for a in alerts if a["firing"]]
if any(a["severity"] == "sev1" for a in firing):
    st.error("⛔ Sev1 alert firing: " + ", ".join(a["name"] for a in firing if a["severity"] == "sev1"))
elif firing:
    st.warning("⚠️ Alerts firing: " + ", ".join(f"{a['name']} ({a['severity']})" for a in firing))
else:
    st.success("✅ No alerts firing")

t = st.tabs(["Value KPIs", "SLOs & alerts", "Controls & guardrails", "LLM usage & cost", "Quality & calibration",
             "Fairness", "Live feeds"])
with t[0]:
    k = pd.DataFrame(d["kpis"])
    k["status"] = k["met"].map(lambda x: ok_badge(x))
    k[["baseline", "value"]] = k[["baseline", "value"]].astype(str)
    st.dataframe(k[["id", "name", "baseline", "value", "target", "status"]], hide_index=True, width="stretch")
    st.caption("Handling time and adjuster minutes use simulated human effort per decision (shared/metrics.py); "
               "baselines are synthetic placeholders from docs/kpis.yaml.")
with t[1]:
    s = pd.DataFrame(d["slos"])
    s["status"] = s["met"].map(lambda x: ok_badge(x))
    s["value"] = s["value"].astype(str)
    st.dataframe(s[["id", "target", "value", "status"]], hide_index=True, width="stretch")
    a = pd.DataFrame(alerts)
    a["state"] = a["firing"].map(lambda f: "🔔 firing" if f else "✅ ok")
    a["value"] = a["value"].map(lambda v: "–" if v is None else f"{v:.3g}" if isinstance(v, float) else str(v))
    st.dataframe(a[["name", "severity", "condition", "value", "state"]], hide_index=True, width="stretch")
    st.markdown("**Queues**")
    st.dataframe(pd.DataFrame(d["queues"]), hide_index=True, width="stretch")
with t[2]:
    c = d["controls"]
    m = st.columns(3)
    m[0].metric("Payments", c["payments_total"])
    m[1].metric("Payments without valid token", c["payments_without_valid_token"], help="Sev1 if > 0")
    m[2].metric("Type B labelled as A", c["type_b_mislabelled_as_a"], help=c["type_b_mislabel_note"])
    st.markdown("**Control events** (blocked attempts are the controls working)")
    st.dataframe(pd.DataFrame(c["events"]), hide_index=True, width="stretch")
    st.markdown("**Guardrail events**")
    g = pd.DataFrame(d["guardrails"])
    if not g.empty:
        g["guardrail / action"] = g["guardrail"] + " · " + g["action"]
        bar(g, "guardrail / action", "n")
with t[3]:
    llm = d["llm"]
    m = st.columns(3)
    m[0].metric("Total model cost", f"${llm['total_cost_usd']:.4f}")
    m[1].metric("Cost per claim", "–" if llm["cost_per_claim_usd"] is None else f"${llm['cost_per_claim_usd']:.5f}")
    m[2].metric("Claims with model calls", llm["claims"])
    by = pd.DataFrame(llm["by_agent"])
    if not by.empty:
        st.caption("Mock-mode rows show estimated tokens/cost at catalog prices (deploy/models.yaml).")
        cost = by.groupby("agent_id")["cost_usd"].sum().reset_index().sort_values("cost_usd", ascending=False)
        bar(cost, "agent_id", "cost_usd", fmt="$.5f")
        st.dataframe(by, hide_index=True, width="stretch")
with t[4]:
    sh = d["shadow"]
    st.markdown(f"**Shadow mode agreement** (agent vs historical adjuster): route {sh['route_agreement']}, "
                f"amount {sh['amount_agreement']} on {sh['claims']} claims")
    cal = pd.DataFrame(d["calibration"])
    st.markdown("**Confidence calibration** - share of claims where the final decision matched the recommendation")
    if not cal.empty:
        bar(cal, "confidence_bin", "agreement", fmt=".2f", horizontal=False,
            tooltip=["confidence_bin", "claims", "agreement"])
        st.dataframe(cal, hide_index=True)
with t[5]:
    st.caption("Observational parity on decided claims (gaps explained by policy terms are documented, not fixed). "
               "The counterfactual test in the RAI report swaps claimant identity on identical claims.")
    for seg, v in d["fairness"].items():
        st.markdown(f"**{seg.replace('_', ' ')}** · approval-rate gap {v['approval_gap_pts']} pts (groups with ≥ 3 claims)")
        st.dataframe(pd.DataFrame(v["groups"]), hide_index=True, width="stretch")
with t[6]:
    st.markdown("**Recent model calls**")
    st.dataframe(pd.DataFrame(get("/metrics/llm-calls", params={"limit": 50})), hide_index=True, width="stretch")
    st.markdown("**Audit trail** (redacted)")
    st.dataframe(pd.DataFrame(get("/audit", params={"limit": 100})), hide_index=True, width="stretch")
