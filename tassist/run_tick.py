"""One tick = one short run: Telegram taps -> news -> read charts -> manage open trades -> scan -> alert -> exit.
Triggered every 15 minutes by cron-job.org (GitHub Actions workflow_dispatch). No state lives on the runner;
everything persists in Supabase. Logs deliberately contain counts only (the repo and its logs are public)."""
from __future__ import annotations
import datetime as dt
import json
import time
from pathlib import Path
import pandas as pd
from . import config as C, clock, data, news, commands, decision, scanner, engine, rating, settings, rotation
from . import messages as M
from .strategies import ALL, strategy_for
from .universe import EQUITIES, INSTRUMENTS, all_equity_insts, inst_for, equity_inst, rotation_symbols
from .db import SupabaseDB, MemoryDB
from .telegram import Telegram

PERSIST = ("entry", "t1", "t2", "sl", "t1_hit", "best", "bars_held", "status", "exit_price", "exit_reason", "closed_at",
           "r_multiple", "pnl_pct", "last_bar", "meta")
SEED = Path(__file__).with_name("seed_backtests.json")


def log(*a):
    print(*a, flush=True)


def build_services():
    dry = C.env("DRY_RUN") == "1"
    url, key = C.env("SUPABASE_URL"), C.env("SUPABASE_SERVICE_KEY")
    if not dry and not (url and key):
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_KEY are not set (GitHub Secrets).")
    db = MemoryDB() if dry else SupabaseDB(url, key)
    tg = Telegram(C.env("TELEGRAM_BOT_TOKEN"), C.env("TELEGRAM_CHAT_ID"), dry=dry)
    return db, tg, dry


def _iso(s):
    return dt.datetime.fromisoformat(s)


def session_of(sym):
    return "MCX" if sym in ("CL=F", "INR=X") else inst_for(sym)["session"]


# --------------------------------------------------------------------------- evidence & ratings
def ensure_seed(db):
    """First run: load the history-test results shipped with the package so ratings exist from day one."""
    if db.select("backtests", limit=1) or not SEED.exists():
        return
    seed = json.loads(SEED.read_text())
    for r in seed.get("backtests", []):
        db.insert("backtests", r)
    if seed.get("rotation_backtest") and not db.kv_get("rotation_backtest"):
        db.kv_set("rotation_backtest", seed["rotation_backtest"])


# --------------------------------------------------------------------------- trade management
def manage_trade(ctx, t, df, daily_df):
    """Advance one open trade over bars completed since last check. Returns number of alerts sent."""
    db, tg = ctx["db"], ctx["tg"]
    strat = strategy_for(t)
    p = strat.prepare(df, daily_df)
    if not t.get("last_bar"):
        db.update("trades", t["id"], {"last_bar": p.index[-1].isoformat()})
        return 0
    lb = pd.Timestamp(t["last_bar"])
    idxs = [k for k in range(len(p)) if p.index[k] > lb]
    if not idxs:
        return 0
    if t["meta"].get("pending_entry"):          # swing: the signal fired at the close, the fill is the next open
        o, long = float(p["open"].iloc[idxs[0]]), t["side"] == "LONG"
        if (long and o <= t["sl"]) or ((not long) and o >= t["sl"]):
            t.update(status="CANCELLED", exit_reason="GAP_THROUGH_STOP", last_bar=p.index[idxs[0]].isoformat())
            db.update("trades", t["id"], {k: t.get(k) for k in PERSIST})
            return 0
        risk, d = abs(o - t["initial_sl"]), (1 if long else -1)
        t.update(entry=o, best=o, t1=o + d * strat.t1_r * risk, t2=o + d * strat.t2_r * risk)
        t["meta"]["pending_entry"] = False
    events = []
    for k in idxs:
        evs = engine.advance(t, p, k, strat)
        for ev in evs:
            if ev["type"] == "EXIT":
                ev["r"] = round(engine.result_r(t, ev["price"]), 2)
        events += evs
        t["last_bar"] = p.index[k].isoformat()
        if t["status"] == "CLOSED":
            break
    if t["source"] == "user" and t["status"] == "CLOSED":      # never auto-close YOUR trade: suggest, you confirm
        t["meta"]["exit_suggested"] = dict(reason=t["exit_reason"], price=t["exit_price"], r=t["r_multiple"])
        t.update(status="OPEN", exit_price=None, exit_reason=None, closed_at=None, r_multiple=None, pnl_pct=None)
    db.update("trades", t["id"], {k: t.get(k) for k in PERSIST})
    notify = t["source"] == "user" or (t.get("taken") and not t["meta"].get("muted_followups"))
    sent = 0
    if notify:
        inst = inst_for(t["symbol"])
        for ev in events:
            btn = M.sold_buttons(t["id"]) if (t["source"] == "user" and ev["type"] == "EXIT") else None
            if tg.send(M.fmt_event(t, ev, inst, ctx.get("fx")), btn):
                sent += 1
    return sent


