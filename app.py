"""
app.py — Kalshi MLB Paper-Trading Research Dashboard (v1)

Run with:  python -m streamlit run app.py
"""
import json
import sqlite3
from datetime import date
from typing import Optional

import pandas as pd
import streamlit as st

from config import load_config
from db.schema import init_db
from game_state.memory import GameStateMemory
from ingest import split_transcript, ingest_messages
from reporting.daily_summary import generate_daily_summary
from reporting.pace_fade_report import get_pace_fade_candidates, get_pace_fade_summary_stats
from trading.fee_calculator import FeeConfig

# ─────────────────────────────────────────────────────────────────────────────
# Page config (must be first Streamlit call)
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MLB Signal Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# Design system CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Global ─────────────────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"] { font-family: 'Inter', system-ui, sans-serif; }

/* Remove default top padding */
.block-container { padding-top: 1.5rem !important; padding-bottom: 2rem !important; }

/* ── Sidebar ────────────────────────────────────────────────── */
section[data-testid="stSidebar"] {
    background: #080c17 !important;
    border-right: 1px solid rgba(255,255,255,0.05) !important;
}
section[data-testid="stSidebar"] .stRadio label {
    font-size: 13px;
    font-weight: 500;
    color: #94a3b8;
    padding: 6px 10px;
    border-radius: 6px;
    transition: color .15s;
}
section[data-testid="stSidebar"] .stRadio label:hover { color: #e2e8f0; }

/* ── Stat cards (custom HTML) ───────────────────────────────── */
.stat-grid { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 4px; }
.stat-card {
    flex: 1; min-width: 110px;
    background: #131929;
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
    padding: 14px 16px 12px;
    position: relative;
    overflow: hidden;
}
.stat-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: var(--accent, #4f8ef7);
    border-radius: 10px 10px 0 0;
}
.stat-label {
    font-size: 10px; font-weight: 700;
    letter-spacing: .8px; text-transform: uppercase;
    color: #64748b; margin-bottom: 6px;
}
.stat-value {
    font-size: 22px; font-weight: 800;
    font-variant-numeric: tabular-nums;
    color: #f1f5f9; line-height: 1.15;
}
.stat-sub {
    font-size: 11px; color: #64748b;
    margin-top: 3px; font-weight: 500;
}
.stat-value.pos { color: #22c55e; }
.stat-value.neg { color: #ef4444; }
.stat-value.warn { color: #f59e0b; }

/* ── Section header ─────────────────────────────────────────── */
.page-header {
    display: flex; align-items: baseline; gap: 10px;
    margin-bottom: 20px; padding-bottom: 12px;
    border-bottom: 1px solid rgba(255,255,255,0.06);
}
.page-title {
    font-size: 20px; font-weight: 800; color: #f1f5f9;
    letter-spacing: -.3px;
}
.page-sub {
    font-size: 12px; color: #475569; font-weight: 500;
}

/* ── Section divider ────────────────────────────────────────── */
.section-head {
    font-size: 11px; font-weight: 700;
    letter-spacing: .8px; text-transform: uppercase;
    color: #475569; margin: 20px 0 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid rgba(255,255,255,0.05);
}

/* ── Badge pills ────────────────────────────────────────────── */
.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px; font-weight: 700;
    letter-spacing: .3px;
    white-space: nowrap;
    line-height: 18px;
}

/* ── Signal log rows ────────────────────────────────────────── */
.log-row {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 10px; border-radius: 6px;
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.04);
    margin-bottom: 4px;
    font-size: 13px;
}
.log-row .game { color: #94a3b8; font-weight: 600; font-size: 12px; }
.log-row .price { font-variant-numeric: tabular-nums; color: #e2e8f0; font-weight: 600; }
.log-row .dim { color: #475569; font-size: 11px; }

/* ── Metric delta override ──────────────────────────────────── */
div[data-testid="metric-container"] {
    background: #131929;
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
    padding: 12px 14px;
}
div[data-testid="stMetricLabel"] { font-size: 10px !important; font-weight: 700 !important;
    letter-spacing: .7px !important; text-transform: uppercase !important; color: #64748b !important; }
div[data-testid="stMetricValue"] { font-size: 22px !important; font-weight: 800 !important;
    font-variant-numeric: tabular-nums !important; color: #f1f5f9 !important; }

/* ── Tables / dataframes ────────────────────────────────────── */
div[data-testid="stDataFrame"] {
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-radius: 8px !important;
    overflow: hidden;
}
iframe { border-radius: 8px !important; }

/* ── Buttons ────────────────────────────────────────────────── */
div[data-testid="stButton"] button[kind="primary"] {
    background: #4f8ef7 !important;
    border: none !important;
    font-weight: 700 !important;
    letter-spacing: .3px !important;
}

/* ── Text inputs ────────────────────────────────────────────── */
div[data-baseweb="input"] input {
    background: #0f1525 !important;
    border-color: rgba(255,255,255,0.08) !important;
}

/* ── Tabs ───────────────────────────────────────────────────── */
button[data-baseweb="tab"] {
    font-size: 13px !important; font-weight: 600 !important;
}

/* ── Expander ───────────────────────────────────────────────── */
details {
    background: rgba(255,255,255,0.02) !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-radius: 8px !important;
    padding: 2px 0 !important;
}
details > summary {
    font-weight: 600 !important;
    font-size: 13px !important;
    padding: 10px 14px !important;
    color: #94a3b8 !important;
}

/* ── Success / warning / error banners ──────────────────────── */
div[data-testid="stAlert"] {
    border-radius: 8px !important;
    border-left-width: 3px !important;
    font-size: 13px !important;
}

/* ── Divider ────────────────────────────────────────────────── */
hr { border-color: rgba(255,255,255,0.06) !important; margin: 18px 0 !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Resources (cached across reruns)
# ─────────────────────────────────────────────────────────────────────────────
DB_PATH = "kalshi_mlb.db"


@st.cache_resource
def get_db() -> sqlite3.Connection:
    return init_db(DB_PATH)


@st.cache_resource
def get_memory() -> GameStateMemory:
    return GameStateMemory()


@st.cache_resource
def get_fee_cfg() -> FeeConfig:
    cfg = load_config()
    return FeeConfig(
        taker_fee_rate=cfg.taker_fee_rate,
        maker_fee_rate=cfg.maker_fee_rate,
        fee_multiplier=cfg.fee_multiplier,
    )


conn = get_db()
memory = get_memory()
fee_cfg = get_fee_cfg()

# ─────────────────────────────────────────────────────────────────────────────
# Design tokens
# ─────────────────────────────────────────────────────────────────────────────
SIGNAL_COLORS: dict[str, str] = {
    "midgame_blowup_fade":         "#ef4444",
    "fade_overreaction":           "#f97316",
    "stability_over":              "#4f8ef7",
    "stability_under":             "#22c55e",
    "pace_fade_under_candidate":   "#a855f7",
    "lagging_reprice":             "#06b6d4",
    "trap_no_bet":                 "#475569",
    "no_chase_over":               "#64748b",
    "too_early_too_risky":         "#64748b",
    "unresolved_needs_enrichment": "#eab308",
    "high_line_under_ladder":      "#9333ea",
    "exit_offset":                 "#14b8a6",
}
ACTION_COLORS = {"paper_entry": "#22c55e", "skipped": "#475569", "candidate": "#eab308"}
STATUS_COLORS = {"open": "#4f8ef7", "settled": "#22c55e", "exited": "#f97316"}

LABELS: dict[str, str] = {
    "midgame_blowup_fade":         "MIDGAME BLOWUP",
    "fade_overreaction":           "FADE",
    "stability_over":              "STABILITY OVER",
    "stability_under":             "STABILITY UNDER",
    "pace_fade_under_candidate":   "PACE FADE",
    "lagging_reprice":             "LAG REPRICE",
    "trap_no_bet":                 "TRAP / NO BET",
    "no_chase_over":               "NO CHASE",
    "too_early_too_risky":         "TOO EARLY",
    "unresolved_needs_enrichment": "UNRESOLVED",
    "high_line_under_ladder":      "LADDER",
    "exit_offset":                 "EXIT",
}

ALL_TYPES = list(SIGNAL_COLORS.keys())

# ─────────────────────────────────────────────────────────────────────────────
# HTML component helpers
# ─────────────────────────────────────────────────────────────────────────────

def badge(text: str, bg: str = "#475569", fg: str = "#fff") -> str:
    return (f'<span class="badge" style="background:{bg};color:{fg}">'
            f'{text}</span>')


def sig_badge(t: str, subtype: Optional[str] = None) -> str:
    b = badge(LABELS.get(t, t.upper().replace("_", " ")), SIGNAL_COLORS.get(t, "#64748b"))
    if subtype:
        sub_label = LABELS.get(subtype, subtype.upper().replace("_", " "))
        sub_color = SIGNAL_COLORS.get(subtype, "#334155")
        b += f'&nbsp;<span class="badge" style="background:{sub_color};color:#fff;opacity:.85;font-size:10px">{sub_label}</span>'
    return b


def action_badge(a: Optional[str]) -> str:
    a = a or "skipped"
    return badge(a.upper().replace("_", " "), ACTION_COLORS.get(a, "#475569"))


def status_badge(s: str) -> str:
    return badge(s.upper(), STATUS_COLORS.get(s, "#475569"))


def page_header(title: str, sub: str = "") -> None:
    sub_html = f'<span class="page-sub">{sub}</span>' if sub else ""
    st.markdown(
        f'<div class="page-header">'
        f'<span class="page-title">{title}</span>{sub_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def section_head(text: str) -> None:
    st.markdown(f'<div class="section-head">{text}</div>', unsafe_allow_html=True)


def stat_cards(items: list[dict]) -> None:
    """
    Render a row of stat cards.
    items: [{"label", "value", "sub"?, "accent"?, "cls"?}, ...]
    """
    html = '<div class="stat-grid">'
    for it in items:
        accent = it.get("accent", "#4f8ef7")
        cls    = it.get("cls", "")
        sub    = f'<div class="stat-sub">{it["sub"]}</div>' if it.get("sub") else ""
        html += (
            f'<div class="stat-card" style="--accent:{accent}">'
            f'<div class="stat-label">{it["label"]}</div>'
            f'<div class="stat-value {cls}">{it["value"]}</div>'
            f'{sub}'
            f'</div>'
        )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def _pnl(cents: Optional[int]) -> str:
    if cents is None:
        return "—"
    sign = "+" if cents >= 0 else ""
    return f"{sign}{cents}¢"


def _pnl_cls(cents: Optional[int]) -> str:
    if cents is None or cents == 0:
        return ""
    return "pos" if cents > 0 else "neg"


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────
PAGES = ["Ingest", "Signals", "Positions", "Candidates", "Daily Summary", "Data Health"]

with st.sidebar:
    st.markdown(
        '<div style="padding:4px 0 16px">'
        '<div style="font-size:16px;font-weight:800;color:#f1f5f9;letter-spacing:-.2px">MLB Dashboard</div>'
        '<div style="font-size:11px;color:#475569;margin-top:2px;font-weight:500">Kalshi paper-trading research</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    page = st.radio("nav", PAGES, label_visibility="collapsed")
    st.markdown("<hr style='margin:12px 0'>", unsafe_allow_html=True)

    # Live DB stats in sidebar
    n_raw  = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
    n_sig  = conn.execute("SELECT COUNT(*) FROM signal_events").fetchone()[0]
    n_pos  = conn.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]
    n_open = conn.execute("SELECT COUNT(*) FROM paper_positions WHERE status='open'").fetchone()[0]
    net_all = conn.execute(
        "SELECT COALESCE(SUM(net_pnl_cents),0) FROM paper_positions WHERE status!='open'"
    ).fetchone()[0]

    st.markdown(
        f'<div style="font-size:11px;color:#475569;line-height:1.9;font-weight:500">'
        f'<span style="color:#64748b">Messages</span>&nbsp;&nbsp;'
        f'<span style="color:#94a3b8;font-weight:700">{n_raw:,}</span><br>'
        f'<span style="color:#64748b">Signals</span>&nbsp;&nbsp;&nbsp;&nbsp;'
        f'<span style="color:#94a3b8;font-weight:700">{n_sig:,}</span><br>'
        f'<span style="color:#64748b">Positions</span>&nbsp;&nbsp;'
        f'<span style="color:#94a3b8;font-weight:700">{n_pos}</span>'
        f'&nbsp;<span style="color:#4f8ef7">({n_open} open)</span><br>'
        f'<span style="color:#64748b">Net P/L</span>&nbsp;&nbsp;&nbsp;'
        f'<span style="color:{"#22c55e" if net_all >= 0 else "#ef4444"};font-weight:700">'
        f'{"+" if net_all >= 0 else ""}{net_all}¢</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div style="font-size:10px;color:#2d3748;margin-top:16px">{DB_PATH}</div>',
        unsafe_allow_html=True,
    )

# =============================================================================
# INGEST
# =============================================================================
if page == "Ingest":
    page_header("Ingest Transcript", "Upload or paste a Discord feed to process")

    tab_paste, tab_upload = st.tabs(["Paste Text", "Upload File"])
    with tab_paste:
        paste_text = st.text_area(
            "transcript",
            height=220,
            placeholder="Paste ⚾-separated Discord messages here…",
            label_visibility="collapsed",
            key="paste_text",
        )
    with tab_upload:
        uploaded = st.file_uploader("Upload .txt", type=["txt"], key="upload_file",
                                    label_visibility="collapsed")
        if uploaded is not None:
            st.session_state["upload_text"] = uploaded.read().decode("utf-8")
            st.success(f"Loaded {len(st.session_state['upload_text']):,} chars from `{uploaded.name}`")

    ic1, ic2 = st.columns([4, 1])
    run_btn    = ic1.button("Run Ingest", type="primary", use_container_width=True)
    paper_mode = ic2.selectbox("Mode", ["realistic", "optimistic"],
                               label_visibility="collapsed", key="paper_mode")

    if run_btn:
        raw_text = (
            st.session_state.get("upload_text", "")
            or st.session_state.get("paste_text", "")
            or ""
        ).strip()
        if not raw_text:
            st.warning("Paste or upload a transcript first.")
        else:
            msgs = split_transcript(raw_text)
            if not msgs:
                st.error("No parseable ⚾ message chunks found.")
            else:
                with st.spinner(f"Processing {len(msgs)} message chunks…"):
                    stats = ingest_messages(msgs, conn, memory, fee_cfg, paper_mode)
                st.session_state["last_stats"] = stats
                st.session_state["last_n_chunks"] = len(msgs)
                st.session_state.pop("upload_text", None)

    # ── Results ──────────────────────────────────────────────────────────────
    if "last_stats" in st.session_state:
        stats    = st.session_state["last_stats"]
        n_chunks = st.session_state.get("last_n_chunks", 0)
        failures = stats.get("failures", [])
        n_fail   = len(failures)
        n_dup    = max(0, stats["skipped"] - n_fail)
        sig_log  = stats.get("signal_log", [])
        entries_log = [s for s in sig_log if s.get("pos_id")]
        skips_log   = [s for s in sig_log if not s.get("pos_id")]
        blowup_n    = sum(1 for s in entries_log if s["signal_type"] == "midgame_blowup_fade")

        st.markdown("<hr>", unsafe_allow_html=True)
        section_head("Last Run Results")

        if n_fail == 0 and stats["parsed"] > 0:
            st.success(f"Ingest complete — {stats['parsed']} messages processed, {stats['entries']} position(s) opened.")
        elif stats["parsed"] == 0 and n_dup > 0:
            st.warning(f"No new messages — {n_dup} duplicate(s) skipped. Transcript already ingested.")
        elif n_fail > 0:
            st.warning(f"Complete with {n_fail} parse failure(s).")
        else:
            st.info("Ingest complete — no signals fired.")

        stat_cards([
            {"label": "Chunks Split", "value": str(n_chunks),           "accent": "#64748b"},
            {"label": "Parsed",       "value": str(stats["parsed"]),     "accent": "#4f8ef7"},
            {"label": "Skipped",      "value": str(stats["skipped"]),    "accent": "#475569"},
            {"label": "Failures",     "value": str(n_fail),
             "accent": "#ef4444" if n_fail else "#475569",
             "cls": "warn" if n_fail else ""},
            {"label": "Signals",      "value": str(stats["signals"]),    "accent": "#a855f7"},
            {"label": "Entries",      "value": str(stats["entries"]),    "accent": "#22c55e",
             "cls": "pos" if stats["entries"] else ""},
            {"label": "PF Explosions","value": str(stats.get("pace_fade_explosions", 0)), "accent": "#9333ea"},
            {"label": "PF Rows",      "value": str(stats.get("pace_fade_rows", 0)),       "accent": "#7c3aed"},
            {"label": "Blowup Entries","value": str(blowup_n),
             "accent": "#ef4444" if blowup_n else "#475569",
             "cls": "neg" if blowup_n else ""},
        ])
        st.markdown("<br>", unsafe_allow_html=True)

        # Parse failures
        if failures:
            with st.expander(f"Parse failures / unrecognised  ({n_fail})", expanded=True):
                for f in failures:
                    bg = "#dc2626" if "error" in f["reason"].lower() else "#b45309"
                    st.markdown(
                        f'{badge(f["reason"], bg)} &nbsp;'
                        f'<span style="color:#94a3b8;font-size:12px">#{f["index"]}</span> '
                        f'<code style="font-size:11px;color:#64748b">{f["snippet"][:120]}</code>',
                        unsafe_allow_html=True,
                    )
        else:
            st.success("No parse failures.")

        # Signal log
        if sig_log:
            with st.expander(
                f"Signal log — {len(entries_log)} entries opened,  {len(skips_log)} skipped",
                expanded=bool(entries_log),
            ):
                if entries_log:
                    st.markdown(
                        '<div class="section-head">Positions opened</div>',
                        unsafe_allow_html=True,
                    )
                    for s in entries_log:
                        st.markdown(
                            f'<div class="log-row">'
                            f'{sig_badge(s["signal_type"])}'
                            f'{badge("ENTRY", "#22c55e")}'
                            f'<span class="game">{s["game_id"]}</span>'
                            f'<span class="price">{s["side"]} @{s["price"]}¢</span>'
                            f'<span class="dim">conf={s["conf"]:.2f}</span>'
                            f'<span class="dim">pos #{s["pos_id"]}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                if skips_log:
                    st.markdown(
                        '<div class="section-head" style="margin-top:12px">Skipped / no-bet</div>',
                        unsafe_allow_html=True,
                    )
                    for s in skips_log:
                        reason = s.get("blocked_by") or "low-conf / trap"
                        st.markdown(
                            f'<div class="log-row">'
                            f'{sig_badge(s["signal_type"])}'
                            f'{badge("SKIP", "#334155")}'
                            f'<span class="game">{s["game_id"]}</span>'
                            f'<span class="dim">{reason}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
        elif stats["parsed"] > 0:
            st.info("No signals fired on this batch.")

# =============================================================================
# SIGNALS
# =============================================================================
elif page == "Signals":
    page_header("Live Signals", "All classifier events — entries and skips")

    fc = st.columns([2, 2, 2, 1])
    f_game   = fc[0].text_input("Game (e.g. WSH@SF)", key="sig_game", placeholder="WSH@SF")
    f_type   = fc[1].selectbox("Signal type", ["(all)"] + ALL_TYPES, key="sig_type")
    f_action = fc[2].selectbox("Action", ["(all)", "paper_entry", "skipped", "candidate"], key="sig_action")
    f_limit  = fc[3].number_input("Limit", 10, 1000, 200, 10, key="sig_limit")

    where, params = [], []
    if f_game.strip():
        where.append("game_id = ?"); params.append(f_game.strip())
    if f_type != "(all)":
        where.append("signal_type = ?"); params.append(f_type)
    if f_action != "(all)":
        where.append("action_taken = ?"); params.append(f_action)

    rows = conn.execute(
        "SELECT id, created_at, game_id, signal_type, signal_subtype, confidence, market_line, "
        "entry_side, entry_price_cents, blocked_by, action_taken, reason "
        "FROM signal_events"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY created_at DESC LIMIT ?",
        params + [int(f_limit)],
    ).fetchall()

    st.markdown(
        f'<div style="font-size:12px;color:#475569;margin-bottom:8px">'
        f'{len(rows)} event(s) matched</div>',
        unsafe_allow_html=True,
    )

    if not rows:
        st.info("No signal events match the current filters.")
    else:
        df = pd.DataFrame([{
            "Time":    r["created_at"][:16].replace("T", " ") if r["created_at"] else "—",
            "Game":    r["game_id"],
            "Type":    LABELS.get(r["signal_type"], r["signal_type"]),
            "Subtype": LABELS.get(r["signal_subtype"], "") if r["signal_subtype"] else "",
            "Action":  (r["action_taken"] or "—").upper(),
            "Side":    r["entry_side"] or "—",
            "Line":    r["market_line"],
            "Entry¢":  r["entry_price_cents"],
            "Conf":    round(r["confidence"], 3),
            "Blocked": r["blocked_by"] or "",
        } for r in rows])

        st.dataframe(
            df, use_container_width=True, hide_index=True,
            column_config={
                "Conf": st.column_config.ProgressColumn(
                    "Conf", min_value=0, max_value=1, format="%.2f"),
                "Line": st.column_config.NumberColumn("Line", format="%.1f"),
                "Entry¢": st.column_config.NumberColumn("Entry¢"),
            },
        )

        st.divider()
        section_head("Event Detail")
        sel_idx = st.selectbox(
            "event",
            range(len(rows)),
            format_func=lambda i: (
                f"#{rows[i]['id']}  {rows[i]['game_id']}  ·  "
                f"{LABELS.get(rows[i]['signal_type'], rows[i]['signal_type'])}"
                + (f" / {LABELS.get(rows[i]['signal_subtype'], rows[i]['signal_subtype'])}"
                   if rows[i]['signal_subtype'] else "")
                + f"  ·  {rows[i]['action_taken'] or 'skipped'}"
            ),
            label_visibility="collapsed",
            key="sig_detail_sel",
        )
        sel = rows[sel_idx]
        d1, d2 = st.columns(2)
        with d1:
            st.markdown(f"**Game** &nbsp; `{sel['game_id']}`", unsafe_allow_html=True)
            st.markdown(
                f"**Type** &nbsp; {sig_badge(sel['signal_type'], sel['signal_subtype'])}",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"**Action** &nbsp; {action_badge(sel['action_taken'])}",
                unsafe_allow_html=True,
            )
            st.markdown(f"**Confidence** &nbsp; `{sel['confidence']:.3f}`", unsafe_allow_html=True)
        with d2:
            st.markdown(f"**Line** &nbsp; `{sel['market_line']}`", unsafe_allow_html=True)
            st.markdown(f"**Side** &nbsp; `{sel['entry_side'] or '—'}`", unsafe_allow_html=True)
            st.markdown(f"**Entry** &nbsp; `{sel['entry_price_cents'] or '—'}¢`", unsafe_allow_html=True)
            st.markdown(
                f"**Blocked by** &nbsp; `{sel['blocked_by'] or 'not blocked'}`",
                unsafe_allow_html=True,
            )
        with st.expander("Classification reason"):
            st.code(sel["reason"], language=None)

# =============================================================================
# POSITIONS
# =============================================================================
elif page == "Positions":
    page_header("Paper Positions", "Fee-adjusted performance tracking")

    pc = st.columns([1, 2, 2, 1])
    p_status = pc[0].selectbox("Status", ["(all)", "open", "settled", "exited"], key="pos_status")
    p_sig    = pc[1].selectbox("Signal type", ["(all)"] + ALL_TYPES, key="pos_sig")
    p_game   = pc[2].text_input("Game", key="pos_game", placeholder="e.g. WSH@SF")
    p_limit  = pc[3].number_input("Limit", 10, 1000, 100, 10, key="pos_limit")

    where, params = [], []
    if p_status != "(all)":
        where.append("status = ?"); params.append(p_status)
    if p_sig != "(all)":
        where.append("signal_type = ?"); params.append(p_sig)
    if p_game.strip():
        where.append("game_id = ?"); params.append(p_game.strip())

    pos_rows = conn.execute(
        "SELECT * FROM paper_positions"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY created_at DESC LIMIT ?",
        params + [int(p_limit)],
    ).fetchall()

    open_n    = sum(1 for r in pos_rows if r["status"] == "open")
    settled_n = sum(1 for r in pos_rows if r["status"] == "settled")
    exited_n  = sum(1 for r in pos_rows if r["status"] == "exited")
    net_pnl   = sum((r["net_pnl_cents"] or 0) for r in pos_rows if r["status"] != "open")
    wins      = sum(1 for r in pos_rows if (r["net_pnl_cents"] or 0) > 0 and r["status"] != "open")

    stat_cards([
        {"label": "Total",    "value": str(len(pos_rows)),  "accent": "#64748b"},
        {"label": "Open",     "value": str(open_n),         "accent": "#4f8ef7"},
        {"label": "Settled",  "value": str(settled_n),      "accent": "#22c55e"},
        {"label": "Exited",   "value": str(exited_n),       "accent": "#f97316"},
        {"label": "Net P/L",  "value": _pnl(net_pnl),
         "accent": "#22c55e" if net_pnl >= 0 else "#ef4444",
         "cls": _pnl_cls(net_pnl)},
        {"label": "Wins",     "value": str(wins),           "accent": "#22c55e",
         "cls": "pos" if wins else ""},
    ])
    st.markdown("<br>", unsafe_allow_html=True)

    if not pos_rows:
        st.info("No positions match the current filters.")
    else:
        df = pd.DataFrame([{
            "Status":   r["status"].upper(),
            "Game":     r["game_id"],
            "Line":     r["market_line"],
            "Side":     r["side"],
            "Entry¢":   r["realistic_entry_price_cents"],
            "Exit¢":    r["exit_price_cents"],
            "Gross¢":   r["gross_pnl_cents"],
            "Net¢":     r["net_pnl_cents"],
            "MFE":      r["mfe_cents"] or 0,
            "MAE":      r["mae_cents"] or 0,
            "Type":     LABELS.get(r["signal_type"], r["signal_type"]),
            "Subtype":  LABELS.get(r["signal_subtype"], "") if r["signal_subtype"] else "",
            "Conf":     round(r["confidence"], 2),
            "Opened":   r["created_at"][:16].replace("T", " ") if r["created_at"] else "—",
        } for r in pos_rows])

        st.dataframe(
            df, use_container_width=True, hide_index=True,
            column_config={
                "Line":   st.column_config.NumberColumn("Line", format="%.1f"),
                "Conf":   st.column_config.ProgressColumn("Conf", min_value=0, max_value=1, format="%.2f"),
                "Gross¢": st.column_config.NumberColumn("Gross¢"),
                "Net¢":   st.column_config.NumberColumn("Net¢"),
                "MFE":    st.column_config.NumberColumn("MFE"),
                "MAE":    st.column_config.NumberColumn("MAE"),
            },
        )

        st.divider()
        section_head("Position Detail")
        sel_idx = st.selectbox(
            "position",
            range(len(pos_rows)),
            format_func=lambda i: (
                f"#{pos_rows[i]['id']}  {pos_rows[i]['game_id']}  ·  "
                f"line {pos_rows[i]['market_line']}  ·  "
                f"{pos_rows[i]['signal_type']}  ·  {pos_rows[i]['status']}"
            ),
            label_visibility="collapsed",
            key="pos_detail_sel",
        )
        sel = pos_rows[sel_idx]
        d1, d2, d3 = st.columns(3)
        with d1:
            st.markdown(f"**Game** &nbsp; `{sel['game_id']}`", unsafe_allow_html=True)
            st.markdown(
                f"**Type** &nbsp; {sig_badge(sel['signal_type'], sel['signal_subtype'])}",
                unsafe_allow_html=True,
            )
            st.markdown(f"**Status** &nbsp; {status_badge(sel['status'])}", unsafe_allow_html=True)
            st.markdown(f"**Conf** &nbsp; `{sel['confidence']:.3f}`", unsafe_allow_html=True)
        with d2:
            st.markdown(f"**Line** &nbsp; `{sel['market_line']:.1f}`", unsafe_allow_html=True)
            st.markdown(f"**Side** &nbsp; `{sel['side']}`", unsafe_allow_html=True)
            st.markdown(
                f"**Entry** &nbsp; `{sel['realistic_entry_price_cents']}¢` "
                f"<span style='color:#475569;font-size:11px'>(fee {sel['entry_fee_cents']}¢)</span>",
                unsafe_allow_html=True,
            )
            if sel["exit_price_cents"] is not None:
                st.markdown(f"**Exit** &nbsp; `{sel['exit_price_cents']}¢`", unsafe_allow_html=True)
        with d3:
            pnl_color = "#22c55e" if (sel["net_pnl_cents"] or 0) > 0 else "#ef4444" if (sel["net_pnl_cents"] or 0) < 0 else "#94a3b8"
            st.markdown(
                f"**Gross P/L** &nbsp; "
                f'<span style="color:{pnl_color};font-weight:700">{_pnl(sel["gross_pnl_cents"])}</span>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f"**Net P/L** &nbsp; "
                f'<span style="color:{pnl_color};font-weight:700">{_pnl(sel["net_pnl_cents"])}</span>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f"**MFE / MAE** &nbsp; "
                f'<span style="color:#22c55e">{sel["mfe_cents"] or 0}¢</span> / '
                f'<span style="color:#ef4444">{sel["mae_cents"] or 0}¢</span>',
                unsafe_allow_html=True,
            )
        with st.expander("Entry reason / signal context"):
            st.code(sel["reason"], language=None)

# =============================================================================
# CANDIDATES
# =============================================================================
elif page == "Candidates":
    page_header("Candidate Research Board", "Pace-fade early explosions and midgame blowup signals")

    tab_pf, tab_blowup = st.tabs(["Pace-Fade Candidates", "Midgame Blowup Signals"])

    # ── Pace-Fade ─────────────────────────────────────────────────────────
    with tab_pf:
        fpc = st.columns([2, 2, 1, 1])
        pf_game  = fpc[0].text_input("Game", key="pf_game", placeholder="e.g. STL@NYM")
        pf_class = fpc[1].selectbox("Classification", [
            "(all)", "pace_fade_under_candidate", "unresolved_needs_enrichment",
            "no_chase_over", "too_early_too_risky",
        ], key="pf_class")
        pf_min   = fpc[2].slider("Min score", 0.0, 1.0, 0.0, 0.05, key="pf_score")
        pf_lim   = fpc[3].number_input("Limit", 10, 500, 100, 10, key="pf_lim")

        pf_rows = get_pace_fade_candidates(
            conn,
            game_id=pf_game.strip() or None,
            classification=None if pf_class == "(all)" else pf_class,
            limit=int(pf_lim),
        )
        pf_rows = [r for r in pf_rows if r["pace_fade_score"] >= pf_min]

        st.markdown(
            f'<div style="font-size:12px;color:#475569;margin-bottom:8px">'
            f'{len(pf_rows)} candidate row(s)</div>',
            unsafe_allow_html=True,
        )

        if not pf_rows:
            st.info(
                "No pace-fade candidates yet. They appear when an early-inning game "
                "(T1–T3) reaches 6+ runs and the pace-fade observer fires."
            )
        else:
            pf_display = []
            for r in pf_rows:
                flags   = json.loads(r["risk_flags_json"]) if r["risk_flags_json"] else []
                missing = json.loads(r["missing_context_json"]) if r["missing_context_json"] else []
                inning  = f"T{r['inning_number']}" if r["inning_half"] == "T" else f"B{r['inning_number']}"
                result  = "WIN" if r["under_won"] == 1 else "LOSS" if r["under_won"] == 0 else "—"
                pf_display.append({
                    "Game":    r["game_id"],
                    "Inn":     inning,
                    "Total":   r["current_total"],
                    "Line":    r["line"],
                    "Entry¢":  r["estimated_under_entry"],
                    "Score":   round(r["pace_fade_score"], 3),
                    "Class":   LABELS.get(r["classification"], r["classification"]),
                    "Flags":   ", ".join(flags[:2]) if flags else "",
                    "Missing": ", ".join(missing[:2]) if missing else "",
                    "Final":   r["final_total"],
                    "Result":  result,
                })

            st.dataframe(
                pd.DataFrame(pf_display), use_container_width=True, hide_index=True,
                column_config={
                    "Score": st.column_config.ProgressColumn(
                        "Score", min_value=0, max_value=1, format="%.3f"),
                    "Line":  st.column_config.NumberColumn("Line", format="%.1f"),
                },
            )

            st.divider()
            section_head("Score Breakdown")
            sel_pf = st.selectbox(
                "candidate",
                range(len(pf_rows)),
                format_func=lambda i: (
                    f"{pf_rows[i]['game_id']}  T{pf_rows[i]['inning_number']}  "
                    f"line={pf_rows[i]['line']:.1f}  score={pf_rows[i]['pace_fade_score']:.3f}  "
                    f"[{pf_rows[i]['classification']}]"
                ),
                label_visibility="collapsed",
                key="pf_detail_sel",
            )
            r = pf_rows[sel_pf]
            flags   = json.loads(r["risk_flags_json"]) if r["risk_flags_json"] else []
            missing = json.loads(r["missing_context_json"]) if r["missing_context_json"] else []

            stat_cards([
                {"label": "Total Score",        "value": f"{r['pace_fade_score']:.4f}",      "accent": "#a855f7"},
                {"label": "Early Explosion",    "value": f"{r['early_explosion_score']:.4f}","accent": "#7c3aed"},
                {"label": "Line Cushion Score", "value": f"{r['line_cushion_score']:.4f}",   "accent": "#6d28d9"},
                {"label": "Under Entry Value",  "value": f"{r['under_entry_value_score']:.4f}","accent":"#5b21b6"},
                {"label": "Cushion (runs)",     "value": f"{r['line_cushion']:.1f}r",         "accent": "#4f8ef7"},
                {"label": "Entry Estimate",     "value": f"{r['estimated_under_entry']}¢",    "accent": "#4f8ef7"},
                {"label": "Run Env",            "value": r["run_env_tag"] or "—",             "accent": "#64748b"},
                {"label": "Context Conf",       "value": f"{r['context_confidence']:.2f}",    "accent": "#64748b"},
            ])
            st.markdown("<br>", unsafe_allow_html=True)

            if flags:
                st.markdown(
                    "**Risk flags** &nbsp; " + "  ".join(badge(f, "#7f1d1d", "#fca5a5") for f in flags),
                    unsafe_allow_html=True,
                )
            if missing:
                st.markdown(
                    "**Missing context** &nbsp; " + "  ".join(badge(m, "#78350f", "#fcd34d") for m in missing),
                    unsafe_allow_html=True,
                )

            if r["final_total"] is not None:
                oc = "#166534" if r["under_won"] else "#7f1d1d"
                ft = "#86efac" if r["under_won"] else "#fca5a5"
                st.markdown(
                    f"**Outcome** &nbsp; "
                    + badge("WIN" if r["under_won"] else "LOSS", oc, ft)
                    + f'&nbsp; <span style="color:#64748b;font-size:12px">final total = {r["final_total"]}</span>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"**Outcome** &nbsp; " + badge("UNRESOLVED", "#78350f", "#fcd34d"),
                    unsafe_allow_html=True,
                )

    # ── Midgame Blowup ────────────────────────────────────────────────────
    with tab_blowup:
        fmb = st.columns([2, 2, 1])
        mb_game   = fmb[0].text_input("Game", key="mb_game", placeholder="e.g. WSH@SF")
        mb_action = fmb[1].selectbox("Action", ["(all)", "paper_entry", "skipped"], key="mb_action")
        mb_lim    = fmb[2].number_input("Limit", 10, 500, 100, key="mb_lim")

        mb_where = ["signal_type = 'midgame_blowup_fade'"]
        mb_params: list = []
        if mb_game.strip():
            mb_where.append("game_id = ?"); mb_params.append(mb_game.strip())
        if mb_action != "(all)":
            mb_where.append("action_taken = ?"); mb_params.append(mb_action)

        mb_rows = conn.execute(
            "SELECT id, created_at, game_id, signal_type, confidence, market_line, "
            "entry_side, entry_price_cents, blocked_by, action_taken, reason "
            "FROM signal_events WHERE " + " AND ".join(mb_where)
            + " ORDER BY created_at DESC LIMIT ?",
            mb_params + [int(mb_lim)],
        ).fetchall()

        st.markdown(
            f'<div style="font-size:12px;color:#475569;margin-bottom:8px">'
            f'{len(mb_rows)} blowup signal(s)</div>',
            unsafe_allow_html=True,
        )

        if not mb_rows:
            st.info(
                "No midgame blowup signals yet. "
                "These fire in innings ≥5 with score gap ≥3 and settled over price."
            )
        else:
            mb_display = []
            for r in mb_rows:
                mb_display.append({
                    "Time":    r["created_at"][:16].replace("T", " ") if r["created_at"] else "—",
                    "Game":    r["game_id"],
                    "Line":    r["market_line"],
                    "Side":    r["entry_side"] or "—",
                    "Entry¢":  r["entry_price_cents"],
                    "Conf":    round(r["confidence"], 3),
                    "Action":  (r["action_taken"] or "—").upper(),
                    "Blocked": r["blocked_by"] or "",
                })
            st.dataframe(
                pd.DataFrame(mb_display), use_container_width=True, hide_index=True,
                column_config={
                    "Line": st.column_config.NumberColumn("Line", format="%.1f"),
                    "Conf": st.column_config.ProgressColumn(
                        "Conf", min_value=0, max_value=1, format="%.2f"),
                },
            )

            st.divider()
            section_head("Signal Detail")
            sel_mb = st.selectbox(
                "blowup signal",
                range(len(mb_rows)),
                format_func=lambda i: (
                    f"#{mb_rows[i]['id']}  {mb_rows[i]['game_id']}  "
                    f"line={mb_rows[i]['market_line']}  "
                    f"|  {mb_rows[i]['action_taken'] or 'skipped'}"
                ),
                label_visibility="collapsed",
                key="mb_detail_sel",
            )
            sel = mb_rows[sel_mb]
            d1, d2 = st.columns(2)
            with d1:
                st.markdown(f"**Game** &nbsp; `{sel['game_id']}`", unsafe_allow_html=True)
                st.markdown(f"**Type** &nbsp; {sig_badge(sel['signal_type'])}", unsafe_allow_html=True)
                st.markdown(f"**Action** &nbsp; {action_badge(sel['action_taken'])}", unsafe_allow_html=True)
                st.markdown(f"**Confidence** &nbsp; `{sel['confidence']:.3f}`", unsafe_allow_html=True)
            with d2:
                st.markdown(f"**Line** &nbsp; `{sel['market_line']:.1f}`", unsafe_allow_html=True)
                st.markdown(f"**Entry** &nbsp; `{sel['entry_price_cents'] or '—'}¢`", unsafe_allow_html=True)
                st.markdown(f"**Side** &nbsp; `{sel['entry_side'] or '—'}`", unsafe_allow_html=True)
                st.markdown(f"**Blocked by** &nbsp; `{sel['blocked_by'] or 'not blocked'}`", unsafe_allow_html=True)
            with st.expander("Classification reason"):
                st.code(sel["reason"], language=None)

# =============================================================================
# DAILY SUMMARY
# =============================================================================
elif page == "Daily Summary":
    page_header("Daily Summary", "High-level metrics and signal performance")

    dsc = st.columns([2, 1])
    summary_date = dsc[0].date_input("Date", value=date.today(), key="sum_date")
    with dsc[1]:
        st.write("")
        if st.button("Refresh Summary", type="primary", key="sum_refresh"):
            st.session_state["summary"] = generate_daily_summary(conn, summary_date)

    if "summary" not in st.session_state:
        st.session_state["summary"] = generate_daily_summary(conn, summary_date)

    s = st.session_state.get("summary", {})
    if not s:
        st.info("Click Refresh to load the daily summary.")
    else:
        net_d  = s.get("net_pnl_dollars", 0)
        gross_d = s.get("gross_pnl_dollars", 0)

        stat_cards([
            {"label": "Raw Messages",  "value": str(s.get("total_messages", 0)),  "accent": "#64748b"},
            {"label": "Signals",       "value": str(s.get("total_signals", 0)),   "accent": "#a855f7"},
            {"label": "Entries",       "value": str(s.get("total_entries", 0)),   "accent": "#22c55e"},
            {"label": "Open",          "value": str(s.get("open_positions", 0)),  "accent": "#4f8ef7"},
            {"label": "Settled",       "value": str(s.get("settled_positions",0)),"accent": "#22c55e"},
            {"label": "Exited",        "value": str(s.get("exited_positions", 0)),"accent": "#f97316"},
            {"label": "Gross P/L",     "value": f"${gross_d:+.2f}",
             "accent": "#22c55e" if gross_d >= 0 else "#ef4444",
             "cls": "pos" if gross_d > 0 else "neg" if gross_d < 0 else ""},
            {"label": "Net P/L",       "value": f"${net_d:+.2f}",
             "accent": "#22c55e" if net_d >= 0 else "#ef4444",
             "cls": "pos" if net_d > 0 else "neg" if net_d < 0 else ""},
            {"label": "Avg MFE",       "value": f"{s.get('avg_mfe_cents', 0)}¢",  "accent": "#22c55e"},
            {"label": "Avg MAE",       "value": f"{s.get('avg_mae_cents', 0)}¢",  "accent": "#ef4444"},
        ])
        st.markdown("<br>", unsafe_allow_html=True)

        # Signal performance
        sig_stats = s.get("signal_stats", {})
        if sig_stats:
            st.divider()
            section_head("Signal Performance  ·  settled & exited positions only")
            perf = []
            for sig_type, d in sig_stats.items():
                perf.append({
                    "Signal":       LABELS.get(sig_type, sig_type),
                    "Trades":       d["count"],
                    "Wins":         d["wins"],
                    "Win%":         f"{d['win_rate']:.0%}",
                    "Net P/L (¢)":  d["net_pnl_cents"],
                })
            pdf = pd.DataFrame(perf).sort_values("Net P/L (¢)", ascending=False)
            st.dataframe(
                pdf, use_container_width=True, hide_index=True,
                column_config={"Net P/L (¢)": st.column_config.NumberColumn("Net P/L (¢)", format="%+d")},
            )
        else:
            st.info("No settled/exited positions yet — signal performance will appear after positions close.")

        # Pace-fade
        pf = s.get("pace_fade", {})
        if pf.get("total_candidate_rows", 0) > 0 or pf.get("total_explosion_snapshots", 0) > 0:
            st.divider()
            section_head("Pace-Fade  ·  observational")
            stat_cards([
                {"label": "Explosions",  "value": str(pf.get("total_explosion_snapshots", 0)), "accent": "#7c3aed"},
                {"label": "Candidates",  "value": str(pf.get("total_candidate_rows", 0)),      "accent": "#a855f7"},
                {"label": "Avg Score",   "value": f"{pf.get('avg_score', 0):.3f}",             "accent": "#9333ea"},
                {"label": "Unresolved",  "value": str(pf.get("unresolved_outcomes", 0)),       "accent": "#64748b"},
            ])
            wins   = pf.get("settled_wins", 0)
            losses = pf.get("settled_losses", 0)
            if wins or losses:
                total = wins + losses
                pct   = f"{wins/total:.0%}" if total else "—"
                st.markdown(
                    f'<div style="font-size:12px;color:#64748b;margin:8px 0 4px">'
                    f'Settled: {badge(str(wins)+" wins","#166534","#86efac")} '
                    f'{badge(str(losses)+" losses","#7f1d1d","#fca5a5")} '
                    f'&nbsp;{pct} win rate</div>',
                    unsafe_allow_html=True,
                )

            by_class = pf.get("by_classification", {})
            if by_class:
                st.markdown("<br>", unsafe_allow_html=True)
                class_df = pd.DataFrame([
                    {"Classification": k, "Count": v["count"], "Avg Score": f"{v['avg_score']:.3f}"}
                    for k, v in by_class.items()
                ])
                st.dataframe(class_df, use_container_width=True, hide_index=True)

            top = pf.get("top_candidates", [])
            if top:
                st.markdown("<br>", unsafe_allow_html=True)
                section_head("Top Candidates by Score")
                for t in top:
                    inn = f"T{t['inning_number']}" if t["inning_half"] == "T" else f"B{t['inning_number']}"
                    color = SIGNAL_COLORS.get(t["classification"], "#64748b")
                    st.markdown(
                        f'<div class="log-row">'
                        f'{badge(LABELS.get(t["classification"], t["classification"]), color)}'
                        f'<span class="game">{t["game_id"]} {inn}</span>'
                        f'<span class="price">line {t["line"]:.1f}</span>'
                        f'<span class="price" style="color:#a855f7">score {t["pace_fade_score"]:.3f}</span>'
                        f'<span class="dim">{t["estimated_under_entry"]}¢ entry</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

        # Midgame blowup
        blowup_n      = conn.execute(
            "SELECT COUNT(*) FROM signal_events WHERE signal_type='midgame_blowup_fade'"
        ).fetchone()[0]
        trap_blowup_n = conn.execute(
            "SELECT COUNT(*) FROM signal_events "
            "WHERE signal_type='trap_no_bet' AND reason LIKE '%midgame blowup%'"
        ).fetchone()[0]
        if blowup_n or trap_blowup_n:
            st.divider()
            section_head("Midgame Blowup  ·  all-time")
            stat_cards([
                {"label": "Blowup Signals Fired", "value": str(blowup_n),      "accent": "#ef4444"},
                {"label": "Trap / Blocked",        "value": str(trap_blowup_n), "accent": "#475569"},
            ])

# =============================================================================
# DATA HEALTH
# =============================================================================
elif page == "Data Health":
    page_header("Parser / Data Health", "Quality indicators for the ingest pipeline")

    h_date = st.date_input("Date", value=date.today(), key="health_date")
    prefix = h_date.isoformat() + "T"

    total_raw      = conn.execute("SELECT COUNT(*) FROM raw_messages WHERE received_at LIKE ?", (prefix+"%",)).fetchone()[0]
    total_parsed   = conn.execute("SELECT COUNT(*) FROM raw_messages WHERE parsed=1 AND received_at LIKE ?", (prefix+"%",)).fetchone()[0]
    total_unparsed = total_raw - total_parsed
    total_signals  = conn.execute("SELECT COUNT(*) FROM signal_events WHERE created_at LIKE ?", (prefix+"%",)).fetchone()[0]
    total_entries  = conn.execute("SELECT COUNT(*) FROM signal_events WHERE action_taken='paper_entry' AND created_at LIKE ?", (prefix+"%",)).fetchone()[0]
    total_traps    = conn.execute("SELECT COUNT(*) FROM signal_events WHERE signal_type='trap_no_bet' AND created_at LIKE ?", (prefix+"%",)).fetchone()[0]

    parse_rate  = total_parsed  / total_raw     * 100 if total_raw     else 0.0
    signal_rate = total_signals / total_parsed  * 100 if total_parsed  else 0.0
    entry_rate  = total_entries / total_signals * 100 if total_signals else 0.0

    stat_cards([
        {"label": "Raw Messages",  "value": str(total_raw),
         "accent": "#4f8ef7"},
        {"label": "Parsed",        "value": str(total_parsed),
         "accent": "#22c55e" if parse_rate > 90 else "#f97316",
         "cls": "pos" if parse_rate > 90 else "warn"},
        {"label": "Unrecognised",  "value": str(total_unparsed),
         "accent": "#ef4444" if total_unparsed > 5 else "#475569",
         "cls": "neg" if total_unparsed > 5 else ""},
        {"label": "Signals Fired", "value": str(total_signals), "accent": "#a855f7"},
        {"label": "Entries",       "value": str(total_entries), "accent": "#22c55e"},
        {"label": "Trap/No-Bet",   "value": str(total_traps),   "accent": "#475569"},
    ])

    st.markdown("<br>", unsafe_allow_html=True)
    stat_cards([
        {"label": "Parse Rate",  "value": f"{parse_rate:.1f}%",
         "cls": "pos" if parse_rate > 90 else "warn",
         "accent": "#22c55e" if parse_rate > 90 else "#f97316",
         "sub": "messages parsed successfully"},
        {"label": "Signal Rate", "value": f"{signal_rate:.1f}%",
         "accent": "#a855f7",
         "sub": "signals per parsed message"},
        {"label": "Entry Rate",  "value": f"{entry_rate:.1f}%",
         "accent": "#22c55e",
         "sub": "% signals that opened a position"},
    ])
    st.markdown("<br>", unsafe_allow_html=True)

    # Signals by type
    st.divider()
    section_head("Signals by Type")
    sig_counts = conn.execute(
        "SELECT signal_type, action_taken, COUNT(*) n FROM signal_events "
        "WHERE created_at LIKE ? GROUP BY signal_type, action_taken ORDER BY n DESC",
        (prefix + "%",),
    ).fetchall()
    if sig_counts:
        sig_df = pd.DataFrame([{
            "Signal": LABELS.get(r["signal_type"], r["signal_type"]),
            "Action": (r["action_taken"] or "—").upper(),
            "Count":  r["n"],
        } for r in sig_counts])
        st.dataframe(sig_df, use_container_width=True, hide_index=True)
    else:
        st.info("No signal events found for this date.")

    # Unrecognised messages
    st.divider()
    unrecog = conn.execute(
        "SELECT id, content, received_at FROM raw_messages "
        "WHERE parsed=0 AND received_at LIKE ? ORDER BY id DESC LIMIT 20",
        (prefix + "%",),
    ).fetchall()
    if unrecog:
        with st.expander(f"Unrecognised / unparsed messages ({len(unrecog)} shown)", expanded=False):
            for r in unrecog:
                ts = r["received_at"][:19].replace("T", " ") if r["received_at"] else "—"
                st.markdown(
                    f'<span style="color:#475569;font-size:11px">{ts}</span> &nbsp;'
                    f'<code style="font-size:11px">{r["content"][:140]}</code>',
                    unsafe_allow_html=True,
                )
    else:
        st.success("All messages for this date parsed successfully.")

    # All-time totals
    st.divider()
    section_head("Database Totals  ·  all time")
    stat_cards([
        {"label": "Raw Messages",    "value": str(conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]),           "accent": "#64748b"},
        {"label": "Game States",     "value": str(conn.execute("SELECT COUNT(*) FROM game_states").fetchone()[0]),            "accent": "#64748b"},
        {"label": "Signal Events",   "value": str(conn.execute("SELECT COUNT(*) FROM signal_events").fetchone()[0]),          "accent": "#a855f7"},
        {"label": "Paper Positions", "value": str(conn.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]),        "accent": "#22c55e"},
        {"label": "Markets",         "value": str(conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0]),                "accent": "#64748b"},
        {"label": "Pace-Fade Rows",  "value": str(conn.execute("SELECT COUNT(*) FROM pace_fade_training_rows").fetchone()[0]),"accent": "#9333ea"},
        {"label": "Games Seen",      "value": str(conn.execute("SELECT COUNT(DISTINCT game_id) FROM signal_events").fetchone()[0]), "accent": "#4f8ef7"},
        {"label": "Summaries",       "value": str(conn.execute("SELECT COUNT(*) FROM daily_summaries").fetchone()[0]),        "accent": "#475569"},
    ])
