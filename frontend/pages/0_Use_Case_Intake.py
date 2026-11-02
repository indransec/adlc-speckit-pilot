"""G0 / G1 intake: capture the use case, compute its risk tier, confirm limits and KPIs, and sign the gates."""
from datetime import date

import pandas as pd
import streamlit as st

from common import GATE_ICON, clock_badge, gate_signoff, get, ok_badge, page, persona_picker, post, try_action

page("Use case intake (G0 / G1)", "📝")
persona = persona_picker()
clk = clock_badge()
people = {p["id"]: f"{p['name']} · {p['title']}" for p in get("/personas")}
pids = list(people)
summary = get("/usecases/current")
rec = summary["record"] if summary["exists"] else get("/usecases/template")
uc = rec["id"]


def rows(df: pd.DataFrame) -> list[dict]:
    out = []
    for r in df.to_dict("records"):
        r = {k: (v.isoformat() if isinstance(v, date) else ("" if v is None or v != v else v)) for k, v in r.items()}
        if any(str(v).strip() for v in r.values()):
            out.append(r)
    return out


def gate_status(gate: str) -> dict:
    return get(f"/lifecycle/gates/{gate}")


g0, g1 = gate_status("G0"), gate_status("G1")
c = st.columns(5)
c[0].metric("Use case", uc)
c[1].metric("State", "not saved" if not summary["exists"] else "submitted" if summary["submitted"] else "draft")
c[2].metric("Risk tier", summary.get("tier") or "—")
c[3].metric("G0", f"{GATE_ICON.get(g0['record']['status'], '⬜')} {g0['record']['status']}")
c[4].metric("G1", f"{GATE_ICON.get(g1['record']['status'], '⬜')} {g1['record']['status']}")
if not clk["simulated"]:
    st.info("The intake form writes into a simulation run's workspace. Start one with `make sim-new`, "
            "then run `make sim-api` and `make sim-ui`.")

t = st.tabs(["G0 · Use case form", "G0 · Checks and sign-off", "G1 · Limits and KPIs", "G1 · Checks and sign-off", "Generated card"])

# ------------------------------------------------------------------------------------------- G0 form
with t[0]:
    st.caption(f"Acting as **{persona['name']}**. Saving writes `usecase/{uc}.yaml`, the risk file and the card "
               "into the run's workspace and logs the change in the evidence ledger.")
    with st.form("usecase"):
        title = st.text_input("Title", rec["title"])
        o = st.columns(3)
        sponsor = o[0].selectbox("Sponsor", pids, index=pids.index(rec["sponsor"]), format_func=people.get)
        bo = o[1].selectbox("Business owner", pids, index=pids.index(rec["business_owner"]), format_func=people.get)
        po = o[2].selectbox("Product owner", pids, index=pids.index(rec["product_owner"]), format_func=people.get)
        problem = st.text_area("Problem", rec["problem"], height=90)
        solution = st.text_area("Proposed solution", rec["proposed_solution"], height=90)
        st.markdown("**Value hypothesis**")
        value = st.data_editor(pd.DataFrame(rec["value_hypothesis"]), num_rows="dynamic", width="stretch", key="value")
        s = st.columns(2)
        scope_in = s[0].text_area("In scope (one per line)", "\n".join(rec["scope_in"]))
        scope_out = s[1].text_area("Out of scope (one per line)", "\n".join(rec["scope_out"]))
        st.markdown("**Data classification** (the Data owner confirms it after saving)")
        data_rows = st.data_editor(pd.DataFrame(rec["data_classification"]["rows"]), num_rows="dynamic", width="stretch",
                                   key="dataclass")
        st.markdown("**Risk questionnaire** (the tier is computed from these answers)")
        rk, q = rec["risk"], st.columns(4)
        risk = {"customer_outcome": q[0].checkbox("Affects customer outcomes or money", rk["customer_outcome"]),
                "takes_actions": q[1].checkbox("Takes actions in systems (writes, payments)", rk["takes_actions"]),
                "personal_data": q[2].checkbox("Processes personal data", rk["personal_data"]),
                "special_category": q[3].checkbox("Special-category data (health etc.)", rk["special_category"]),
                "regulatory_exposure": q[0].checkbox("Regulatory exposure", rk["regulatory_exposure"]),
                "autonomy": q[1].selectbox("Autonomy", ["none", "recommend", "decide_some", "decide_all"],
                                           index=["none", "recommend", "decide_some", "decide_all"].index(rk["autonomy"])),
                "max_autonomous_usd": q[2].number_input("Max autonomous amount (USD)", value=float(rk["max_autonomous_usd"]))}
        st.markdown("**Feasibility**")
        f = rec["feasibility"]
        verdicts = ["Go", "Go with conditions", "No go"]
        verdict = st.selectbox("Verdict", verdicts, index=verdicts.index(f["verdict"]))
        rationale = st.text_area("Rationale", f["rationale"], height=70)
        cond = pd.DataFrame(f["conditions"] or [{"condition": "", "owner": pids[0], "due": None}])
        cond["due"] = pd.to_datetime(cond["due"]).dt.date
        conditions = st.data_editor(cond, num_rows="dynamic", width="stretch", key="conditions", column_config={
            "owner": st.column_config.SelectboxColumn("owner", options=pids, required=True),
            "due": st.column_config.DateColumn("due", required=True)})
        st.markdown("**RACI** (exactly one Accountable per activity)")
        raci = st.data_editor(pd.DataFrame(rec["raci"]), num_rows="dynamic", width="stretch", key="raci")
        saved = st.form_submit_button("💾 Save record", type="primary", disabled=not clk["simulated"])
    if saved:
        record = {"id": uc, "title": title, "sponsor": sponsor, "business_owner": bo, "product_owner": po,
                  "problem": problem, "proposed_solution": solution, "value_hypothesis": rows(value),
                  "scope_in": [x.strip() for x in scope_in.splitlines() if x.strip()],
                  "scope_out": [x.strip() for x in scope_out.splitlines() if x.strip()],
                  "data_classification": {"rows": rows(data_rows), "confirmed_by": None}, "risk": risk,
                  "feasibility": {"verdict": verdict, "rationale": rationale, "conditions": rows(conditions)},
                  "raci": rows(raci)}
        if try_action(post, "/usecases", {"by": persona["id"], "record": record}, success="Record saved"):
            st.rerun()
    a = st.columns(3)
    if summary["exists"] and not summary["submitted"]:
        if a[0].button("📤 Submit for G0", type="primary"):
            if try_action(post, f"/usecases/{uc}/submit", {"by": persona["id"]}, success="Submitted for G0"):
                st.rerun()
    if summary["exists"] and not summary["record"]["data_classification"]["confirmed_by"]:
        is_owner = "Data owner" in persona["roles"]
        if a[1].button("🔐 Confirm data classification", disabled=not is_owner,
                       help=None if is_owner else "Only the Data owner can confirm (switch persona)"):
            if try_action(post, f"/usecases/{uc}/classification/confirm", {"by": persona["id"]}, success="Classification confirmed"):
                st.rerun()
    if summary["exists"]:
        st.caption(f"Computed tier **{summary['tier']}**: " + "; ".join(summary["tier_reasons"]) +
                   ". Rules: " + " · ".join(summary["tier_rules"]))