# --------------------------------------------------------------------------- candidates -> alerts
def process_candidates(ctx, cands, frames_for, regime, news_rows, fx, open_keys):
    db, tg, now, cfg, ratings = ctx["db"], ctx["tg"], ctx["now"], ctx["cfg"], ctx["ratings"]
    if not cands:
        return 0, 0
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    todays = db.select("signals", [("created_at", "gte", start_today)])
    done = {(s["strategy"], s["symbol"]) for s in todays}
    n_alerts_today = sum(1 for s in todays if s.get("decision") == "OK")
    n_shadow_open = len(db.select("trades", [("status", "eq", "OPEN"), ("source", "eq", "system")]))
    intr = [c for c in cands if c[0].mode != "swing"]
    prices = data.last_prices(sorted({c[0].symbol for c in intr}), now) if intr else {}
    sent = n_sig = 0
    for sig, strat, inst in sorted(cands, key=lambda c: -c[0].score):
        key = (sig.strategy, sig.symbol)
        if key in open_keys:
            continue
        if sig.mode == "swing":
            cut = now - dt.timedelta(days=C.SWING_DEDUPE_DAYS)
            if db.select("signals", [("strategy", "eq", sig.strategy), ("symbol", "eq", sig.symbol), ("created_at", "gte", cut)]):
                continue
        elif key in done:
            continue
        px = prices.get(sig.symbol) if sig.mode != "swing" else None
        nf = news.flags_for(news_rows, inst["name"]) if inst["kind"] == "equity" else {}
        rt = ratings.get((sig.strategy, sig.mode)) or rating.rate([], None)
        ok, reason = decision.evaluate(sig, strat, inst, px, regime, nf, rt["key"], cfg)
        if ok and (sent >= C.MAX_ALERTS_PER_TICK or n_alerts_today + sent >= C.MAX_ALERTS_PER_DAY):
            ok, reason = False, "ALERT_CAP"
        entry = px if px else sig.entry
        t = engine.make_trade(sig, entry, strat, taken=ok)
        if sig.mode == "swing":
            t["meta"]["pending_entry"] = True
        t["qty"] = decision.position_size(entry, sig.sl, cfg) if inst["kind"] == "equity" else None
        fr = frames_for(sig.mode).get(sig.symbol)
        t["last_bar"] = fr.index[-1].isoformat() if fr is not None and len(fr) else None
        t_row = None
        if ok or n_shadow_open < C.MAX_OPEN_SHADOW:
            t_row = db.insert("trades", t)
            t["id"] = t_row["id"]
            n_shadow_open += 1
        db.insert("signals", dict(strategy=sig.strategy, symbol=sig.symbol, name=sig.name, mode=sig.mode, side=sig.side,
                                  entry=entry, sl=sig.sl, score=sig.score, reason=sig.reason, decision=reason,
                                  bar_time=sig.bar_time, trade_id=t.get("id"), meta=sig.meta))
        n_sig += 1
        open_keys.add(key)
        if ok and t_row:
            warns = ["The company's results are in today's news - prices can jump either way."] if nf.get("earnings") else []
            msg = M.fmt_signal(sig, strat, t, inst, t["qty"], rt, nf.get("headline"), warns, fx, cfg)
            if tg.send(msg, M.signal_buttons(t["id"])):
                sent += 1
    return n_sig, sent


