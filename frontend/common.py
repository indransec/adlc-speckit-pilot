"""Shared helpers for the Streamlit pages: API client, chart specs, status badges."""
from __future__ import annotations

import os

import altair as alt
import httpx
import pandas as pd
import streamlit as st

API = os.environ.get("API_URL", "http://localhost:8000")

# dataviz reference palette: categorical slots in fixed order; status colours only with icon + label
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
STATUS = {"good": ("#0ca30c", "✅"), "warning": ("#fab219", "⚠️"), "serious": ("#ec835a", "🟠"), "critical": ("#d03b3b", "⛔")}
STATUS_LABEL = {
    "new": "🆕 new", "processing": "⏳ processing", "registered": "⏳ registered", "pending_documents": "📄 pending documents",
    "awaiting_l1": "🧑‍💼 awaiting L1", "awaiting_l2": "🧑‍⚖️ awaiting L2", "awaiting_investigation": "🔍 investigation",
    "awaiting_siu": "🕵️ awaiting SIU", "approved": "✔️ approved", "closed_paid": "✅ paid", "closed_rejected": "❌ rejected",
    "closed_incomplete": "🗂️ closed incomplete", "payment_on_hold": "⏸️ payment on hold", "siu_referred": "🚩 SIU referred",
    "shadow_complete": "👥 shadow complete", "manual_queue": "🛑 manual queue", "error": "⚠️ error",
}


def page(title: str, icon: str = "🧳"):
    st.set_page_config(page_title=f"{title} · Travel Claims", page_icon=icon, layout="wide")
    st.title(f"{icon} {title}")


def call(method: str, path: str, **kw):
    try:
        r = httpx.request(method, f"{API}{path}", timeout=kw.pop("timeout", 120), **kw)
    except httpx.HTTPError as e:
        st.error(f"Backend not reachable at {API} ({e}). Start it with `make api`.")
        st.stop()
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text
        raise RuntimeError(detail)
    return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.content


def get(path: str, **kw):
    return call("GET", path, **kw)


def post(path: str, json: dict | None = None, **kw):
    return call("POST", path, json=json or {}, **kw)


def try_action(fn, *a, success: str | None = None, **kw):
    try:
        out = fn(*a, **kw)
        if success:
            st.toast(success, icon="✅")
        return out
    except RuntimeError as e:
        st.error(str(e))
        return None


def status_label(s: str | None) -> str:
    return STATUS_LABEL.get(s or "", s or "")


def ok_badge(ok: bool | None, yes="met", no="not met") -> str:
    if ok is None:
        return "➖ n/a"
    return f"{STATUS['good'][1]} {yes}" if ok else f"{STATUS['critical'][1]} {no}"


def bar(df: pd.DataFrame, x: str, y: str, title: str = "", horizontal: bool = True, fmt: str = ",.0f",
        tooltip: list | None = None, height: int | None = None):
    """Single-series bar chart: one colour (slot 1), thin bars, rounded data end, value tooltip."""
    if df.empty:
        st.caption("No data yet.")
        return
    tip = tooltip or [alt.Tooltip(x, title=x), alt.Tooltip(y, title=y, format=fmt)]
    if horizontal:
        enc = dict(y=alt.Y(f"{x}:N", sort=None, title=None), x=alt.X(f"{y}:Q", title=y))
        mark = alt.MarkDef(type="bar", color=SERIES[0], cornerRadiusEnd=4, height={"band": 0.6})
    else:
        enc = dict(x=alt.X(f"{x}:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)), y=alt.Y(f"{y}:Q", title=y))
        mark = alt.MarkDef(type="bar", color=SERIES[0], cornerRadiusEnd=4, width={"band": 0.6})
    ch = alt.Chart(df, title=title).mark_bar(**{k: v for k, v in mark.to_dict().items() if k != "type"}).encode(
        **enc, tooltip=tip).properties(height=height or max(140, 28 * len(df)) if horizontal else height or 260)
    st.altair_chart(ch.configure_axis(grid=False, domainColor="#c3c2b7", tickColor="#c3c2b7")
                    .configure_view(strokeWidth=0), width="stretch")


def grouped_bar(df: pd.DataFrame, x: str, series: str, y: str, order: list[str], title: str = ""):
    """Two-to-four series on one axis, categorical slots in fixed order, legend + tooltips."""
    if df.empty:
        st.caption("No data yet.")
        return
    ch = alt.Chart(df, title=title).mark_bar(cornerRadiusEnd=4).encode(
        y=alt.Y(f"{x}:N", title=None, sort=None), x=alt.X(f"{y}:Q", title=y),
        yOffset=alt.YOffset(f"{series}:N", sort=order),
        color=alt.Color(f"{series}:N", scale=alt.Scale(domain=order, range=SERIES[:len(order)]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[x, series, alt.Tooltip(y, format=",.2f")]).properties(height=max(160, 40 * df[x].nunique()))
    st.altair_chart(ch.configure_axis(grid=False).configure_view(strokeWidth=0), width="stretch")


def money(v) -> str:
    return "–" if v is None else f"USD {v:,.2f}"
