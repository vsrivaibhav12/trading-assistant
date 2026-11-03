"""Scanner. Stage 1 reads cheap DAILY data for the whole universe and shortlists who deserves a 15-minute
chart read. Stage 2 (`scan`) runs the strategies on the shortlisted charts."""
from __future__ import annotations
import pandas as pd
from . import indicators as ind


def rank_intraday(daily_raw: dict, now, top_n: int, min_turnover: float = 2e8) -> list[str]:
    today = pd.Timestamp(now.date())
    scored = []
    for sym, d in daily_raw.items():
        if not sym.endswith(".NS") or d is None or len(d) < 40:
            continue
        has_today = d.index[-1] >= today
        prev = d.iloc[:-1] if has_today else d
        if len(prev) < 30:
            continue
        a = ind.atr(prev, 14).iloc[-1]
        c = float(prev["close"].iloc[-1])
        if not a > 0 or c <= 0:
            continue
        turnover = float((prev["close"] * prev["volume"]).rolling(20).mean().iloc[-1])
        atr_pct = a / c * 100
        if turnover < min_turnover or atr_pct < 0.8:
            continue
        gap = (float(d["open"].iloc[-1]) / c - 1) * 100 if has_today else 0.0
        near = min((prev["high"].rolling(20).max().iloc[-1] - c) / a, (c - prev["low"].rolling(20).min().iloc[-1]) / a)
        vavg = prev["volume"].rolling(20).mean().iloc[-1]
        surge = float(prev["volume"].iloc[-1] / vavg) if vavg > 0 else 1.0
        score = 2 * abs(gap) / atr_pct + max(0.0, 1.5 - float(near)) + 0.5 * min(surge, 3)
        scored.append((score, sym))
    scored.sort(reverse=True)
    return [s for _, s in scored[:top_n]]


def scan(strats, insts, frames: dict, daily: dict, lookback: int = 2):
    """Run strategies on prepared charts; look at the last `lookback` closed bars so one missed tick loses nothing."""
    out = []
    for inst in insts:
        df = frames.get(inst["yf"])
        if df is None or len(df) < 30:
            continue
        for strat in strats:
            if not strat.applies(inst):
                continue
            try:
                p = strat.prepare(df, daily.get(inst["yf"]))
                n = len(p)
                for i in range(n - 1, max(n - 1 - lookback, -1), -1):
                    sig = strat.signal_at(p, i, inst)
                    if sig:
                        out.append((sig, strat, inst))
                        break
            except Exception as e:
                print(f"scan: {strat.name} failed on one symbol ({type(e).__name__})")
    return out
