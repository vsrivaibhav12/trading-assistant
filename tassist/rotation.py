"""Monthly momentum list - the one idea here with decades of published evidence (including Indian studies).
Simplified version of the public idea behind NSE's momentum indices (not a copy of their rules):
  score = average of 12-month and 6-month price gain (skipping the latest month), divided by the stock's volatility
  hold the top 20 with positive gains, equal money in each, re-pick once a month,
  and go to cash while Nifty is below its 200-day average (a pre-set rule, not tuned)."""
from __future__ import annotations
import math
import numpy as np
import pandas as pd

TOP_N = 20
ROUND_TRIP_COST_PCT = 0.25
MIN_TURNOVER = 5e8          # only stocks trading at least Rs 50 crore a day (3-month average): keeps the list buyable


def close_matrix(frames: dict, index) -> pd.DataFrame:
    C = pd.DataFrame({s: f["close"] for s, f in frames.items() if len(f)})
    return C.reindex(index).ffill(limit=5)


def turnover_matrix(frames: dict, index) -> pd.DataFrame:
    T = pd.DataFrame({s: (f["close"] * f["volume"]).rolling(60, min_periods=30).mean() for s, f in frames.items() if len(f)})
    return T.reindex(index).ffill(limit=5)


def scores_at(C: pd.DataFrame, i: int, T: pd.DataFrame | None = None) -> pd.Series:
    """Momentum scores using ONLY data up to row i. Highest first; ineligible names dropped."""
    if i < 273:
        return pd.Series(dtype=float)
    win = C.iloc[i - 252:i + 1]
    r12 = C.iloc[i - 21] / C.iloc[i - 252] - 1
    r6 = C.iloc[i - 21] / C.iloc[i - 126] - 1
    vol = win.pct_change().std() * math.sqrt(252)
    sc = ((r12 + r6) / 2) / vol
    ok = (win.count() >= 240) & (r12 > 0) & (r6 > 0) & vol.notna() & (vol > 0)
    if T is not None:
        ok &= T.iloc[i] >= MIN_TURNOVER
    return sc.where(ok).dropna().sort_values(ascending=False)


def month_end_positions(idx) -> list[int]:
    s = pd.Series(range(len(idx)), index=idx)
    return list(s.groupby([idx.year, idx.month]).last().values)


def _metrics(rets: list[float], bench: list[float]) -> dict:
    if not rets:
        return {}
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    beq = 1.0
    bpeak, bdd = 1.0, 0.0
    for r in bench:
        beq *= 1 + r
        bpeak = max(bpeak, beq)
        bdd = max(bdd, 1 - beq / bpeak)
    yrs = len(rets) / 12
    return dict(cagr=(eq ** (1 / yrs) - 1) * 100, max_dd=dd * 100, nifty_cagr=(beq ** (1 / yrs) - 1) * 100,
                nifty_max_dd=bdd * 100, months=len(rets),
                beat_pct=100 * sum(1 for a, b in zip(rets, bench) if a > b) / len(rets),
                positive_pct=100 * sum(1 for a in rets if a > 0) / len(rets))


def backtest(frames: dict, nifty: pd.DataFrame, top_n: int = TOP_N) -> dict:
    nc = nifty["close"]
    C = close_matrix(frames, nc.index)
    T = turnover_matrix(frames, nc.index)
    sma200 = nc.rolling(200).mean()
    me = month_end_positions(nc.index)
    out = {}
    for name, use_filter in (("with_market_filter", True), ("always_invested", False)):
        prev: set = set()
        rets, bench = [], []
        for a, b in zip(me[:-1], me[1:]):
            if a < 273:
                continue
            ok = (not use_filter) or bool(nc.iloc[a] > sma200.iloc[a])
            hold = list(scores_at(C, a, T).index[:top_n]) if ok else []
            r = 0.0
            if hold:
                g = (C.iloc[b][hold] / C.iloc[a][hold] - 1).dropna()
                r = float(g.mean()) if len(g) else 0.0
            cost = (len(prev - set(hold)) + len(set(hold) - prev)) / top_n * (ROUND_TRIP_COST_PCT / 2) / 100
            rets.append(r - cost)
            bench.append(float(nc.iloc[b] / nc.iloc[a] - 1))
            prev = set(hold)
        out[name] = _metrics(rets, bench)
    out["universe_n"] = int(C.shape[1])
    out["period"] = f"{nc.index[me[0] if me[0] > 273 else 273].date()} to {nc.index[-1].date()}"
    return out


