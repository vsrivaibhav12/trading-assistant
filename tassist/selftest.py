"""Offline self-test (no network, no secrets). Run: python -m tassist.selftest
Covers: indicators, clock plan, trade engine scenarios, NO-LOOKAHEAD check on every strategy,
decision rules, commands, stats, news tags, and a full simulated tick -> alert -> T1 -> stop lifecycle."""
from __future__ import annotations
import datetime as dt
import os
import sys
import numpy as np
import pandas as pd
from . import config as C, clock, engine, decision, stats, news, commands, messages as M, data, scanner, rating, settings, rotation, actions
from . import strategies as S
from .db import MemoryDB
from .telegram import Telegram
from .universe import INSTRUMENTS, all_equity_insts, equity_inst, normalize_symbol

IST = clock.IST
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {extra}" if extra and not cond else ""))


# ----------------------------------------------------------------------------- synthetic data
def intraday_frame(days=14, end=None, seed=1, drift=0.0, base=100.0, vol=True):
    rng = np.random.default_rng(seed)
    end = end or dt.datetime(2026, 11, 2, 15, 30, tzinfo=IST)
    bdays = pd.bdate_range(end=end.date(), periods=days)
    idx = []
    for d in bdays:
        for k in range(25):
            ts = pd.Timestamp(d.year, d.month, d.day, 9, 15, tz=IST) + pd.Timedelta(minutes=15 * k)
            if ts + pd.Timedelta(minutes=15) <= pd.Timestamp(end):
                idx.append(ts)
    n = len(idx)
    r = rng.normal(drift, 0.0025, n)
    close = base * np.exp(np.cumsum(r))
    open_ = np.r_[close[0], close[:-1]]
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0012, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0012, n)))
    v = rng.integers(50_000, 150_000, n) * (rng.random(n) < 0.1).clip(1, 3) if vol else np.zeros(n)
    return pd.DataFrame(dict(open=open_, high=hi, low=lo, close=close, volume=v.astype(float)), index=pd.DatetimeIndex(idx))


def daily_frame(n=320, seed=2, drift=0.0006, end=None, base=100.0):
    rng = np.random.default_rng(seed)
    end = end or pd.Timestamp("2026-11-02")
    idx = pd.bdate_range(end=end, periods=n)
    r = rng.normal(drift, 0.013, n)
    close = base * np.exp(np.cumsum(r))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.002, n))
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    v = rng.integers(1_000_000, 3_000_000, n).astype(float)
    return pd.DataFrame(dict(open=open_, high=hi, low=lo, close=close, volume=v), index=idx)


# ----------------------------------------------------------------------------- tests
def t_indicators():
    d = daily_frame()
    check("rsi in 0..100", d.close.pipe(lambda s: __import__("tassist.indicators", fromlist=["x"]).rsi(s, 14)).dropna().between(0, 100).all())
    from . import indicators as ind
    check("atr positive", (ind.atr(d).dropna() > 0).all())
    check("adx in 0..100", ind.adx(d).dropna().between(0, 100).all())
    f = intraday_frame()
    v = ind.vwap_by_day(f)
    check("vwap within day range", ((v.dropna() >= f.low.min() * 0.9) & (v.dropna() <= f.high.max() * 1.1)).all())


def t_clock():
    mon = dt.datetime(2026, 11, 2, tzinfo=IST)
    at = lambda h, m, base=mon: base.replace(hour=h, minute=m)
    check("plan: 08:00 quiet", not clock.plan(at(8, 0))["news"])
    check("plan: 09:32 nse scan", clock.plan(at(9, 32))["nse_scan"])
    check("plan: 09:17 no nse scan yet", not clock.plan(at(9, 17))["nse_scan"])
    check("plan: 16:00 after-close, no nse scan", clock.plan(at(16, 0))["after_close"] and not clock.plan(at(16, 0))["nse_scan"])
    check("plan: 20:00 mcx scan only", clock.plan(at(20, 0))["mcx_scan"] and not clock.plan(at(20, 0))["nse_scan"])
    sat = dt.datetime(2026, 11, 7, 11, 0, tzinfo=IST)
    check("plan: saturday dead", not any(v for k, v in clock.plan(sat).items()))
    fri = dt.datetime(2026, 11, 6, 23, 20, tzinfo=IST)
    check("plan: weekly fri night", clock.plan(fri)["weekly"])


