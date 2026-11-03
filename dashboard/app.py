"""My trading assistant - dashboard. Built for someone who does not want to think about technology:
open it, read the green/red status, tap a button if it asks you to. Everything is in rupees and plain words."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import streamlit as st

from tassist.db import SupabaseDB
from tassist import actions, rating, settings as S
from tassist.rotation import rate_rotation
from tassist.stats import perf
from tassist.strategies import BY_NAME
from tassist.universe import normalize_symbol, display_name, inst_for

st.set_page_config(page_title="My trading assistant", page_icon="📈", layout="centered")
IST = "Asia/Kolkata"

# ------------------------------------------------------------------ optional password
_pw = st.secrets.get("APP_PASSWORD", None)
if _pw and not st.session_state.get("authed"):
    st.title("📈 My trading assistant")
    entered = st.text_input("Password", type="password")
    if entered and entered == _pw:
        st.session_state["authed"] = True
        st.rerun()
    st.stop()


@st.cache_resource
def get_db():
    return SupabaseDB(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_SERVICE_KEY"])


@st.cache_data(ttl=45, show_spinner=False)
def load(table: str, limit: int = 20000) -> pd.DataFrame:
    order = "published.desc" if table == "news" else "id.desc"
    return pd.DataFrame(get_db().select(table, order=order, limit=limit))


def refresh():
    st.cache_data.clear()
    st.rerun()


def to_ist(s):
    return pd.to_datetime(s, utc=True, errors="coerce").dt.tz_convert(IST)


def inr(x, signed=False):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    s = f"₹{abs(x):,.0f}"
    return (("+" if x >= 0 else "-") + s) if signed else s


def plain(msg: str) -> str:
    return msg.replace("<b>", "").replace("</b>", "")


def prep_trades(df):
    if df.empty:
        return df
    df = df.copy()
    for c in ("opened_at", "closed_at"):
        if c in df:
            df[c] = to_ist(df[c])
    df["r_multiple"] = pd.to_numeric(df["r_multiple"], errors="coerce")
    df["taken"] = df["taken"].fillna(True).astype(bool)
    df["meta"] = df["meta"].apply(lambda m: m if isinstance(m, dict) else {})
    return df


cfg = S.load(get_db())
RISK = S.risk_amount(cfg)
trades = prep_trades(load("trades"))
now_ist = pd.Timestamp.now(tz=IST)

NAV = ["🏠 Today", "📊 Is it working?", "📅 Monthly stock list", "➕ Add or close a trade", "⚙️ Settings", "🔍 Details"]
page = st.sidebar.radio("Go to", NAV, label_visibility="collapsed")
if st.sidebar.button("🔄 Refresh"):
    refresh()
st.sidebar.caption("Everything here is practice-tracked at the prices the assistant saw. "
                   "It is a helper, not a promise: you place every order yourself.")

DECISION_TEXT = {
    "OK": "✅ Sent to you", "NOT_WORKING": "Held back - this idea type isn't working", "MUTED": "Held back - switched off",
    "UNPROVEN": "Held back - not proven yet (you chose 'only good ideas')", "PRICE_MOVED": "Held back - price had already moved",
    "REGIME_DOWN": "Held back - the market is weak", "REGIME_UP": "Held back - the market is strong",
    "NEWS_NEGATIVE": "Held back - bad news on the stock", "NEWS_POSITIVE": "Held back - good news on the stock",
    "PAUSED": "Held back - you paused ideas", "TURNED_OFF": "Held back - you turned this type off",
    "ALERT_CAP": "Held back - enough ideas for today", "BAD_LEVELS": "Held back",
}
MODE_NAME = {"intraday": "Same-day trades (stocks)", "option": "Options (Nifty, Bank Nifty, crude)", "swing": "Few-days-to-weeks trades"}


def trade_card(t, system_undecided=False):
    """One open trade, in plain words, with the right buttons."""
    meta = t.get("meta") or {}
    long = t["side"] == "LONG"
    label = BY_NAME[t["strategy"]].plain if t.get("strategy") in BY_NAME else "Your own trade"
    with st.container(border=True):
        st.markdown(f"**{'🟢 BUY' if long else '🔴 SELL first'} {t['name']}** · {label}")
        st.write(f"{'Bought' if long else 'Sold'} around **{t['entry']:,.2f}**")
        if t.get("qty"):
            st.caption(f"{int(t['qty'])} shares")
        st.write(f"🛑 Stop-loss: **{t['sl']:,.2f}**" + ("  _(already moved to protect you)_" if t.get("t1_hit") else ""))
        st.write(f"🎯 First target: {t['t1']:,.2f}" + ("  ✅ reached" if t.get("t1_hit") else "") + f"  ·  Final target: {t['t2']:,.2f}")
        sug = meta.get("exit_suggested")
        if sug:
            why = {"SETUP_LOST": "the reason for the trade no longer holds", "STOP": "stop-loss reached",
                   "TRAIL_STOP": "stop-loss reached", "TARGET": "final target reached", "EOD": "the day is ending",
                   "MAX_HOLD": "time limit reached"}.get(sug.get("reason"), "time to leave")
            st.error(f"⚠️ **{'SELL' if long else 'BUY BACK'} now** - {why} (price about {sug.get('price') or 0:,.2f})")
        if system_undecided:
            c1, c2 = st.columns(2)
            if c1.button("✅ I bought it", key=f"took{t['id']}"):
                actions.took(get_db(), t["id"], None)
                refresh()
            if c2.button("⏭ Skip", key=f"skip{t['id']}"):
                actions.skip(get_db(), t["id"])
                refresh()
        else:
            default = float(sug["price"]) if sug and sug.get("price") else 0.0
            px = st.number_input("Price I sold at", min_value=0.0, value=default, format="%.2f", key=f"px{t['id']}")
            if st.button("✅ I've sold it", key=f"sold{t['id']}"):
                actions.sold(get_db(), t["id"], px or None, now_ist.to_pydatetime())
                refresh()


# ====================================================================== TODAY
if page == NAV[0]:
    st.title("🏠 Today")
    runs = load("runs", 200)
    market_hours = now_ist.weekday() < 5 and 9 <= now_ist.hour < 23
    if runs.empty:
        st.info("Waiting for the first automatic check. This can take up to 15 minutes after the clock service starts.")
    else:
        runs["started_at"] = to_ist(runs["started_at"])
        age = (now_ist - runs["started_at"].max()).total_seconds() / 60
        if market_hours and age > 45:
            st.error(f"⚠️ I haven't checked the market for {age:.0f} minutes. The automatic clock may have stopped - "
                     "see 'If something stops working' in your guide.")
        else:
            st.success(f"✅ Running normally. Last check {age:.0f} minutes ago.")
    if cfg["paused"]:
        st.warning("⏸ New trade ideas are paused (change this in ⚙️ Settings). I'll still warn you about trades you're in.")

    if trades.empty:
        st.info("No trade ideas yet. When one comes, it appears here and on Telegram. Most days there are 0 to 2.")
    else:
        op = trades[trades["status"] == "OPEN"]
        mine = op[op["source"] == "user"]
        undecided = op[(op["source"] == "system") & op["taken"] & ~op["meta"].apply(lambda m: bool(m.get("muted_followups")))]
        st.subheader("Trades I'm watching for you")
        if mine.empty and undecided.empty:
            st.caption("Nothing open right now.")
        for _, t in mine.iterrows():
            trade_card(t.to_dict())
        if not undecided.empty:
            st.caption("New ideas - did you take them?")
            for _, t in undecided.iterrows():
                trade_card(t.to_dict(), system_undecided=True)

        sig = load("signals", 300)
        st.subheader("Today's ideas")
        if sig.empty:
            st.caption("None yet.")
        else:
            sig["created_at"] = to_ist(sig["created_at"])
            td = sig[sig["created_at"].dt.date == now_ist.date()]
            if td.empty:
                st.caption("No ideas today so far - that's normal. I only speak up when something looks worth it.")
            else:
                st.dataframe(pd.DataFrame({"Time": td["created_at"].dt.strftime("%H:%M"), "Stock": td["name"],
                                           "Idea": td["side"].map({"LONG": "Buy", "SHORT": "Sell first"}),
                                           "What happened": td["decision"].map(lambda d: DECISION_TEXT.get(d, d))}),
                             hide_index=True)
        done = trades[(trades["status"] == "CLOSED") & (trades["source"] == "system") & trades["taken"]]
        m = done[done["closed_at"] >= now_ist.replace(day=1, hour=0, minute=0, second=0)]
        if len(m):
            st.subheader("This month")
            st.metric("Finished practice trades", len(m))
            st.metric("Net result if you'd followed every idea", inr(m["r_multiple"].sum() * RISK, True),
                      help=f"Assumes you risk {inr(RISK)} per trade.")

# ====================================================================== IS IT WORKING
elif page == NAV[1]:
    st.title("📊 Is it working?")
    st.write("Every idea is tracked as a practice trade, including ones I held back, so I can tell you honestly what works. "
             "A new approach needs about **30 finished trades** before its record means anything.")
    closed = trades[(trades["status"] == "CLOSED") & (trades["source"] == "system")] if not trades.empty else trades
    rts = rating.compute_ratings(get_db(), closed.to_dict("records") if not closed.empty else [])
    best = rating.best_per_mode(rts)
    st.caption("🟢 proven in live practice · 🟡 looks promising · ⚪ too early to tell · 🔴 not working (I don't send these)")
    for mode in ("intraday", "option", "swing"):
        st.subheader(MODE_NAME[mode])
        rows = []
        for (name, md), r in rts.items():
            if md != mode:
                continue
            stt = r["stats"] or {}
            star = " ⭐" if best.get(mode) == name and r["key"] in ("PROVEN", "PROMISING") else ""
            rows.append({"": r["emoji"], "Approach": BY_NAME[name].plain + star,
                         "Wins out of 100": None if stt.get("win_rate") is None else round(stt["win_rate"]),
                         "Typical win vs typical loss": None if not stt.get("payoff") else f"{stt['payoff']:.1f}x",
                         f"Average per trade (risking {inr(RISK)})": None if stt.get("avg_r") is None else inr(stt["avg_r"] * RISK, True),
                         "Trades": stt.get("n"), "Based on": r["basis"]})
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    st.subheader("Monthly stock list")
    rs = get_db().kv_get("rotation_state", {}) or {}
    rr = rate_rotation(rs.get("history", []))
    st.write(f"{rr['emoji']} **{rr['label']}** - {rr['text']}.")
    st.info("**How to read this:** 'Wins out of 100' alone means little. Many good approaches win under half the time but make "
            "bigger wins than losses ('typical win vs typical loss' above 1x). What matters is the average per trade being above zero, "
            "over enough trades.")

    st.subheader("My own trades")
    mine = trades[(trades["status"] == "CLOSED") & (trades["source"] == "user")] if not trades.empty else trades
    if mine.empty:
        st.caption("Trades you close will be summed up here.")
    else:
        def pnl(r):
            sign = 1 if r["side"] == "LONG" else -1
            if r.get("qty") and r.get("exit_price"):
                return (r["exit_price"] - r["entry"]) * r["qty"] * sign
            return (r["r_multiple"] or 0) * RISK
        mine = mine.assign(rs=mine.apply(pnl, axis=1))
        c1, c2, c3 = st.columns(3)
        c1.metric("Trades", len(mine))
        c2.metric("Winners", f"{(mine['rs'] > 0).mean() * 100:.0f}%")
        c3.metric("Net result", inr(mine["rs"].sum(), True))
    with st.expander("Technical details (for the curious)"):
        if not closed.empty:
            tab = [dict(strategy=s, mode=m, **perf(g["r_multiple"].tolist())) for (s, m), g in closed.groupby(["strategy", "mode"])]
            st.dataframe(pd.DataFrame(tab))
            st.caption("avg_r = average result in multiples of the money risked per trade. t_stat above ~1.65 means the result is unlikely to be luck.")
            sg = load("signals")
            if not sg.empty:
                st.caption("Do my safety rules help? Average result by reason for being held back:")
                j = closed.merge(sg[["trade_id", "decision"]], left_on="id", right_on="trade_id", how="left")
                st.dataframe(j.groupby("decision")["r_multiple"].agg(["count", "mean"]))

# ====================================================================== MONTHLY LIST
elif page == NAV[2]:
    st.title("📅 Monthly stock list")
    st.write("Once a month (the last trading day) I pick the 20 stocks that have risen the most, most smoothly, over the past "
             "6-12 months. If Nifty is below its 200-day average price, my rule is to stay in cash.")
    rs = get_db().kv_get("rotation_state", None)
    if not rs:
        st.info("The first list will be sent on Telegram after the next market close.")
    else:
        st.subheader(f"Latest list ({rs['month']})")
        if not rs.get("ok"):
            st.warning("Nifty is below its 200-day average price. My rule says: **stay in cash** this month.")
        else:
            hold = rs["holdings"]
            each = cfg["capital"] / max(len(hold), 1)
            st.dataframe(pd.DataFrame({"Stock": list(hold), "Price when picked": [f"₹{v:,.2f}" for v in hold.values()],
                                       "About this much each": [inr(each)] * len(hold)}), hide_index=True)
        h = rs.get("history", [])
        if len(h) >= 2:
            g = pd.DataFrame(h)
            g["Model list"] = (1 + g["port"]).cumprod() * 100
            g["Nifty"] = (1 + g["nifty"]).cumprod() * 100
            st.line_chart(g.set_index("month")[["Model list", "Nifty"]])
            st.caption("Starts at 100. Live practice record, after estimated costs, price only.")
        else:
            st.caption("The live record starts building after the second monthly list.")
    rr = rate_rotation((rs or {}).get("history", []))
    st.write(f"**Confidence:** {rr['emoji']} {rr['label']} - {rr['text']}.")
    with st.expander("My own history test (read the warning first)"):
        bt = get_db().kv_get("rotation_backtest", None)
        st.warning("These tests use today's surviving big companies, so they look MUCH better than reality would have. "
                   "Do not expect these numbers. NSE's published momentum index is the realistic reference: about 5% a year above Nifty 200.")
        if bt:
            for key, label in (("large_caps", "Large companies only"), ("broad", "Broader list")):
                r = (bt.get(key) or {}).get("with_market_filter")
                if r:
                    st.write(f"**{label}:** grew {r['cagr']:.0f}% a year vs Nifty {r['nifty_cagr']:.0f}%; "
                             f"worst fall {r['max_dd']:.0f}% vs {r['nifty_max_dd']:.0f}%; beat Nifty in {r['beat_pct']:.0f}% of months.")
    st.caption("Tax note: short-term gains on shares held under a year are taxed - check the current rate before selling.")

# ====================================================================== ADD / CLOSE A TRADE
elif page == NAV[3]:
    st.title("➕ Add or close a trade")
    st.write("Took a trade I didn't suggest? Add it here and I'll watch it and tell you when to move the stop-loss or leave.")
    with st.form("open_trade", clear_on_submit=True):
        sym = st.text_input("Stock or index name", placeholder="e.g. RELIANCE, NIFTY, BANKNIFTY, CRUDE")
        side = st.radio("What did you do?", ["I bought", "I sold first (same-day only)"], horizontal=True)
        kind = st.selectbox("How long will you hold it?", ["Same day", "A few days or weeks", "Options (same day)"])
        c1, c2, c3 = st.columns(3)
        entry = c1.number_input("Price", min_value=0.0, format="%.2f")
        sl = c2.number_input("Stop-loss price", min_value=0.0, format="%.2f")
        qty = c3.number_input("Quantity (optional)", min_value=0.0, format="%.0f")
        track = st.checkbox("Watch it for me (works for stocks, Nifty, Bank Nifty and crude)", value=True)
        if st.form_submit_button("Save"):
            long = side == "I bought"
            if not sym or entry <= 0 or sl <= 0:
                st.error("Please fill in the name, price and stop-loss.")
            elif (long and sl >= entry) or ((not long) and sl <= entry):
                st.error("The stop-loss must be below your buy price (above it, if you sold first).")
            else:
                ysym = normalize_symbol(sym)
                inst = inst_for(ysym)
                mode = {"Same day": "intraday", "A few days or weeks": "swing", "Options (same day)": "option"}[kind]
                risk, d = abs(entry - sl), (1 if long else -1)
                row = dict(source="user", strategy="MANUAL", symbol=ysym, name=display_name(ysym), mode=mode,
                           timeframe="1d" if mode == "swing" else "15m", side="LONG" if long else "SHORT", entry=entry, initial_sl=sl,
                           sl=sl, t1=entry + d * 1.5 * risk, t2=entry + d * 3 * risk, qty=qty or None, status="OPEN", t1_hit=False,
                           best=entry, bars_held=0, taken=True, tracked=track,
                           meta=dict(session=inst["session"], open_date=str(now_ist.date()), via="dashboard"))
                r = get_db().insert("trades", row)
                st.cache_data.clear()
                st.success(f"Saved. I'm watching {display_name(ysym)} for you (trade #{r['id']}).")
    st.subheader("Close a trade")
    mine = trades[(trades["status"] == "OPEN") & (trades["source"] == "user")] if not trades.empty else trades
    if mine.empty:
        st.caption("You have no open trades.")
    else:
        for _, t in mine.iterrows():
            trade_card(t.to_dict())

# ====================================================================== SETTINGS
elif page == NAV[4]:
    st.title("⚙️ Settings")
    st.write("Change these any time. They take effect at my next check (within 15 minutes).")
    with st.form("settings"):
        capital = st.number_input("Money you trade with (₹)", min_value=0.0, value=float(cfg["capital"]), step=10000.0, format="%.0f")
        risk_pct = st.number_input("Most you're willing to lose on one trade (% of that money)", min_value=0.1, max_value=5.0,
                                   value=float(cfg["risk_pct"]), step=0.1, format="%.1f")
        st.caption(f"That means a stop-loss hit costs you about {inr(capital * risk_pct / 100)}. 1% is a common, careful choice.")
        st.markdown("**Send me ideas for:**")
        s_intra = st.checkbox("Same-day stock trades", value=cfg["send_intraday"])
        s_opt = st.checkbox("Options (Nifty, Bank Nifty, crude)", value=cfg["send_option"])
        s_swing = st.checkbox("Few-days-to-weeks trades", value=cfg["send_swing"])
        s_mom = st.checkbox("The monthly stock list", value=cfg["send_momentum"])
        only_good = st.checkbox("Only send ideas from approaches that look good (🟢 or 🟡) - recommended once you use real money",
                                value=cfg["only_good"])
        paused = st.checkbox("⏸ Pause all NEW ideas (I'll still warn you about trades you're in)", value=cfg["paused"])
        if st.form_submit_button("Save settings"):
            get_db().kv_set("settings", dict(capital=capital, risk_pct=risk_pct, send_intraday=s_intra, send_option=s_opt,
                                             send_swing=s_swing, send_momentum=s_mom, only_good=only_good, paused=paused))
            st.cache_data.clear()
            st.success("Saved.")

# ====================================================================== DETAILS
else:
    st.title("🔍 Details")
    st.caption("You never need this page. It's here if someone is helping you check things.")
    t1, t2, t3, t4 = st.tabs(["All ideas", "News", "History tests", "System log"])
    with t1:
        sg = load("signals", 500)
        if sg.empty:
            st.caption("No ideas yet.")
        else:
            sg["created_at"] = to_ist(sg["created_at"])
            sg["What happened"] = sg["decision"].map(lambda d: DECISION_TEXT.get(d, d))
            st.dataframe(sg[["created_at", "name", "side", "strategy", "What happened", "reason"]], hide_index=True)
    with t2:
        n = load("news", 300)
        if n.empty:
            st.caption("No news yet.")
        else:
            n["published"] = to_ist(n["published"])
            n["symbols"] = n["symbols"].apply(lambda x: ", ".join(x) if isinstance(x, list) else "")
            q = st.text_input("Search")
            if q:
                n = n[n.apply(lambda r: q.lower() in f"{r['title']} {r['symbols']}".lower(), axis=1)]
            st.dataframe(n[["published", "sentiment", "symbols", "title"]], hide_index=True)
    with t3:
        b = load("backtests", 200)
        if b.empty:
            st.caption("No history tests yet.")
        else:
            b["run_at"] = to_ist(b["run_at"])
            latest = b.sort_values("run_at").groupby(["strategy", "mode"]).tail(1)
            st.dataframe(latest[["strategy", "mode", "trades", "win_rate", "payoff", "avg_r", "t_stat", "run_at", "note"]], hide_index=True)
    with t4:
        r = load("runs", 300)
        if r.empty:
            st.caption("No runs yet.")
        else:
            r["started_at"] = to_ist(r["started_at"])
            st.dataframe(r[["started_at", "ok", "detail", "data_lag_min", "n_signals", "n_alerts", "seconds"]], hide_index=True)
