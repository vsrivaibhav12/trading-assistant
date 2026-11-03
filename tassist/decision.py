"""Decision layer: takes a raw strategy signal and decides whether it deserves an alert.
Rules are simple, explicit and logged, so the scorecard can show whether each rule helps."""
from __future__ import annotations
import math

GOOD = ("PROVEN", "PROMISING")


def evaluate(sig, strat, inst, entry_px, regime, news, rating_key, cfg, muted=False):
    """Returns (ok, reason). The reason is stored with the signal either way."""
    risk = abs(sig.entry - sig.sl)
    if risk <= 0:
        return False, "BAD_LEVELS"
    if cfg.get("paused"):
        return False, "PAUSED"
    if not cfg.get(f"send_{sig.mode}", True):
        return False, "TURNED_OFF"
    if muted:
        return False, "MUTED"
    if rating_key == "WEAK":
        return False, "NOT_WORKING"
    if cfg.get("only_good") and rating_key not in GOOD:
        return False, "UNPROVEN"
    if entry_px is not None and abs(entry_px - sig.entry) > 0.5 * risk:
        return False, "PRICE_MOVED"          # chasing, or the setup is already weakening
    if inst["kind"] == "equity":
        if regime == "DOWN" and sig.side == "LONG":
            return False, "REGIME_DOWN"
        if regime == "UP" and sig.side == "SHORT":
            return False, "REGIME_UP"
        if news.get("neg") and sig.side == "LONG":
            return False, "NEWS_NEGATIVE"
        if news.get("pos") and sig.side == "SHORT":
            return False, "NEWS_POSITIVE"
    return True, "OK"


def regime_from_daily(nifty_daily) -> str:
    """UP / DOWN / RANGE from Nifty's last completed daily bars (EMA20 vs EMA50 and close)."""
    from . import indicators as ind
    if nifty_daily is None or len(nifty_daily) < 60:
        return "UNKNOWN"
    e20, e50 = ind.ema(nifty_daily["close"], 20).iloc[-1], ind.ema(nifty_daily["close"], 50).iloc[-1]
    c = nifty_daily["close"].iloc[-1]
    if c > e50 and e20 > e50:
        return "UP"
    if c < e50 and e20 < e50:
        return "DOWN"
    return "RANGE"


def regime_series(nifty_daily):
    """Same rule as above for every date (used by the backtester so it matches live behaviour)."""
    import numpy as np
    import pandas as pd
    from . import indicators as ind
    c = nifty_daily["close"]
    e20, e50 = ind.ema(c, 20), ind.ema(c, 50)
    out = pd.Series("RANGE", index=c.index, dtype=object)
    out[(c > e50) & (e20 > e50)] = "UP"
    out[(c < e50) & (e20 < e50)] = "DOWN"
    out[e50.isna()] = "UNKNOWN"
    return out


def position_size(entry: float, sl: float, cfg) -> int:
    risk = abs(entry - sl)
    if risk <= 0:
        return 0
    return int(math.floor(float(cfg["capital"]) * float(cfg["risk_pct"]) / 100 / risk))