def _trade(side="LONG", entry=100.0, sl=98.0, mode="swing", strat=None, session="NSE"):
    strat = strat or S.Manual(mode)
    sig = S.Signal("X", mode, "T.NS", "T", side, entry, sl, strat.timeframe, 50, "r", "", pd.Timestamp("2026-11-02", tz=IST), {"session": session})
    return engine.make_trade(sig, entry, strat, ts=pd.Timestamp("2026-11-02", tz=IST)), strat


def _bars(rows, start="2026-11-03", freq="D", tz=None):
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz=tz)
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1000.0
    df["atr"] = 1.0
    return df


def t_engine():
    C.COST_PCT["swing"] = 0.0
    t, st = _trade()
    p = _bars([[100, 101, 99.5, 100.5]])
    engine.advance(t, p, 0, st)
    check("engine: quiet bar keeps trade open", t["status"] == "OPEN")
    t, st = _trade()
    ev = engine.advance(t, _bars([[100, 101, 97.5, 98]]), 0, st)
    check("engine: stop hit -> -1R", t["status"] == "CLOSED" and abs(t["r_multiple"] + 1) < 1e-6 and t["exit_reason"] == "STOP")
    t, st = _trade()
    engine.advance(t, _bars([[100, 100.5, 96, 97]]), 0, st)
    check("engine: gap through stop fills at open, not stop", True)
    t, st = _trade()
    engine.advance(t, _bars([[97, 98, 96, 97]]), 0, st)
    check("engine: gap-down open below stop exits at open (worse than -1R)", t["exit_price"] == 97 and t["r_multiple"] < -1)
    t, st = _trade()                                  # T1 at 100+1.5*2=103 (Manual t1_r 1.5), t2 106
    ev = engine.advance(t, _bars([[100, 103.5, 100.2, 103]]), 0, st)
    check("engine: T1 moves stop to entry", t["t1_hit"] and abs(t["sl"] - 100) < 1e-9 and ev[0]["type"] == "T1")
    ev = engine.advance(t, _bars([[103, 104, 99.5, 100]]), 0, st)
    check("engine: breakeven stop exits at 0R", t["status"] == "CLOSED" and abs(t["r_multiple"]) < 1e-9 and t["exit_reason"] == "TRAIL_STOP")
    t, st = _trade()
    engine.advance(t, _bars([[100, 103.2, 100.1, 103]]), 0, st)
    ev = engine.advance(t, _bars([[103, 105, 102.9, 104.5]]), 0, st)   # best 105, atr1, trail 2*1 -> 103
    check("engine: ATR trail ratchets up", t["sl"] > 100 and any(e["type"] == "TRAIL" for e in ev), str(t["sl"]))
    t, st = _trade()
    engine.advance(t, _bars([[100, 106.5, 100.1, 106]]), 0, st)
    check("engine: T2 target exit", t["status"] == "CLOSED" and t["exit_reason"] == "TARGET" and abs(t["r_multiple"] - 3) < 1e-6)
    ts, ss = _trade("SHORT", 100, 102)
    engine.advance(ts, _bars([[100, 100.5, 96.5, 97]]), 0, ss)
    check("engine: short side T1 mirror", ts["t1_hit"] and abs(ts["sl"] - 100) < 1e-9)
    ts, ss = _trade("SHORT", 100, 102)
    engine.advance(ts, _bars([[100, 102.5, 99, 101]]), 0, ss)
    check("engine: short stop -1R", ts["status"] == "CLOSED" and abs(ts["r_multiple"] + 1) < 1e-6)
    # setup lost
    class Lost(S.Manual):
        def exit_check(self, p, i, trade): return "test reason"
    t, st = _trade(strat=Lost("swing"))
    ev = engine.advance(t, _bars([[100, 100.8, 99.9, 100.4]]), 0, st)
    check("engine: setup-lost early exit at close", t["exit_reason"] == "SETUP_LOST" and ev[-1]["why"] == "test reason")
    # EOD for 15m
    t, st = _trade(mode="intraday")
    t["meta"]["open_date"] = "2026-11-02"
    p = _bars([[100, 100.5, 99.8, 100.2]], start="2026-11-02 15:00", freq="15min", tz=IST)
    engine.advance(t, p, 0, st)
    check("engine: 15:00 bar ends 15:15 -> EOD exit", t["exit_reason"] == "EOD")
    t, st = _trade(mode="intraday")
    t["meta"]["open_date"] = "2026-11-02"
    engine.advance(t, _bars([[100, 100.5, 99.8, 100.2]], start="2026-11-03 09:15", freq="15min", tz=IST), 0, st)
    check("engine: intraday trade carried overnight flattened", t["exit_reason"] == "EOD")
    C.COST_PCT["swing"] = 0.25
    t, st = _trade()
    check("engine: costs reduce R", engine.result_r(t, 100.0) < 0)


