"""ADLC lifecycle console: gates G0-G7, evidence, actions, approvals, rollout controls."""
import pandas as pd
import streamlit as st

from common import call, clock_badge, gate_signoff, get, ok_badge, page, persona_picker, post, try_action

page("ADLC lifecycle", "🛤️")
persona = persona_picker()
clock_badge()
GATE_ACTIONS = {"G2": ["validate_manifests"], "G3": ["unit_tests", "evals", "perf_test", "traceability"],
                "G5": ["bundle_release", "shadow_run", "kill_switch_drill", "rollback_drill"],
                "G6": ["hypercare"], "G7": ["g7_review"]}
ICON = {"approved": "✅", "pending": "⬜", "rejected": "❌", "approved_with_conditions": "🟡"}

rt = get("/system")["runtime"]
with st.container(border=True):
    st.markdown("**Rollout controls** (deploy/rollout-plan.md, deploy/rollback.md)")
    c = st.columns([2, 2, 2, 3])
    phase = c[0].selectbox("Rollout phase", ["shadow", "assisted", "full"], index=["shadow", "assisted", "full"].index(rt["mode"]))
    if c[0].button("Apply phase"):
        try_action(post, "/lifecycle/actions/set_phase", {"params": {"phase": phase}}, success=f"Phase {phase} requested")
    ks = c[1].toggle("Kill switch", value=rt["kill_switch"])
    if ks != rt["kill_switch"]:
        try_action(call, "PUT", "/runtime", json={"key": "kill_switch", "value": ks}, success="Kill switch updated")
        st.rerun()
    llm = c[2].selectbox("LLM mode", ["auto", "live", "mock"], index=["auto", "live", "mock"].index(rt["llm_mode"]),
                         help=f"effective: {rt['llm_effective']}")
    if llm != rt["llm_mode"]:
        try_action(call, "PUT", "/runtime", json={"key": "llm_mode", "value": llm}, success="LLM mode updated")
        st.rerun()
    c[3].write(f"mode **{rt['mode']}** · STP **{'on' if rt['straight_through_enabled'] else 'off'}** · payments "
               f"**{'on' if rt['payments_enabled'] else 'off'}** · LLM **{rt['llm_effective']}**")

gates = get("/lifecycle/gates")
cols = st.columns(len(gates))
for col, g in zip(cols, gates):
    st_ = g["record"]["status"]
    col.markdown(f"### {ICON.get(st_, '⬜')} {g['gate']}\n<small>{g['stage']}</small><br>"
                 f"<small>checks {sum(c['ok'] for c in g['checks'])}/{len(g['checks'])}</small>", unsafe_allow_html=True)

a, b, c = st.columns([2, 2, 3])
if a.button("🚀 Simulate the full lifecycle (G0 → G7)", type="primary",
            help="Runs every stage's actions, checks each gate and signs with simulated approvers (~3 min)"):
    r = try_action(post, "/lifecycle/actions/simulate_lifecycle", {})
    if r:
        st.session_state.setdefault("jobs", []).insert(0, r["job_id"])
if b.button("↺ Reset gate records"):
    try_action(call, "POST", "/lifecycle/gates/reset?frm=G0", success="Gates reset")
    st.rerun()


@st.fragment(run_every=2)
def jobs_panel():
    jobs = get("/lifecycle/jobs", params={"limit": 8})
    if not jobs:
        return
    running = [j for j in jobs if j["status"] in ("queued", "running")]
    with st.expander(f"Lifecycle jobs ({len(running)} running)", expanded=bool(running)):
        for j in jobs:
            icon = {"succeeded": "✅", "failed": "❌", "running": "⏳", "queued": "⏳"}[j["status"]]
            with st.expander(f"{icon} {j['action']} · {j['status']} · {j['started_at']}", expanded=j["status"] == "running"):
                st.code(j["output"] or "(running…)", language=None)


jobs_panel()