# --------------------------------------------------------------------------- main market job
def run_market(ctx):
    db, tg, now, pl = ctx["db"], ctx["tg"], ctx["now"], ctx["plan"]
    open_all = [t for t in db.select("trades", [("status", "eq", "OPEN")])
                if t.get("tracked", True) and not (t.get("meta") or {}).get("exit_suggested")]
    syms = ([i["yf"] for i in all_equity_insts()] + [i["yf"] for i in INSTRUMENTS.values()] + ["INR=X"]
            + [t["symbol"] for t in open_all])
    raw_daily = data.download(syms, "1d", "1y")
    if not raw_daily:
        raise RuntimeError("Yahoo returned no daily data")
    daily = {s: data.drop_incomplete_daily(d, now, session_of(s)) for s, d in raw_daily.items()}
    regime = decision.regime_from_daily(daily.get("^NSEI"))
    fx = float(daily["INR=X"]["close"].iloc[-1]) if "INR=X" in daily and len(daily["INR=X"]) else None
    ctx["fx"] = fx
    news_rows = news.recent(db, 24)

    scan_insts, need = [], set()
    if pl["nse_scan"]:
        top = scanner.rank_intraday(raw_daily, now, C.TOP_N_INTRADAY)
        scan_insts += [equity_inst(s[:-3]) for s in top] + [INSTRUMENTS["NIFTY"], INSTRUMENTS["BANKNIFTY"]]
    if pl["mcx_scan"]:
        scan_insts.append(INSTRUMENTS["CRUDE"])
    need |= {i["yf"] for i in scan_insts}
    m15_trades = [t for t in open_all if t["mode"] in ("intraday", "option") and
                  ((session_of(t["symbol"]) == "NSE" and pl["nse_manage"]) or (session_of(t["symbol"]) == "MCX" and pl["mcx_manage"]))]
    need |= {t["symbol"] for t in m15_trades}
    if pl["nse_manage"] or pl["nse_scan"]:
        need.add("^NSEI")
    if pl["mcx_manage"] or pl["mcx_scan"]:
        need.add("CL=F")
    frames = {}
    if need:
        raw15 = data.download(sorted(need), "15m", "10d")
        frames = {s: data.drop_incomplete_intraday(d, 15, now) for s, d in raw15.items()}

    def fresh(ref):
        d = frames.get(ref)
        if d is None or d.empty or d.index[-1].date() != now.date():
            return False
        lag = data.lag_minutes(d, 15, now)
        ctx["lag"] = lag if ctx.get("lag") is None else max(ctx["lag"], lag)
        if lag > C.STALE_MIN:
            last = db.kv_get("stale_notice")
            if not last or (now - _iso(last["ts"])).total_seconds() > 3600:
                tg.send("⚠️ The price feed looks out of date right now, so I'm pausing checks until it recovers. Nothing for you to do.")
                db.kv_set("stale_notice", {"ts": now.isoformat()})
            return False
        return True

    ok_sess = {"NSE": fresh("^NSEI") if ("^NSEI" in need) else False, "MCX": fresh("CL=F") if ("CL=F" in need) else False}

    alerts = 0
    for t in m15_trades:                      # stops & exits beat new ideas
        if ok_sess.get(session_of(t["symbol"])) and t["symbol"] in frames:
            alerts += manage_trade(ctx, t, frames[t["symbol"]], daily.get(t["symbol"]))
    if pl["after_close"] and (db.kv_get("swing_manage_date") or {}).get("d") != str(now.date()):
        for t in [t for t in open_all if t["mode"] == "swing" and t["symbol"] in daily]:
            alerts += manage_trade(ctx, t, daily[t["symbol"]], None)
        db.kv_set("swing_manage_date", {"d": str(now.date())})

    open_keys = {(t["strategy"], t["symbol"]) for t in db.select("trades", [("status", "eq", "OPEN")])}
    n_sig = 0
    live_insts = [i for i in scan_insts if ok_sess.get(i["session"])]
    if live_insts:
        cands = scanner.scan([s for s in ALL if s.mode in ("intraday", "option")], live_insts, frames, daily)
        a, b = process_candidates(ctx, cands, lambda m: frames, regime, news_rows, fx, open_keys)
        n_sig, alerts = n_sig + a, alerts + b

    nifty = daily.get("^NSEI")
    if pl["after_close"] and nifty is not None and len(nifty) and nifty.index[-1].date() == now.date() \
            and (db.kv_get("swing_scan_date") or {}).get("d") != str(now.date()):
        eq = {i["yf"]: daily[i["yf"]] for i in all_equity_insts() if i["yf"] in daily}
        cands = scanner.scan([s for s in ALL if s.mode == "swing"], [i for i in all_equity_insts() if i["yf"] in eq],
                             eq, {}, lookback=1)
        a, b = process_candidates(ctx, cands, lambda m: daily, regime, news_rows, fx, open_keys)
        n_sig, alerts = n_sig + a, alerts + b
        db.kv_set("swing_scan_date", {"d": str(now.date())})
    ctx.update(regime=regime, n_signals=n_sig, n_alerts=alerts)
    log(f"market: regime={regime} scanned={len(live_insts)} signals={n_sig} alerts={alerts}")