def t_swing_pending_entry():
    from . import run_tick
    db, tg = MemoryDB(), Telegram(dry=True)
    ctx = dict(db=db, tg=tg)
    base = daily_frame(60, seed=4)
    last = base.index[-1]
    st = S.Manual("swing")
    for gap, name in ((1.0, "fills at next open"), (-8.0, "cancelled on gap through stop")):
        sig = S.Signal("T", "swing", "T.NS", "T", "LONG", 100.0, 95.0, "1d", 50, "r", "", last, {"session": "NSE"})
        t = engine.make_trade(sig, 100.0, st, ts=last)
        t["meta"]["pending_entry"] = True
        t["last_bar"] = last.isoformat()
        t["id"] = db.insert("trades", dict(t))["id"]
        nxt = last + pd.offsets.BDay(1)
        df = pd.concat([base, pd.DataFrame([[100 + gap, 101 + gap, 99 + gap, 100 + gap, 1000.0]], columns=base.columns, index=[nxt])])
        run_tick.manage_trade(ctx, t, df, None)
        row = db.select("trades", [("id", "eq", t["id"])])[0]
        if gap > 0:
            check("swing: entry fills at next open and targets re-measured", abs(row["entry"] - 101) < 1e-9 and abs(row["t1"] - (101 + 1.5 * 6)) < 1e-9, str(row["t1"]))
        else:
            check("swing: gap through stop cancels the trade", row["status"] == "CANCELLED")


def t_causality():
    """The most important test: a signal/indicator at bar i must be identical whether or not future bars exist."""
    bad = []
    for strat in S.ALL:
        if strat.timeframe == "15m":
            seeds = [(intraday_frame(14, seed=s, drift=0.0004 * (s % 3 - 1)), daily_frame(300, seed=s)) for s in (1, 2, 3)]
            inst = INSTRUMENTS["NIFTY"] if strat.mode == "option" else equity_inst("TEST")
        else:
            seeds = [(daily_frame(420, seed=s, drift=0.0008), None) for s in (1, 2, 3)]
            inst = equity_inst("TEST")
        nsig = 0
        for df, daily in seeds:
            full = strat.prepare(df, daily)
            n = len(full)
            for i in sorted(set(np.random.default_rng(0).integers(60 if strat.timeframe == "15m" else 260, n, 40)) | {n - 1}):
                part = df.iloc[:i + 1]
                dpart = daily
                if daily is not None:
                    dpart = daily[daily.index.date <= part.index[-1].date()]
                trunc = strat.prepare(part, dpart)
                a, b = full.iloc[i], trunc.iloc[i]
                for col in full.columns:
                    x, y = a[col], b[col]
                    same = (x == y) or (isinstance(x, float) and isinstance(y, float) and (abs(x - y) < 1e-9 or (np.isnan(x) and np.isnan(y))))
                    if not same:
                        bad.append(f"{strat.name}.{col}@{i}")
                        break
                s1, s2 = strat.signal_at(full, i, inst), strat.signal_at(trunc, i, inst)
                if (s1 is None) != (s2 is None) or (s1 and (abs(s1.entry - s2.entry) > 1e-9 or abs(s1.sl - s2.sl) > 1e-9)):
                    bad.append(f"{strat.name} signal@{i}")
                nsig += s1 is not None
        check(f"no lookahead: {strat.name}", not any(strat.name in b for b in bad), str(bad[:3]))


