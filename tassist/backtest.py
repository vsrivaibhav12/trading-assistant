"""Backtest using the SAME strategy, trade engine and market-weakness filter as live.
Entries fill at the NEXT bar's open; costs from config.COST_PCT are deducted.
Caveats: the stock list is today's big companies, so results are flattered (survivorship bias);
intraday/option history is only ~60 days (Yahoo's limit). News filtering can't be backtested."""
from __future__ import annotations
import json
import sys
import datetime as dt
import pandas as pd
from . import config as C, data, engine, stats, decision, rotation
from .strategies import ALL
from .universe import all_equity_insts, INSTRUMENTS, rotation_symbols, EQUITIES
from .db import SupabaseDB, MemoryDB


def _blocked(regime_map, sig, inst, same_day):
    if regime_map is None or inst["kind"] != "equity":
        return False
    d = pd.Timestamp(sig.bar_time.date())
    if same_day:
        reg = regime_map.get(d)
    else:                                         # intraday: only yesterday's close was known this morning
        prior = regime_map[regime_map.index < d]
        reg = prior.iloc[-1] if len(prior) else None
    return (reg == "DOWN" and sig.side == "LONG") or (reg == "UP" and sig.side == "SHORT")


def simulate(strat, inst, df, daily=None, regime=None):
    p = strat.prepare(df, daily)
    n, trades, i = len(p), [], (210 if strat.timeframe == "1d" else 60)
    same_day = strat.timeframe == "1d"
    while i < n - 1:
        sig = strat.signal_at(p, i, inst)
        if sig is None or _blocked(regime, sig, inst, same_day):
            i += 1
            continue
        j = i + 1
        entry = float(p["open"].iloc[j])
        long = sig.side == "LONG"
        if (long and entry <= sig.sl) or ((not long) and entry >= sig.sl):
            i += 1
            continue
        t = engine.make_trade(sig, entry, strat, ts=p.index[j])
        exit_k = None
        for k in range(j, n):
            engine.advance(t, p, k, strat)
            if t["status"] == "CLOSED":
                exit_k = k
                break
        if exit_k is None:
            break
        t["opened_at"], t["closed_at"] = p.index[j], p.index[exit_k]
        trades.append(t)
        if strat.timeframe == "15m":
            d0 = p.index[j].date()
            i = next((k for k in range(exit_k, n) if p.index[k].date() > d0), n)
        else:
            i = exit_k + 1
    return trades


def run(mode: str) -> list[dict]:
    strats = [s for s in ALL if s.mode == mode]
    if mode == "swing":
        insts = all_equity_insts()
        frames = data.download([i["yf"] for i in insts] + ["^NSEI"], "1d", "5y")
        nifty = frames.pop("^NSEI", None)
        daily = {}
    else:
        insts = (all_equity_insts()[:25] if mode == "intraday" else []) + (list(INSTRUMENTS.values()) if mode == "option" else [])
        frames = data.download([i["yf"] for i in insts], "15m", "60d")
        daily = data.download([i["yf"] for i in insts] + ["^NSEI"], "1d", "2y")
        nifty = daily.pop("^NSEI", None)
    regime = decision.regime_series(nifty) if nifty is not None and len(nifty) > 60 else None
    out = []
    for s in strats:
        rows = []
        for inst in insts:
            df = frames.get(inst["yf"])
            if df is None or len(df) < 100 or not s.applies(inst):
                continue
            rows += simulate(s, inst, df, daily.get(inst["yf"]), regime)
        rs = [t["r_multiple"] for t in rows]
        by_year = {}
        for t in rows:
            by_year.setdefault(str(t["closed_at"].year), []).append(t["r_multiple"])
        by_year = {y: dict(n=len(v), avg_r=round(sum(v) / len(v), 3)) for y, v in sorted(by_year.items())}
        p = stats.perf(rs)
        out.append(dict(run_at=dt.datetime.now(dt.timezone.utc), strategy=s.name, mode=mode, timeframe=s.timeframe,
                        universe_n=len(insts), trades=p["n"], win_rate=p["win_rate"], avg_r=p["avg_r"], total_r=p["total_r"],
                        profit_factor=p["pf"], max_dd_r=p["max_dd"], t_stat=p["t_stat"], verdict=p["verdict"],
                        avg_win_r=p["avg_win"], avg_loss_r=p["avg_loss"], payoff=p["payoff"], by_year=by_year,
                        note=("only ~60 days of 15-minute history exists on Yahoo - small sample" if s.timeframe == "15m" else
                              "today's big companies over 5 years: survivorship bias flatters this")))
    return out


def run_rotation() -> dict:
    syms = rotation_symbols()
    frames = data.download(syms + ["^NSEI"], "1d", "8y")
    nifty = frames.pop("^NSEI")
    large = {k: v for k, v in frames.items() if k[:-3] in EQUITIES}
    res = dict(broad=rotation.backtest(frames, nifty), large_caps=rotation.backtest(large, nifty),
               run_at=dt.datetime.now(dt.timezone.utc).isoformat(),
               warning="Both tests use today's surviving big companies, so they are flattered. Do not expect these numbers.")
    return res


def main(mode: str = "all"):
    url, key = C.env("SUPABASE_URL"), C.env("SUPABASE_SERVICE_KEY")
    db = SupabaseDB(url, key) if (url and key and C.env("DRY_RUN") != "1") else MemoryDB()
    modes = ["swing", "intraday", "option", "rotation"] if mode == "all" else [mode]
    for m in modes:
        if m == "rotation":
            res = run_rotation()
            db.kv_set("rotation_backtest", res)
            for grp in ("broad", "large_caps"):
                for k in ("with_market_filter", "always_invested"):
                    r = res[grp][k]
                    print(f"ROTATION {grp:10s} {k:19s} months={r['months']} CAGR={r['cagr']:.1f}% vs Nifty {r['nifty_cagr']:.1f}% | "
                          f"worst fall {r['max_dd']:.0f}% vs {r['nifty_max_dd']:.0f}% | beat Nifty {r['beat_pct']:.0f}% of months")
            continue
        for r in run(m):
            db.insert("backtests", r)
            wr = "n/a" if r["win_rate"] is None else f"{r['win_rate']:.0f}%"
            ar = "n/a" if r["avg_r"] is None else f"{r['avg_r']:+.2f}"
            pay = "n/a" if not r["payoff"] else f"{r['payoff']:.1f}x"
            print(f"{r['strategy']:16s} {m:8s} trades={r['trades']:4d} wins={wr:>5s} win/loss size={pay:>5s} avg={ar:>6s}  {r['verdict']}")
    return db


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