# --------------------------------------------------------------------------- monthly momentum list
def rotation_key(now, db):
    d, t = now.date(), now.time()
    if not clock.is_trading_day(d) or t < dt.time(15, 45):
        return None
    state = db.kv_get("rotation_state")
    if state is None:
        return f"{d:%Y-%m}-start"                                    # first ever list: right away
    nxt = d + dt.timedelta(days=1)
    while not clock.is_trading_day(nxt):
        nxt += dt.timedelta(days=1)
    if nxt.month != d.month:
        return f"{d:%Y-%m}"
    if d.day <= 7:                                                   # catch up if last month's was missed
        prev = f"{(d.replace(day=1) - dt.timedelta(days=1)):%Y-%m}"
        if not str(state.get("month", "")).startswith(prev) and state.get("month") != prev:
            return prev
    return None


def run_rotation(ctx):
    db, tg, now, cfg = ctx["db"], ctx["tg"], ctx["now"], ctx["cfg"]
    if cfg.get("paused") or not cfg.get("send_momentum", True):
        return
    key = rotation_key(now, db)
    state = db.kv_get("rotation_state")
    if not key or (state and state.get("month") == key):
        return
    frames = data.download(rotation_symbols() + ["^NSEI"], "1d", "2y")
    if "^NSEI" not in frames:
        raise RuntimeError("no Nifty data for the monthly list")
    frames = {s: data.drop_incomplete_daily(d, now, "NSE") for s, d in frames.items()}
    nifty = frames.pop("^NSEI")
    tgt = rotation.live_target(frames, nifty)
    new_state, buys, sells, keeps = rotation.rebalance(state, tgt, key)
    rt = rotation.rate_rotation(new_state["history"])
    tg.send(M.fmt_rotation(now.strftime("%B %Y"), tgt["ok"], buys, sells, keeps, rt, cfg, rotation.TOP_N))
    db.kv_set("rotation_state", new_state)
    log(f"rotation: list updated ({len(new_state['holdings'])} stocks)")


# --------------------------------------------------------------------------- housekeeping
def open_names(db):
    rows = db.select("trades", [("status", "eq", "OPEN")])
    return sorted({r["name"] for r in rows if r["source"] == "user" or (r.get("taken") and not (r.get("meta") or {}).get("muted_followups"))})