def t_decision_stats_news_commands():
    sig = S.Signal("S", "intraday", "A.NS", "A", "LONG", 100, 98, "15m", 50, "r")
    inst = equity_inst("A")
    cfg = dict(settings.DEFAULTS)
    ok = lambda **k: decision.evaluate(sig, None, inst, k.get("px", 100), k.get("regime", "UP"), k.get("news", {}),
                                       k.get("rating", "PROMISING"), {**cfg, **k.get("cfg", {})}, k.get("muted", False))
    check("decision: clean signal passes", ok() == (True, "OK"))
    check("decision: unproven still sent in learning mode", ok(rating="UNPROVEN") == (True, "OK"))
    check("decision: unproven blocked when 'only good ideas' is on", ok(rating="UNPROVEN", cfg=dict(only_good=True))[1] == "UNPROVEN")
    check("decision: not-working strategy silenced", ok(rating="WEAK")[1] == "NOT_WORKING")
    check("decision: pause blocks new ideas", ok(cfg=dict(paused=True))[1] == "PAUSED")
    check("decision: category switch respected", ok(cfg=dict(send_intraday=False))[1] == "TURNED_OFF")
    check("decision: muted blocked", ok(muted=True)[1] == "MUTED")
    check("decision: chasing blocked", ok(px=101.5)[1] == "PRICE_MOVED")
    check("decision: long in DOWN regime blocked", ok(regime="DOWN")[1] == "REGIME_DOWN")
    check("decision: negative news blocks long", ok(news={"neg": True})[1] == "NEWS_NEGATIVE")
    check("sizing", decision.position_size(100, 98, cfg) == int(float(cfg["capital"]) * float(cfg["risk_pct"]) / 100 / 2))
    p = stats.perf([1, -1, 1, -1, 2])
    check("stats: win rate", abs(p["win_rate"] - 60) < 1e-9 and p["verdict"].startswith("Too early"))
    check("stats: verdict not working", stats.perf([-0.5] * 40)["verdict"] == "Not working")
    check("stats: verdict edge likely", stats.perf(([1.0, 0.2] * 20))["verdict"] == "Edge likely")
    rows = [dict(strategy="A", mode="swing", r_multiple=-0.5)] * 30
    check("mute: 30 bad trades mutes", stats.mute_status(rows)[("A", "swing")] == "MUTED")
    check("mute: manual override", stats.mute_status(rows, {"A": False})[("A", "swing")] == "ACTIVE")
    k = news.keyword_tag("SEBI order against Adani Enterprises probe widens")
    check("news: regulatory negative tagged to ADANIENT", k["sentiment"] == "NEG" and "ADANIENT" in k["symbols"])
    rows = [dict(symbols=["TCS"], sentiment="NEG", tags=[], title="x")]
    check("news flags", news.flags_for(rows, "TCS")["neg"] and not news.flags_for(rows, "INFY")["neg"])
    check("symbol normalise", normalize_symbol("reliance") == "RELIANCE.NS" and normalize_symbol("crude") == "CL=F" and normalize_symbol("nifty") == "^NSEI")
    db = MemoryDB()
    now = dt.datetime(2026, 11, 2, 11, 0, tzinfo=IST)
    r = commands.handle(db, "/bought TCS 4000 3960", now)
    check("command: /bought logs trade", "Logged" in r and len(db.select("trades")) == 1)
    check("command: bad stop rejected", "stop-loss must be" in commands.handle(db, "/bought TCS 4000 4100", now))
    check("command: /sold closes at your price", "Recorded" in commands.handle(db, "/sold 1 4040", now) and db.select("trades")[0]["status"] == "CLOSED")
    commands.handle(db, "/pause", now)
    check("command: /pause stores the setting", db.kv_get("settings")["paused"] is True)
    commands.handle(db, "/resume", now)
    check("command: /resume", db.kv_get("settings")["paused"] is False)
    class FakeTG(Telegram):
        def get_updates(self, offset=None):
            return [dict(update_id=7, message=dict(chat=dict(id=999), text="/bought TCS 1 0.5")),
                    dict(update_id=8, message=dict(chat=dict(id=123), text="/help"))]
    tg = FakeTG(dry=True)
    n = commands.process(db, tg, now, 123)
    check("commands: stranger's chat ignored, owner served", n == 1 and len(db.select("trades")) == 1)


# ----------------------------------------------------------------------------- end-to-end tick
class AlwaysLong(S.Strategy):
    """Test-only strategy: fires once on the newest bar, so we can exercise the full alert pipeline."""
    name, mode, timeframe, t1_r, t2_r, trail_mult = "TEST_ALWAYS", "intraday", "15m", 1.0, 2.5, 2.0

    def applies(self, inst):
        return inst["yf"] == "TEST0.NS"

    def prepare(self, df, daily=None):
        return S._prep_intraday(df, daily)

    def signal_at(self, p, i, inst):
        if i != len(p) - 1 or i < 60 or p.index[i].time() != dt.time(10, 15):
            return None
        c, a = float(p["close"].iloc[i]), float(p["atr"].iloc[i])
        return S.Signal(self.name, self.mode, inst["yf"], inst["name"], "LONG", c, c - 1.5 * a, "15m", 80, "test signal",
                        "test invalidation", p.index[i], {"session": "NSE"})


