"""Simulation console: the run, its clock, story progress and whether every fault was caught where it should be."""
import pandas as pd
import streamlit as st

from common import clock_badge, get, page, persona_picker, post, try_action

page("Simulation console", "🎬")
persona_picker()
clk = clock_badge()
if not clk["simulated"]:
    st.info("Start a simulation run: `make sim-new`, `make sim-api`, `make sim-ui`, then `make simulate-e2e ARGS=\"--run current\"`.")
    st.stop()

health = get("/health")
c = st.columns(4)
c[0].metric("Run", health.get("run") or "—")
c[1].metric("Release", health["release"])
c[2].metric("Simulation day", f"{clk['sim_day']:.1f}")
c[3].metric("Clock", "paused" if clk["paused"] else f"×{clk['rate']:g}")
with st.expander("Clock controls"):
    x = st.columns(4)
    if x[0].button("⏸ Pause"):
        try_action(post, "/clock", {"action": "pause"})
    if x[1].button("▶ Resume"):
        try_action(post, "/clock", {"action": "resume"})
    skip = x[2].number_input("Skip days", min_value=0.0, value=1.0, step=1.0)
    if x[3].button("⏩ Skip ahead"):
        try_action(post, "/clock", {"action": "skip_days", "value": skip})
    st.caption("The story runner moves the clock itself; use these when exploring by hand.")


@st.fragment(run_every=5)
def story_progress():
    s = get("/simulation/story")
    st.progress(s["done"] / s["total"] if s["total"] else 0.0, text=f"Story: {s['done']} of {s['total']} steps done")
    nxt = next((x for x in s["steps"] if not x["done"]), None)
    if nxt:
        st.caption(f"Next: step {nxt['n']} · day {nxt['day']} · {nxt['do']} {nxt['what']}")
    if s["last"]:
        with st.expander("Latest step outputs"):
            st.dataframe(pd.DataFrame([{"sim time": r["at"][:16].replace("T", " "), "step": r["step"], "action": r["do"],
                                        "ok": "✓" if r["ok"] else "✗", "output": r["output"][:160]} for r in reversed(s["last"])]),
                         hide_index=True, width="stretch")


story_progress()

st.subheader("Detection matrix")
refresh = st.button("↻ Rebuild from the ledger")
try:
    r = get("/simulation/report", params={"refresh": refresh})
except RuntimeError as e:
    st.caption(f"No report yet ({e}).")
    st.stop()
st.markdown(f"**{r['passed']} of {r['total']} faults caught as expected** · ledger "
            f"{'✅ intact' if r['ledger']['ok'] else '❌ BROKEN'} ({r['ledger']['events']} events, head `{r['ledger']['head'][:16]}`)")
st.dataframe(pd.DataFrame([{
    "fault": f"{x['fault']} {x['name']}", "injected (day)": x["injected_day"], "release": x["release"],
    "expected": (x["expected"].get("alert") or x["expected"].get("caught_at")), "caught by": x["caught_by"] or "—",
    "team": x["team"] or "", "paged": " → ".join(x["paged"]) or "—", "acknowledged": x["acknowledged_by"] or "—",
    "automatic action": "; ".join(x["auto_actions"]) or "—",
    "result": "✅" if x["passed"] else "❌ " + ", ".join(k for k, ok in x["checks"].items() if not ok)} for x in r["matrix"]]),
    hide_index=True, width="stretch")
t = st.tabs(["Gate decisions", "Releases", "Change requests", "Value KPIs"])
with t[0]:
    st.dataframe(pd.DataFrame(r["gates"]), hide_index=True, width="stretch")
with t[1]:
    st.dataframe(pd.DataFrame([{"release": x["version"], "commit": x["commit"][:10], "cut by": x["by"], "message": x["message"],
                                "outcome": (x["closed"]["state"] + ": " + x["closed"]["reason"]) if x["closed"] else "active"}
                               for x in r["releases"]]), hide_index=True, width="stretch")
with t[2]:
    st.dataframe(pd.DataFrame([{"CR": c["id"], "kind": c["kind"], "release": c["release"], "status": c["status"]} for c in r["changes"]]),
                 hide_index=True, width="stretch")
with t[3]:
    st.dataframe(pd.DataFrame(r["kpis"]).astype(str), hide_index=True, width="stretch")
st.download_button("⬇ Evidence pack for the whole run", data=get("/evidence-pack"), file_name="evidence-run.zip", mime="application/zip")
