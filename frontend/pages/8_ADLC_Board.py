"""ADLC board: where the use case stands at every gate, with the evidence behind each decision."""
from html import escape

import pandas as pd
import streamlit as st

from common import clock_badge, gate_signoff, get, ok_badge, page, persona_picker, post, try_action

page("ADLC board", "🗂️")
persona = persona_picker()
clock_badge()
SUB_ICON = {"Draft": "📝", "Doing": "🔧", "Awaiting approval": "🖊️", "Rejected": "⛔", "In operation": "🟢",
            "Raised": "🕓", "Approved": "✅", "Assisted": "🧑‍💼"}
TYPE_ICON = {"epic": "🎯", "release": "🚀", "change": "📝", "incident": "🚨"}
TYPE_ORDER = {"epic": 0, "release": 1, "change": 2, "incident": 3}
SEV_ICON = {"sev1": "🔴", "sev2": "🟠", "sev3": "🟡"}
STATUS_ICON = {"verified": "✅ verified", "modified": "⚠️ modified since approval", "missing": "⛔ missing"}


def card_html(c: dict) -> str:
    title = c["title"] if len(c["title"]) <= 70 else c["title"][:67] + "…"
    icon = "⛔" if c.get("closed") else SUB_ICON.get(c["sub_status"], "")
    lines = [f"{TYPE_ICON[c['type']]} <b>{escape(c['id'])}</b>", escape(title), f"{icon} {escape(c['sub_status'])}"]
    if c.get("checks_total"):
        lines.append(f"checks {c['checks_passed']}/{c['checks_total']}")
    if c.get("approvers_required") and not c.get("closed"):
        lines.append(f"signed {len(c.get('signed', []))}/{len(c['approvers_required'])}")
    if c["type"] == "epic":
        lines.append(f"tier {c.get('tier') or '—'} · release {c['release']}")
    elif c["type"] == "release":
        lines.append(f"commit {c['commit']}" + (f" · {c['phase']}" if c.get("phase") else ""))
    elif c["type"] == "incident":
        lines.append(f"{SEV_ICON.get(c['severity'], '')} {c['severity']} · {c['team']}" + (f" · ack {c['ack_by']}" if c.get("ack_by") else ""))
    else:
        lines.append(f"for {c['release']}")
    lines.append(f"{c['days_in_column']} days here" + (f" · {c['artifacts']} artifacts" if c["artifacts"] else ""))
    if c["drift"]:
        lines.append(f"⚠️ {len(c['drift'])} changed since approval")
    if c["blocked"]:
        lines.append(f"⛔ {escape(c['reason'])}")
    return "<br>".join(lines)


@st.fragment(run_every=5)
def board_view():
    b = get("/board")
    cols = st.columns(len(b["columns"]), gap="small")
    for col, spec in zip(cols, b["columns"]):
        col.markdown(f"<div style='font-size:0.8rem;font-weight:600;border-bottom:2px solid #23445d;"
                     f"padding-bottom:4px;margin-bottom:6px'>{spec['title']}</div>", unsafe_allow_html=True)
        for c in sorted((i for i in b["items"] if i["column"] == spec["id"]), key=lambda i: TYPE_ORDER[i["type"]]):
            with col.container(border=True):
                st.markdown(f"<div style='font-size:0.75rem;line-height:1.35'>{card_html(c)}</div>", unsafe_allow_html=True)
    st.caption(f"Ledger head {b['ledger_head'][:16]} · cards move only when a gate is approved · refreshes every 5 s")
    return b


b = board_view()
if not b["items"]:
    st.info("No use case yet. Create one on the **Use case intake** page.")
    st.stop()