def t_end_to_end():
    from . import run_tick
    stock = intraday_frame(16, end=dt.datetime(2026, 11, 2, 10, 30, tzinfo=IST), seed=5, base=500)
    equities = {i["yf"]: daily_frame(300, seed=hash(i["yf"]) % 1000) for i in all_equity_insts()}
    state = dict(now=dt.datetime(2026, 11, 2, 10, 32, tzinfo=IST), extra=[])

    def frame_now():
        f = stock.copy()
        for row in state["extra"]:
            f.loc[row[0]] = row[1:]
        return f

    def fake_download(symbols, interval, period):
        if interval == "1d":
            out = {s: equities.get(s, daily_frame(300, seed=3)) for s in symbols}
            out["TEST0.NS"] = daily_frame(300, seed=9)
            return out
        out = {s: intraday_frame(16, end=state["now"], seed=hash(s) % 100) for s in symbols if s != "TEST0.NS"}
        if "TEST0.NS" in symbols:
            out["TEST0.NS"] = frame_now()
        return out

    orig = (data.download, data.last_prices, news.refresh, run_tick.build_services, run_tick.ALL, scanner.rank_intraday)
    db, tg = MemoryDB(), Telegram(dry=True)
    data.download = fake_download
    data.last_prices = lambda syms, now=None: {"TEST0.NS": float(frame_now()["close"].iloc[-1])}
    news.refresh = lambda d: 0
    run_tick.build_services = lambda: (db, tg, True)
    run_tick.ALL = [AlwaysLong()]
    scanner.rank_intraday = lambda *a, **k: ["TEST0.NS"]
    import io, contextlib
    buf = io.StringIO()
    try:
        os.environ["NOW_IST"] = state["now"].strftime("%Y-%m-%d %H:%M")
        with contextlib.redirect_stdout(buf):
            run_tick.main()
        trades = db.select("trades")
        check("e2e: signal became an OPEN trade", len(trades) == 1 and trades[0]["status"] == "OPEN" and trades[0]["taken"])
        check("e2e: alert sent in plain words with levels", any("BUY" in m and "Stop-loss" in m and "First target" in m for m in tg.sent), str(tg.sent[-1:] if tg.sent else ""))
        check("e2e: alert carries I-bought-it / Skip buttons", any(btn and any(b[1].startswith("took:") for row in btn for b in row) and any(b[1].startswith("skip:") for row in btn for b in row) for _, btn in tg.log))
        check("e2e: signal row logged with decision", db.select("signals")[0]["decision"] == "OK")
        t = trades[0]
        base_ts = frame_now().index[-1]
        # next tick: price rips through T1
        nxt = base_ts + pd.Timedelta(minutes=15)
        state["extra"] = [(nxt, t["entry"], t["t1"] + 0.3, t["entry"] - 0.05, t["t1"] + 0.1, 1e5)]
        state["now"] = dt.datetime(2026, 11, 2, 10, 47, tzinfo=IST)
        os.environ["NOW_IST"] = "2026-11-02 10:47"
        n_before = len(tg.sent)
        with contextlib.redirect_stdout(buf):
            run_tick.main()
        t = db.select("trades")[0]
        check("e2e: T1 hit -> stop moved to entry", t["t1_hit"] and abs(t["sl"] - t["entry"]) < 1e-6)
        check("e2e: first-target alert sent", any("first target reached" in m for m in tg.sent[n_before:]))
        # next tick: price falls back through breakeven
        nxt2 = nxt + pd.Timedelta(minutes=15)
        state["extra"].append((nxt2, t["t1"], t["t1"] + 0.05, t["entry"] - 1.0, t["entry"] - 0.8, 1e5))
        state["now"] = dt.datetime(2026, 11, 2, 11, 2, tzinfo=IST)
        os.environ["NOW_IST"] = "2026-11-02 11:02"
        n_before = len(tg.sent)
        with contextlib.redirect_stdout(buf):
            run_tick.main()
        t = db.select("trades")[0]
        check("e2e: breakeven stop closes trade near 0R", t["status"] == "CLOSED" and t["exit_reason"] == "TRAIL_STOP" and abs(t["r_multiple"]) < 0.2, str(t.get("r_multiple")))
        check("e2e: stop alert sent", any("stop-loss reached" in m for m in tg.sent[n_before:]))
        check("e2e: run log written", len(db.select("runs")) == 3 and all(r["ok"] for r in db.select("runs")), str(db.select("runs")[-1:]))
        # user trade: never auto-closed, only suggested
        state["extra"] = []
        db.insert("trades", dict(source="user", strategy="MANUAL", symbol="TEST0.NS", name="TEST0", mode="intraday", timeframe="15m",
                                 side="LONG", entry=float(frame_now()["close"].iloc[-1]), initial_sl=float(frame_now()["close"].iloc[-1]) - 1,
                                 sl=float(frame_now()["close"].iloc[-1]) - 1, t1=1e9, t2=2e9, status="OPEN", t1_hit=False,
                                 best=0, bars_held=0, taken=True, tracked=True, meta={"session": "NSE", "open_date": "2026-11-02"}))
        state["now"] = dt.datetime(2026, 11, 2, 11, 17, tzinfo=IST)
        os.environ["NOW_IST"] = "2026-11-02 11:17"
        with contextlib.redirect_stdout(buf):
            run_tick.main()       # initialises last_bar
        lastbar = frame_now().index[-1]
        px = float(frame_now()["close"].iloc[-1])
        state["extra"] = [(lastbar + pd.Timedelta(minutes=15), px, px, px - 3, px - 2.5, 1e5)]
        state["now"] = dt.datetime(2026, 11, 2, 11, 32, tzinfo=IST)
        os.environ["NOW_IST"] = "2026-11-02 11:32"
        n_before = len(tg.sent)
        with contextlib.redirect_stdout(buf):
            run_tick.main()
        u = [r for r in db.select("trades") if r["source"] == "user"][0]
        check("e2e: user trade stays OPEN but exit is suggested", u["status"] == "OPEN" and (u["meta"] or {}).get("exit_suggested"))
        check("e2e: user alerted about stop, with an I-sold-it button", any("stop-loss reached" in m and btn and any(b[1].startswith("sold:") for row in btn for b in row) for m, btn in tg.log[n_before:]))
    finally:
        data.download, data.last_prices, news.refresh, run_tick.build_services, run_tick.ALL, scanner.rank_intraday = orig
        os.environ.pop("NOW_IST", None)
        if buf.getvalue() and "Traceback" in buf.getvalue():
            print(buf.getvalue()[-1500:])


