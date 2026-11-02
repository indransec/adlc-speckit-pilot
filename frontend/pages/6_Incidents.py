"""Incidents: alerts routed to the on-call teams, their automatic actions, acknowledgements and root causes."""
import pandas as pd
import streamlit as st

from common import clock_badge, get, page, persona_picker, post, try_action

page("Incidents", "🚨")
persona = persona_picker()
clock_badge()
SEV = {"sev1": "🔴 sev1", "sev2": "🟠 sev2", "sev3": "🟡 sev3"}
STATUS = {"open": "🔥 open", "acknowledged": "👀 acknowledged", "resolved": "✅ resolved", "closed": "📕 closed"}
st.caption("Alertmanager opens an incident when an alert fires and pages the owning team in "
           "[GoAlert](http://localhost:8081) (pages arrive in [Mailpit](http://localhost:8025)). "
           "Dashboards: [Grafana](http://localhost:3000/d/adlc-sim) · rules: [Prometheus](http://localhost:9090/alerts).")


@st.fragment(run_every=5)
def incident_table():
    inc = get("/incidents")
    if not inc:
        st.info("No incidents yet. They appear when an alert fires during rollout or hypercare.")
        return inc
    c = st.columns(4)
    c[0].metric("Open", sum(i["status"] in ("open", "acknowledged") for i in inc))
    c[1].metric("Sev1", sum(i["severity"] == "sev1" for i in inc))
    c[2].metric("Awaiting root cause", sum(i["status"] == "resolved" for i in inc))
    c[3].metric("Closed", sum(i["status"] == "closed" for i in inc))
    st.dataframe(pd.DataFrame([{"incident": i["id"], "severity": SEV.get(i["severity"], i["severity"]), "alert": i["alert"],
                                "team": i["team"], "status": STATUS[i["status"]], "opened (sim)": i["opened"][:16].replace("T", " "),
                                "acknowledged by": i.get("ack_by") or "", "automatic action": "; ".join(i["actions"])}
                               for i in reversed(inc)]), hide_index=True, width="stretch")
    return inc


inc = incident_table()
if inc:
    sel = st.selectbox("Incident", [i["id"] for i in reversed(inc)])
    i = next(x for x in inc if x["id"] == sel)
    st.markdown(f"**{i['id']} · {i['alert']}** ({SEV.get(i['severity'])}) · {i['summary']} · value {i['value']} · "
                f"release {i['release']} in {i['mode']} mode · team **{i['team']}**"
                + (f" · informed: {', '.join(i['inform'])}" if i["inform"] else ""))
    st.dataframe(pd.DataFrame([{"sim time": t["at"][:16].replace("T", " "), "event": t["event"], "by": t["by"] or "",
                                "note": t["note"]} for t in i["timeline"]]), hide_index=True, width="stretch")
    if i.get("rca"):
        st.success(f"Root cause: {i['rca']}")
    a, b, c = st.columns(3)
    if i["status"] in ("open", "resolved") and not i.get("ack_by") and a.button(f"👀 Acknowledge as {persona['name']}"):
        if try_action(post, f"/incidents/{sel}/ack", {"persona": persona["id"]}, success="Acknowledged"):
            st.rerun()
    with b.form(f"note-{sel}"):
        note = st.text_input("Action taken")
        if st.form_submit_button("Add note") and note:
            if try_action(post, f"/incidents/{sel}/note", {"persona": persona["id"], "text": note}):
                st.rerun()
    if i["status"] == "resolved":
        with c.form(f"close-{sel}"):
            rca = st.text_area("Root cause", height=80)
            if st.form_submit_button("📕 Close with root cause"):
                if try_action(post, f"/incidents/{sel}/close", {"persona": persona["id"], "text": rca}, success="Closed"):
                    st.rerun()

with st.expander("Fault injection (simulation)"):
    fl = get("/faults")
    st.dataframe(pd.DataFrame([{"fault": f["id"], "name": f["name"], "kind": f["kind"], "active": "🔥" if f["active"] else "",
                                "expected alert": (f.get("expected") or {}).get("alert") or "—",
                                "team": (f.get("expected") or {}).get("team", "")} for f in fl]), hide_index=True, width="stretch")
    runtime = [f["id"] for f in fl if f["kind"] == "runtime"]
    x, y = st.columns([2, 1])
    fid = x.selectbox("Runtime fault", runtime)
    on = next(f["active"] for f in fl if f["id"] == fid)
    if y.button("Clear" if on else "Inject", key="fault-toggle"):
        if try_action(post, f"/faults/{fid}", {"on": not on, "by": persona["id"]}, success=f"{fid} {'cleared' if on else 'injected'}"):
            st.rerun()