def housekeeping(ctx):
    db, tg, now, pl, cfg = ctx["db"], ctx["tg"], ctx["now"], ctx["plan"], ctx["cfg"]
    today = str(now.date())
    if pl["heartbeat"] and (db.kv_get("heartbeat_date") or {}).get("d") != today:
        d = data.download(["^NSEI"], "1d", "1y")
        reg = decision.regime_from_daily(data.drop_incomplete_daily(d["^NSEI"], now) if "^NSEI" in d else None)
        ratings = rating.compute_ratings(db)
        learning = not any(r["key"] == "PROVEN" for r in ratings.values())
        note = "" if d else "⚠️ I couldn't get prices this morning. I'll keep trying."
        if cfg.get("paused"):
            note += "\n⏸ You have paused new ideas (change this in the dashboard Settings)."
        tg.send(M.fmt_heartbeat(now, reg, len(EQUITIES), learning, open_names(db), note.strip()))
        db.kv_set("heartbeat_date", {"d": today})
    if pl["after_close"] and (db.kv_get("summary_date") or {}).get("d") != today and ctx.get("market_ran"):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        closed_today = [r for r in db.select("trades", [("status", "eq", "CLOSED"), ("closed_at", "gte", start)])
                        if r.get("taken") or r["source"] == "user"]
        sent_today = sum(1 for s in db.select("signals", [("created_at", "gte", start)]) if s.get("decision") == "OK")
        tg.send(M.fmt_summary(now, sent_today, closed_today, settings.risk_amount(cfg), open_names(db)))
        db.kv_set("summary_date", {"d": today})
    wk = now.strftime("%G-W%V")
    if pl["weekly"] and (db.kv_get("weekly_key") or {}).get("k") != wk:
        tg.send(commands.scorecard_text(db, now))
        for table in ("trades", "signals"):
            rows = db.select(table, order="id.asc", limit=20000)
            if rows:
                tg.send_document(f"{table}_{wk}.csv", pd.DataFrame(rows).to_csv(index=False).encode(), f"Weekly backup of your {table}")
        db.kv_set("weekly_key", {"k": wk})


def run_news(ctx):
    db, now = ctx["db"], ctx["now"]
    if not ctx["plan"]["news"]:
        return
    last = db.kv_get("news_last")
    if last and (now - _iso(last["ts"])).total_seconds() < C.NEWS_EVERY_MIN * 60 - 90:
        return
    n = news.refresh(db)
    db.kv_set("news_last", {"ts": now.isoformat()})
    log(f"news: {n} new headlines")


def main():
    t0 = time.time()
    db, tg, dry = build_services()
    now = clock.now_ist()
    force = dry or C.env("FORCE", "false").lower() == "true"
    last = db.kv_get("last_tick")
    if not force and last and (now - _iso(last["ts"])).total_seconds() < C.DUP_TICK_SEC:
        log("duplicate tick - skipped")
        return
    db.kv_set("last_tick", {"ts": now.isoformat()})
    pl = clock.plan(now)
    ctx = dict(db=db, tg=tg, now=now, plan=pl, errors=[], lag=None, n_signals=0, n_alerts=0, market_ran=False,
               cfg=settings.load(db), ratings={})

    def safe(name, fn):
        try:
            return fn()
        except Exception as ex:
            ctx["errors"].append(f"{name}: {type(ex).__name__}: {str(ex)[:140]}")
            log(f"ERROR in {name}: {type(ex).__name__}")

    safe("seed", lambda: ensure_seed(db))
    safe("commands", lambda: commands.process(db, tg, now, C.env("TELEGRAM_CHAT_ID")))
    ctx["cfg"] = settings.load(db)                       # a /pause tap takes effect immediately
    safe("ratings", lambda: ctx.update(ratings=rating.compute_ratings(db)))
    safe("news", lambda: run_news(ctx))
    if any(pl[k] for k in ("nse_scan", "nse_manage", "mcx_scan", "mcx_manage", "after_close")):
        ctx["market_ran"] = True
        safe("market", lambda: run_market(ctx))
    if pl["after_close"]:
        safe("rotation", lambda: run_rotation(ctx))
    safe("housekeeping", lambda: housekeeping(ctx))
    safe("runlog", lambda: db.insert("runs", dict(started_at=now, job="tick", ok=not ctx["errors"],
                                                  detail="; ".join(ctx["errors"])[:500], data_lag_min=ctx["lag"],
                                                  n_signals=ctx["n_signals"], n_alerts=ctx["n_alerts"],
                                                  seconds=round(time.time() - t0, 1))))
    if ctx["errors"] and not dry:
        prev = db.kv_get("err_notice")
        if not prev or (now - _iso(prev["ts"])).total_seconds() > 3600:
            tg.send("⚠️ Something went wrong behind the scenes. It is usually temporary and fixes itself. "
                    "If you keep seeing this message, tell whoever set this up.\n\n<i>" + "\n".join(ctx["errors"])[:600] + "</i>")
            db.kv_set("err_notice", {"ts": now.isoformat()})
    log(f"tick done in {time.time() - t0:.1f}s, errors={len(ctx['errors'])}")


if __name__ == "__main__":
    main()