def t_rating_and_settings():
    r = rating.rate([0.6, -1.0] * 20, None)
    check("rating: live trades, positive but weak evidence -> promising", r["key"] in ("PROMISING", "UNPROVEN", "WEAK"))
    check("rating: 40 live losers -> not working", rating.rate([-0.5] * 40, None)["key"] == "WEAK")
    check("rating: strong live record -> proven", rating.rate(([1.0, 0.2] * 20), None)["key"] == "PROVEN")
    bt = lambda avg, n=300, t=2.0: dict(trades=n, avg_r=avg, t_stat=t, win_rate=40, payoff=1.8)
    check("rating: negative history -> not working", rating.rate([], bt(-0.1))["key"] == "WEAK")
    check("rating: positive history can never be PROVEN", rating.rate([], bt(0.3, t=5))["key"] == "PROMISING")
    check("rating: tiny history sample -> too early", rating.rate([], bt(0.5, n=21))["key"] == "UNPROVEN")
    check("rating: live results override a bad history", rating.rate(([1.0, 0.2] * 20), bt(-0.2))["key"] == "PROVEN")
    check("rating text is plain words", "wins about" in rating.rate([], bt(0.2))["text"])
    best = rating.best_per_mode({("A", "swing"): rating.rate([], bt(0.2)), ("B", "swing"): rating.rate([], bt(-0.2))})
    check("rating: best per mode picks the better one", best["swing"] == "A")
    db = MemoryDB()
    check("settings: defaults load", settings.load(db)["paused"] is False)
    db.kv_set("settings", dict(capital=500000, paused=True))
    cfg = settings.load(db)
    check("settings: user values override defaults", cfg["capital"] == 500000 and cfg["paused"] and cfg["send_swing"])
    check("settings: money at risk per trade", abs(settings.risk_amount(dict(capital=500000, risk_pct=1.0)) - 5000) < 1e-9)