for g in gates:
    rec = g["record"]
    with st.expander(f"{ICON.get(rec['status'], '⬜')} {g['gate']} · {g['stage']} · {rec['status']}"
                     + (f" · {rec['decided_at'][:19]}" if rec.get("decided_at") else ""), expanded=False):
        st.dataframe(pd.DataFrame([{"evidence check": c["check"], "status": ok_badge(c["ok"], "pass", "fail"),
                                    "detail": c["detail"]} for c in g["checks"]]), hide_index=True, width="stretch")
        acts = GATE_ACTIONS.get(g["gate"], [])
        if acts:
            ac = st.columns(len(acts))
            for col, name in zip(ac, acts):
                if col.button(f"Run {name}", key=f"{g['gate']}-{name}"):
                    r = try_action(post, f"/lifecycle/actions/{name}", {})
                    if r:
                        st.toast(f"{name} started")
        st.caption(f"Named approvers: {', '.join(g['approvers_required'])} · previous gate approved: "
                   f"{g['previous_gate_approved']} · ready: {g['ready']}")
        gate_signoff(g, persona, g["gate"])
        if rec["status"] not in ("approved", "approved_with_conditions") and \
                st.button("Sign with all simulated approvers", key=f"sim-{g['gate']}",
                          help="One persona per required role, from ops/teams.yaml"):
            if try_action(post, f"/lifecycle/gates/{g['gate']}/approve", {"simulated": True},
                          success=f"{g['gate']} approved"):
                st.rerun()

st.header("Evidence store")
files = get("/lifecycle/evidence")
t = st.tabs(["G3 evals", "Traceability", "G4 RAI", "G5 release", "G6 hypercare", "G7 review"])
with t[0]:
    if "eval-report.json" in files:
        r = get("/lifecycle/evidence/eval-report.json")
        st.write(f"Generated {r['generated_at'][:19]} · llm **{r['llm_mode']}** · overall {ok_badge(r['passed'], 'passed', 'failed')}")
        m = pd.DataFrame([{"metric": k, "value": str(v["value"]), "threshold": str(v["threshold"]),
                           "status": ok_badge(v["passed"], "pass", "fail")} for k, v in r["metrics"].items()])
        st.dataframe(m, hide_index=True, width="stretch")
        st.dataframe(pd.DataFrame([{"case": x["case_id"], "suite": x["suite"], "requirements": ", ".join(x["requirements"]),
                                    "pass": x["pass"], "got": str(x["got"])[:120]} for x in r["cases"]]),
                     hide_index=True, width="stretch")
with t[1]:
    if "traceability-matrix.json" in files:
        tm = get("/lifecycle/evidence/traceability-matrix.json")
        st.write(f"{tm['requirements']} requirements · {tm.get('by_status')}")
        st.dataframe(pd.DataFrame([{"requirement": x["requirement"], "status": x["status"], "text": x["text"],
                                    "methods": ", ".join(f"{k}:{'✓' if v['passed'] else '·' if v['passed'] is None else '✗'}"
                                                         for k, v in x["methods"].items()),
                                    "eval cases": ", ".join(x["eval_cases"])} for x in tm["matrix"]]),
                     hide_index=True, width="stretch")
with t[2]:
    if "rai-report.json" in files:
        r = get("/lifecycle/evidence/rai-report.json")
        st.write(f"Overall {ok_badge(r['passed'], 'passed', 'failed')} · injection {r['injection']} · PII leaks "
                 f"{r['pii']['pii_leak_count']} ({r['pii']['values_checked']} sensitive values checked)")
        st.markdown(f"**Counterfactual fairness parity:** {r['fairness']['counterfactual']['counterfactual_fairness_parity']}")
        st.dataframe(pd.DataFrame(r["fairness"]["counterfactual"]["pairs"]), hide_index=True, width="stretch")
        st.markdown("**Automated red team** (docs/rai/red-team-plan.md objectives)")
        st.dataframe(pd.DataFrame(r["red_team"]), hide_index=True, width="stretch")
with t[3]:
    for f in ("release-record.json", "shadow-report.json", "drills.json", "perf-report.json"):
        if f in files:
            st.markdown(f"**{f}**")
            st.json(get(f"/lifecycle/evidence/{f}"), expanded=False)
with t[4]:
    if "hypercare-report.json" in files:
        h = get("/lifecycle/evidence/hypercare-report.json")
        st.write(f"{h['claims']} claims · {h['human_decisions']} human decisions · errors {h['errors']}")
        st.dataframe(pd.DataFrame(h["slos"]).astype(str), hide_index=True)
        st.dataframe(pd.DataFrame(h["kpis"]).astype(str), hide_index=True)
with t[5]:
    md = get("/lifecycle/review")["markdown"]
    st.markdown(md or "Not generated yet - run `g7_review` under G7.")