def live_target(frames: dict, nifty: pd.DataFrame, top_n: int = TOP_N) -> dict:
    nc = nifty["close"]
    C = close_matrix(frames, nc.index)
    T = turnover_matrix(frames, nc.index)
    i = len(C) - 1
    ok = bool(nc.iloc[-1] > nc.rolling(200).mean().iloc[-1])
    sc = scores_at(C, i, T)
    ranked = list(sc.index[:top_n])
    return dict(ok=ok, holdings=ranked if ok else [], prices_all={s: float(v) for s, v in C.iloc[i].dropna().items()},
                nifty=float(nc.iloc[-1]), asof=str(nc.index[-1].date()), n_scored=int(len(sc)))


def rebalance(prev_state: dict | None, tgt: dict, key: str, top_n: int = TOP_N):
    """Compare last month's list with this month's. Returns (new_state, buys, sells, keeps)."""
    prev_hold = (prev_state or {}).get("holdings", {})
    new = {s: tgt["prices_all"][s] for s in tgt["holdings"] if s in tgt["prices_all"]}
    buys = [s for s in new if s not in prev_hold]
    sells = [s for s in prev_hold if s not in new]
    keeps = [s for s in new if s in prev_hold]
    hist = list((prev_state or {}).get("history", []))
    if prev_state:
        rets = [tgt["prices_all"][s] / p - 1 for s, p in prev_hold.items() if s in tgt["prices_all"]]
        gross = float(np.mean(rets)) if rets else 0.0
        cost = (len(sells) + len(buys)) / top_n * (ROUND_TRIP_COST_PCT / 2) / 100
        hist.append(dict(month=prev_state["month"], port=round(gross - cost, 5),
                         nifty=round(tgt["nifty"] / prev_state["nifty"] - 1, 5)))
    state = dict(month=key, holdings=new, nifty=tgt["nifty"], ok=tgt["ok"], history=hist[-120:])
    return state, buys, sells, keeps


def rate_rotation(history: list) -> dict:
    """Rating rests on PUBLISHED evidence first (NSE's momentum index vs Nifty 200, academic studies on Indian stocks).
    Our own history test is shown on the dashboard but not used to rate: it is flattered by survivorship bias.
    After 3+ years of live practice the live record takes over."""
    from .rating import LABELS
    key, basis = "PROMISING", "published research"
    text = ("NSE's own momentum index has beaten the Nifty 200 by roughly 5% a year since 2005 (about 19% vs 14% in mid-2026; "
            "the years before 2018 are a backtest). It can fall hard in bad markets, and part of the edge may simply be a reward for "
            "holding less-traded stocks")
    if len(history) >= 36:
        pr, nr = [h["port"] for h in history], [h["nifty"] for h in history]
        yrs = len(pr) / 12
        pc = (np.prod([1 + x for x in pr]) ** (1 / yrs) - 1) * 100
        nc = (np.prod([1 + x for x in nr]) ** (1 / yrs) - 1) * 100
        key = "PROVEN" if pc - nc > 2 else ("WEAK" if pc - nc < -2 else "PROMISING")
        basis = "live practice"
        text = f"over {len(pr)} live months it grew about {pc:.0f}% a year vs {nc:.0f}% for Nifty"
    e, l = LABELS[key]
    return dict(key=key, emoji=e, label=l, basis=basis, text=text)