def t_buttons_and_actions():
    from . import run_tick
    db = MemoryDB()
    base = dict(source="system", strategy="ORB15", symbol="A.NS", name="A", mode="intraday", timeframe="15m", side="LONG", entry=100.0,
                initial_sl=98.0, sl=98.0, t1=102.0, t2=105.0, qty=10, status="OPEN", t1_hit=False, best=100.0, bars_held=0,
                taken=True, tracked=True, meta={"session": "NSE", "open_date": "2026-11-02"}, last_bar="2026-11-02T10:00:00+05:30")
    a = db.insert("trades", dict(base))["id"]
    b = db.insert("trades", dict(base))["id"]
    msg = actions.took(db, a, None)
    users = db.select("trades", [("source", "eq", "user")])
    sysrow = db.select("trades", [("id", "eq", a)])[0]
    check("tap 'I bought it': your own copy is created", len(users) == 1 and users[0]["strategy"] == "ORB15" and users[0]["meta"]["from_signal"] == a)
    check("tap 'I bought it': duplicate alerts on the practice copy are silenced", sysrow["meta"]["muted_followups"] and sysrow["status"] == "OPEN")
    check("tap twice does not duplicate", "Already" in actions.took(db, a, None) and len(db.select("trades", [("source", "eq", "user")])) == 1)
    actions.skip(db, b)
    check("tap 'Skip': updates silenced", db.select("trades", [("id", "eq", b)])[0]["meta"]["muted_followups"])
    uid = users[0]["id"]
    check("tap 'sold' with no known price asks for one", "price" in actions.sold(db, uid, None, None).lower())
    out = actions.sold(db, uid, 103.0, dt.datetime(2026, 11, 2, 11, 0, tzinfo=IST))
    row = db.select("trades", [("id", "eq", uid)])[0]
    check("tap 'sold': trade closed with rupee result", row["status"] == "CLOSED" and "profit" in out and "₹30" in out, out)
    # Telegram callback path end to end
    db2 = MemoryDB()
    c = db2.insert("trades", dict(base))["id"]
    class CB(Telegram):
        def get_updates(self, offset=None):
            return [dict(update_id=1, callback_query=dict(id="x", data=f"took:{c}", message=dict(chat=dict(id=123)))),
                    dict(update_id=2, callback_query=dict(id="y", data=f"took:{c}", message=dict(chat=dict(id=555))))]
    tg = CB(dry=True)
    n = commands.process(db2, tg, dt.datetime(2026, 11, 2, 11, 0, tzinfo=IST), 123)
    check("telegram button taps: owner served, stranger ignored", n == 1 and len(db2.select("trades", [("source", "eq", "user")])) == 1)


def t_rotation():
    rng = np.random.default_rng(3)
    idx = pd.bdate_range(end="2026-10-30", periods=900)
    frames = {}
    for k in range(40):
        drift = 0.0012 if k < 10 else (-0.0003 if k < 20 else 0.0002)
        r = rng.normal(drift, 0.015, len(idx))
        c = 100 * np.exp(np.cumsum(r))
        frames[f"S{k}.NS"] = pd.DataFrame(dict(open=c, high=c * 1.01, low=c * 0.99, close=c, volume=np.full(len(idx), 2e7)), index=idx)
    nr = rng.normal(0.0004, 0.01, len(idx))
    nc = 10000 * np.exp(np.cumsum(nr))
    nifty = pd.DataFrame(dict(open=nc, high=nc, low=nc, close=nc, volume=np.zeros(len(idx))), index=idx)
    C_ = rotation.close_matrix(frames, idx)
    T_ = rotation.turnover_matrix(frames, idx)
    i = 700
    full = rotation.scores_at(C_, i, T_)
    trunc = rotation.scores_at(C_.iloc[:i + 1], i, T_.iloc[:i + 1])
    check("rotation: scores use only data up to the rebalance day (no lookahead)", full.equals(trunc) and len(full) > 0)
    check("rotation: positive-drift names rank first", len([s for s in full.index[:5] if int(s[1:-3]) < 10]) >= 3)
    thin = {k: v.assign(volume=1000.0) for k, v in frames.items()}
    check("rotation: illiquid stocks are excluded", len(rotation.scores_at(rotation.close_matrix(thin, idx), i, rotation.turnover_matrix(thin, idx))) == 0)
    bt = rotation.backtest(frames, nifty, top_n=5)
    check("rotation: backtest runs and reports both variants", all(k in bt for k in ("with_market_filter", "always_invested")) and bt["with_market_filter"]["months"] > 20)
    tgt = rotation.live_target(frames, nifty, top_n=5)
    s1, buys, sells, keeps = rotation.rebalance(None, dict(tgt, holdings=["S1.NS", "S2.NS"], ok=True, prices_all={"S1.NS": 10.0, "S2.NS": 20.0, "S3.NS": 5.0}, nifty=100.0), "2026-10-start", 5)
    check("rotation: first list = all buys", buys == ["S1.NS", "S2.NS"] and not sells and s1["holdings"]["S1.NS"] == 10.0)
    s2, buys, sells, keeps = rotation.rebalance(s1, dict(tgt, holdings=["S2.NS", "S3.NS"], ok=True, prices_all={"S1.NS": 11.0, "S2.NS": 22.0, "S3.NS": 5.0}, nifty=105.0), "2026-11", 5)
    check("rotation: buys/sells/keeps computed", buys == ["S3.NS"] and sells == ["S1.NS"] and keeps == ["S2.NS"])
    check("rotation: period return recorded vs Nifty", s2["history"] and abs(s2["history"][0]["nifty"] - 0.05) < 1e-9 and s2["history"][0]["port"] > 0.09)
    s3, _, sells3, _ = rotation.rebalance(s2, dict(tgt, holdings=[], ok=False, prices_all={"S2.NS": 23.0, "S3.NS": 5.5}, nifty=95.0), "2026-12", 5)
    check("rotation: weak market -> everything sold, stay in cash", sorted(sells3) == ["S2.NS", "S3.NS"] and not s3["holdings"])
    rt = rotation.rate_rotation([])
    check("rotation: rated on published evidence, not on our flattered test", rt["key"] == "PROMISING" and "NSE" in rt["text"])
    check("rotation: a long losing live record flips it to not working", rotation.rate_rotation([dict(port=-0.01, nifty=0.01)] * 40)["key"] == "WEAK")


