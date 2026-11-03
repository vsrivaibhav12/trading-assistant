"""Trade lifecycle. `advance` is the ONE function that moves a trade forward by one bar.
Live management, the backtest and the self-test all call it, so what you backtest is what runs."""
from __future__ import annotations
import pandas as pd
from . import config as C


def is_long(t) -> bool:
    return t["side"] == "LONG"


def risk_of(t) -> float:
    return abs(t["entry"] - t["initial_sl"])


def cost_r(t) -> float:
    r = risk_of(t)
    return (C.COST_PCT.get(t["mode"], 0.1) / 100 * t["entry"]) / r if r > 0 else 0.0


def result_r(t, px) -> float:
    r = risk_of(t)
    gross = (px - t["entry"]) / r if is_long(t) else (t["entry"] - px) / r
    return gross - cost_r(t)


def make_trade(sig, entry, strat, source="system", taken=True, ts=None) -> dict:
    risk = abs(entry - sig.sl)
    d = 1 if sig.side == "LONG" else -1
    meta = dict(sig.meta)
    meta["open_date"] = str((ts or sig.bar_time).date()) if (ts or sig.bar_time) is not None else None
    if sig.option:
        meta["option"] = sig.option
    return dict(source=source, strategy=sig.strategy, symbol=sig.symbol, name=sig.name, mode=sig.mode,
                timeframe=sig.timeframe, side=sig.side, entry=float(entry), initial_sl=float(sig.sl), sl=float(sig.sl),
                t1=float(entry + d * strat.t1_r * risk), t2=float(entry + d * strat.t2_r * risk), qty=None,
                status="OPEN", t1_hit=False, best=float(entry), bars_held=0, taken=taken, tracked=True, meta=meta)


def close_trade(t, px, reason, ts):
    sign = 1 if is_long(t) else -1
    t.update(status="CLOSED", exit_price=float(px), exit_reason=reason, closed_at=ts,
             r_multiple=round(result_r(t, px), 3), pnl_pct=round(sign * (px - t["entry"]) / t["entry"] * 100, 3))


def advance(t: dict, p: pd.DataFrame, i: int, strat) -> list[dict]:
    """Process bar i of prepared frame p for trade t. Mutates t. Returns events."""
    ev: list[dict] = []
    row = p.iloc[i]
    o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
    atr = float(row["atr"]) if "atr" in p.columns and row["atr"] == row["atr"] else 0.0
    ts = p.index[i]
    long = is_long(t)
    t["bars_held"] = t.get("bars_held", 0) + 1
    was_t1 = t["t1_hit"]          # trailing only starts the bar AFTER T1, so one tick never sends two stop moves

    # intraday-style trade still open on a later day -> flatten at that bar's open
    od = t["meta"].get("open_date")
    if t["mode"] in ("intraday", "option") and od and str(ts.date()) > od:
        close_trade(t, o, "EOD", ts)
        return [dict(type="EXIT", reason="EOD", price=o, why="carried past the session end")]

    # 1) stop (checked first: conservative when a bar touches both stop and target)
    if (l <= t["sl"]) if long else (h >= t["sl"]):
        px = min(t["sl"], o) if long else max(t["sl"], o)
        reason = "TRAIL_STOP" if t["t1_hit"] else "STOP"
        close_trade(t, px, reason, ts)
        return [dict(type="EXIT", reason=reason, price=px)]

    t["best"] = max(t["best"], h) if long else min(t["best"], l)

    # 2) first target -> breakeven
    if not t["t1_hit"] and ((h >= t["t1"]) if long else (l <= t["t1"])):
        t["t1_hit"] = True
        t["sl"] = max(t["sl"], t["entry"]) if long else min(t["sl"], t["entry"])
        ev.append(dict(type="T1", price=t["t1"], new_sl=t["sl"]))

    # 3) final target
    if (h >= t["t2"]) if long else (l <= t["t2"]):
        close_trade(t, t["t2"], "TARGET", ts)
        ev.append(dict(type="EXIT", reason="TARGET", price=t["t2"]))
        return ev

    # 4) chandelier trail once T1 is hit
    if was_t1 and atr > 0:
        cand = t["best"] - strat.trail_mult * atr if long else t["best"] + strat.trail_mult * atr
        if (long and cand > t["sl"] + 0.25 * atr) or ((not long) and cand < t["sl"] - 0.25 * atr):
            t["sl"] = float(cand)
            ev.append(dict(type="TRAIL", price=c, new_sl=t["sl"]))

    # 5) setup lost -> early exit to protect capital
    why = strat.exit_check(p, i, t)
    if why:
        close_trade(t, c, "SETUP_LOST", ts)
        ev.append(dict(type="EXIT", reason="SETUP_LOST", price=c, why=why))
        return ev

    # 6) time exits
    if strat.timeframe == "15m":
        if (ts + pd.Timedelta(minutes=15)).time() >= C.EOD_EXIT[t["meta"].get("session", "NSE")]:
            close_trade(t, c, "EOD", ts)
            ev.append(dict(type="EXIT", reason="EOD", price=c, why="end-of-day exit"))
    elif strat.max_hold and t["bars_held"] >= strat.max_hold:
        close_trade(t, c, "MAX_HOLD", ts)
        ev.append(dict(type="EXIT", reason="MAX_HOLD", price=c, why=f"held {t['bars_held']} days"))
    return ev