def checks_table(gs: dict):
    st.dataframe(pd.DataFrame([{"check": x["check"], "status": ok_badge(x["ok"], "pass", "fail"), "detail": x["detail"]}
                               for x in gs["checks"]]), hide_index=True, width="stretch")


with t[1]:
    checks_table(g0)
    gate_signoff(g0, persona, "g0")

# ------------------------------------------------------------------------------------------- G1
with t[2]:
    if not summary["exists"]:
        st.info("Save and submit the G0 record first.")
    else:
        g1t = get(f"/usecases/{uc}/g1/template")
        st.caption("The Business owner confirms the authority limits, KPI targets and thresholds. The engine checks "
                   "they equal `system.yaml` and the constants in `routing.py`.")
        with st.form("g1"):
            lc = st.columns(3)
            stp = lc[0].number_input("STP limit (USD)", value=float(g1t["stp_limit_usd"]))
            sig = lc[1].number_input("Significance (net % reduction)", value=float(g1t["significance_net_pct"]))
            conf = lc[2].number_input("Confidence cut-off", value=float(g1t["confidence_cutoff"]), step=0.05)
            hitl = st.text_input("HITL points (comma separated)", ", ".join(g1t["hitl_points"]))
            kpis = st.data_editor(pd.DataFrame([{"kpi": k, "target": v} for k, v in g1t["kpi_targets"].items()]),
                                  width="stretch", key="kpis")
            ack = st.columns(2)
            thr = ack[0].checkbox("Eval thresholds agreed (evals/thresholds.yaml)", g1t["thresholds_acknowledged"])
            slo = ack[1].checkbox("SLOs agreed (observability/slos.yaml)", g1t["slos_acknowledged"])
            ok = st.form_submit_button("✅ Confirm as Business owner", type="primary",
                                       disabled="Business owner (Head of Claims)" not in persona["roles"])
        if ok:
            data = {"stp_limit_usd": stp, "significance_net_pct": sig, "confidence_cutoff": conf,
                    "hitl_points": [x.strip() for x in hitl.split(",") if x.strip()],
                    "kpi_targets": {r["kpi"]: str(r["target"]) for r in rows(kpis)},
                    "thresholds_acknowledged": thr, "slos_acknowledged": slo}
            if try_action(post, f"/usecases/{uc}/g1", {"by": persona["id"], "data": data}, success="G1 confirmed"):
                st.rerun()
        if "Business owner (Head of Claims)" not in persona["roles"]:
            st.caption("Only the Business owner can confirm. Switch persona in the sidebar.")

with t[3]:
    checks_table(g1)
    gate_signoff(g1, persona, "g1")

with t[4]:
    if summary["exists"]:
        st.markdown(summary["card"])
        st.dataframe(pd.DataFrame([{"file": v["path"], "sha256": v["sha256"]} for v in summary["files"].values()]),
                     hide_index=True, width="stretch")
    else:
        st.info("Not saved yet.")