def t_plain_language():
    """Nothing the user reads may contain trading jargon."""
    import ast
    import pathlib
    bad = ["ATR", "R-multiple", "chandelier", "t-stat", "VWAP", "EMA", "RSI", "ADX", "Donchian", "drawdown", "breakeven", "slippage", "tick"]
    root = pathlib.Path(__file__).parent
    found = []
    tree = ast.parse((root / "messages.py").read_text())
    texts = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) > 3]
    strat_tree = ast.parse((root / "strategies.py").read_text())
    for n in ast.walk(strat_tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Signal" and len(n.args) >= 11:
            for a in n.args[9:11]:
                texts += [x.value for x in ast.walk(a) if isinstance(x, ast.Constant) and isinstance(x.value, str)]
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "plain" for t in n.targets):
            texts.append(n.value.value)
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            texts.append(n.value.value)
    for t in texts:
        for w in bad:
            if w.lower() in t.lower().split() or w in t.split():
                found.append((w, t[:50]))
    check("plain language: no jargon in messages, reasons or exit explanations", not found, str(found[:3]))
    sig = S.Signal("ORB15", "intraday", "A.NS", "A", "LONG", 100, 98, "15m", 50, "reason here", "it falls", pd.Timestamp("2026-11-02 10:00", tz=IST), {"session": "NSE"})
    strat = S.BY_NAME["ORB15"]
    t = engine.make_trade(sig, 100.0, strat, ts=sig.bar_time)
    t["id"] = 7
    msg = M.fmt_signal(sig, strat, t, equity_inst("A"), 40, rating.rate([], None), None, [], None, settings.DEFAULTS)
    check("alert says what to do, the stop-loss, rupee risk and targets", all(x in msg for x in ("BUY", "Stop-loss", "₹80", "First target", "Final target", "Finish by")), msg[:200])
    check("alert for unproven strategy says practice only", "Practice idea" in msg)


def t_checkup():
    from . import checkup
    os.environ["DRY_RUN"] = "1"
    import io, contextlib
    buf = io.StringIO()
    orig = (data.download, news.fetch_feeds)
    data.download = lambda syms, interval, period: {s: intraday_frame(5) if interval == "15m" else daily_frame(300) for s in syms}
    news.fetch_feeds = lambda: [1]
    try:
        with contextlib.redirect_stdout(buf):
            code = checkup.main()
    finally:
        data.download, news.fetch_feeds = orig
        os.environ.pop("DRY_RUN", None)
    check("checkup: all green in a healthy dry run", code == 0 and "Everything is connected" in buf.getvalue())


def main():
    for fn in (t_indicators, t_clock, t_engine, t_swing_pending_entry, t_causality, t_decision_stats_news_commands, t_rating_and_settings, t_buttons_and_actions, t_rotation, t_plain_language, t_checkup, t_end_to_end):
        print(fn.__name__)
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAIL.append(f"{fn.__name__} crashed: {type(e).__name__}: {e}")
    print(f"\n{len(PASS)} checks passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", *FAIL, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main()