st.divider()
labels = {i["id"]: f"{TYPE_ICON[i['type']]} {i['id']} · {i['sub_status']}" for i in b["items"]}
item_id = st.selectbox("Card details", list(labels), format_func=labels.get)
d = get(f"/board/items/{item_id}")
t = st.tabs(["Current gate", "Decisions and approvers", "Artifacts", "History", "Ledger"])
with t[0]:
    g = d["current_gate"]
    if g:
        st.markdown(f"**{g['gate']} · {g['stage']}**: {d['card']['sub_status']} · approvers: {', '.join(g['approvers_required'])}")
        st.dataframe(pd.DataFrame([{"check": c["check"], "status": ok_badge(c["ok"], "pass", "fail"), "detail": c["detail"]}
                                   for c in g["checks"]]), hide_index=True, width="stretch")
        gate_signoff(g, persona, f"board-{item_id}")
    elif d["card"]["type"] == "change":
        c = d["card"]
        st.markdown(f"**{c['id']}** · {c['title']} · {c['sub_status']} · approvers: {', '.join(c['approvers_required'])}"
                    f" · signed: {', '.join(c['signed']) or '—'}")
        if c["sub_status"] == "Raised" and c["kind"] != "go_live":
            mine = [r for r in c["approvers_required"] if r in persona["roles"] and r not in c["signed"]]
            if not mine:
                st.caption(f"{persona['name']} cannot decide this change. Switch persona (CAB Chair: Nora Mwangi).")
            else:
                with st.form(f"cr-{c['id']}"):
                    notes = st.text_input("Notes")
                    x, y = st.columns(2)
                    ok, no = x.form_submit_button(f"✅ Approve as {mine[0]}", type="primary"), y.form_submit_button("❌ Reject")
                if ok or no:
                    body = {"persona": persona["id"], "decision": "approved" if ok else "rejected", "notes": notes}
                    if try_action(post, f"/changes/{c['id']}/decide", body, success=f"{c['id']} decided"):
                        st.rerun()
        elif c["kind"] == "go_live":
            st.caption("The go-live change is decided by the G5 sign-off (CAB Chair + Head of Claims).")
    else:
        st.write(d["card"]["sub_status"])
with t[1]:
    if not d["decisions"]:
        st.caption("No gate decisions yet.")
    for x in reversed(d["decisions"]):
        icon = "✅" if x["decision"] != "rejected" else "❌"
        with st.expander(f"{icon} {x['gate']} {x['decision']} · release {x['release']} · {x['decided_at'][:16]} · "
                         f"manifest {x['manifest_sha256'][:12]}"):
            st.dataframe(pd.DataFrame([{"role": a["role"], "signed by": a.get("name"), "decision": a.get("decision", x["decision"]),
                                        "notes": a.get("notes", ""), "at": (a.get("at") or "")[:16]} for a in x["approvers"]]),
                         hide_index=True, width="stretch")
            if x["notes"]:
                st.write(f"Notes: {x['notes']}")
            st.dataframe(pd.DataFrame([{"check": c["check"], "status": ok_badge(c["ok"], "pass", "fail"), "detail": c["detail"]}
                                       for c in x["checks"]]), hide_index=True, width="stretch")
with t[2]:
    for x in reversed(d["decisions"]):
        st.markdown(f"**{x['gate']} {x['decision']}** · frozen {x['decided_at'][:16]} · manifest `{x['manifest_sha256']}`")
        st.dataframe(pd.DataFrame([{"artifact": a["name"], "path": a["path"], "sha256": a["sha256"][:16],
                                    "bytes": a["bytes"], "required": a["required"], "on change": a["on_drift"],
                                    "live file": STATUS_ICON[a["status"]]} for a in x["artifacts"]]),
                     hide_index=True, width="stretch")
        if x["missing"]:
            st.warning("Missing at decision time: " + ", ".join(x["missing"]))
        st.download_button(f"⬇ Evidence pack: {x['gate']} {x['release']}", key=f"pack-{x['seq']}", mime="application/zip",
                           data=get("/evidence-pack", params={"release": x["release"], "gate": x["gate"]}),
                           file_name=f"evidence-{x['release']}-{x['gate']}.zip")
with t[3]:
    st.dataframe(pd.DataFrame([{"seq": e["seq"], "sim time": e["ts"][:16].replace("T", " "), "event": e["type"],
                                "gate": e.get("gate", ""), "by": e.get("by") or e.get("persona") or ", ".join(e.get("approvers", [])),
                                "hash": e["hash"][:12]} for e in reversed(d["history"])]), hide_index=True, width="stretch")
with t[4]:
    if st.button("🔎 Verify the evidence ledger"):
        v = try_action(get, "/ledger/verify")
        if v:
            (st.success if v["ok"] else st.error)(
                f"{'Intact' if v['ok'] else 'BROKEN'}: {v['events']} events, {v['manifests']} manifests, "
                f"{v['artifacts_checked']} artifact copies checked · head {v['head'][:16]}")
            for p in v["problems"]:
                st.write(f"- {p}")
    st.download_button("⬇ Evidence pack for the whole run", data=get("/evidence-pack"), file_name="evidence-run.zip",
                       mime="application/zip")
