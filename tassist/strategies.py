"""Strategies. Each one is a small class with three methods:

  prepare(df, daily)   -> adds indicator columns once (vectorised, causal)
  signal_at(p, i, inst)-> Signal if the bar *closing at i* triggers an entry, else None
  exit_check(p, i, trade) -> text reason if the setup is lost at the close of bar i

The same code path is used live, in the backtest, and in the self-test, so there is one source of truth.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import time
import pandas as pd
from . import indicators as ind
from . import config as C


@dataclass
class Signal:
    strategy: str
    mode: str
    symbol: str
    name: str
    side: str
    entry: float
    sl: float
    timeframe: str
    score: float
    reason: str
    invalid: str = ""
    bar_time: object = None
    meta: dict = field(default_factory=dict)
    option: dict | None = None


def _daily_bias(daily: pd.DataFrame) -> pd.DataFrame:
    e20, e50 = ind.ema(daily["close"], 20), ind.ema(daily["close"], 50)
    up = ((daily["close"] > e20) & (e20 > e50)).shift(1)
    dn = ((daily["close"] < e20) & (e20 < e50)).shift(1)
    out = pd.DataFrame({"bias_up": up, "bias_dn": dn})
    out.index = [d.date() for d in out.index]
    return out


def _prep_intraday(df: pd.DataFrame, daily) -> pd.DataFrame:
    p = df.copy()
    p["date"] = p.index.date
    p["tod"] = [t.time() for t in p.index]
    p["atr"] = ind.atr(p, 14)
    p["ema9"], p["ema20"] = ind.ema(p["close"], 9), ind.ema(p["close"], 20)
    p["ema21"], p["ema50"] = ind.ema(p["close"], 21), ind.ema(p["close"], 50)
    p["adx"] = ind.adx(p, 14)
    p["vwap"] = ind.vwap_by_day(p)
    p["vol_avg"] = p["volume"].rolling(20, min_periods=10).mean()
    p["n"] = p.groupby("date").cumcount()
    if daily is not None and len(daily) >= 60:
        b = _daily_bias(daily)
        p["bias_up"] = p["date"].map(b["bias_up"]).fillna(False).astype(bool)
        p["bias_dn"] = p["date"].map(b["bias_dn"]).fillna(False).astype(bool)
    else:
        p["bias_up"] = False
        p["bias_dn"] = False
    return p


def _prep_daily(df: pd.DataFrame) -> pd.DataFrame:
    p = df.copy()
    p["atr"] = ind.atr(p, 14)
    p["adx"] = ind.adx(p, 14)
    p["sma5"], p["sma50"], p["sma200"] = ind.sma(p["close"], 5), ind.sma(p["close"], 50), ind.sma(p["close"], 200)
    p["ema20"] = ind.ema(p["close"], 20)
    p["rsi2"] = ind.rsi(p["close"], 2)
    p["vol_avg"] = p["volume"].rolling(20, min_periods=10).mean()
    return p


class Strategy:
    name = "base"
    plain = ""
    mode = ""
    timeframe = "15m"
    t1_r, t2_r, trail_mult = 1.5, 3.0, 2.0
    max_hold = None
    desc = ""

    def applies(self, inst) -> bool:
        return True

    def prepare(self, df, daily=None):
        raise NotImplementedError

    def signal_at(self, p, i, inst):
        raise NotImplementedError

    def exit_check(self, p, i, trade):
        return None


# ---------------------------------------------------------------- intraday (15m) ----
class ORB15(Strategy):
    name, mode, timeframe = "ORB15", "intraday", "15m"
    plain = "Morning breakout"
    t1_r, t2_r, trail_mult = 1.0, 2.5, 2.0
    desc = "15-minute opening-range breakout; needs volume, VWAP side and daily trend to agree"

    def applies(self, inst):
        return inst["kind"] == "equity"

    def prepare(self, df, daily=None):
        p = _prep_intraday(df, daily)
        g = p.groupby("date")
        p["or_high"], p["or_low"] = g["high"].transform("first"), g["low"].transform("first")
        p["first_tod"] = g["tod"].transform("first")
        return p

    def signal_at(self, p, i, inst):
        if i < 30:
            return None
        r = p.iloc[i]
        if r["n"] < 1 or r["first_tod"] != time(9, 15) or r["tod"] > C.INTRADAY_LAST_ENTRY:
            return None
        atr = r["atr"]
        if not atr > 0:
            return None
        hi, lo = float(r["or_high"]), float(r["or_low"])
        if not (0.4 * atr <= hi - lo <= 3.5 * atr):
            return None
        c, pc = float(r["close"]), float(p["close"].iloc[i - 1])
        volr = r["volume"] / r["vol_avg"] if r["vol_avg"] > 0 else 0
        if volr < 1.2:
            return None
        score = round(min(100, 35 + 15 * min(volr, 3) + (10 if (hi - lo) < 2 * atr else 0)), 1)
        if c > hi and pc <= hi and c > r["vwap"] and r["bias_up"]:
            sl = lo if (c - lo) <= 2.5 * atr else (hi + lo) / 2
            sl = min(sl, c - 0.5 * atr)
            return Signal(self.name, self.mode, inst["yf"], inst["name"], "LONG", c, sl, self.timeframe, score,
                          f"Price just broke above the first 15 minutes' high ({hi:.2f}) with {volr:.1f}x the usual trading, and the bigger trend is up",
                          f"it falls back below {hi:.2f}",
                          p.index[i], {"or_high": hi, "or_low": lo, "session": "NSE"})
        if c < lo and pc >= lo and c < r["vwap"] and r["bias_dn"]:
            sl = hi if (hi - c) <= 2.5 * atr else (hi + lo) / 2
            sl = max(sl, c + 0.5 * atr)
            return Signal(self.name, self.mode, inst["yf"], inst["name"], "SHORT", c, sl, self.timeframe, score,
                          f"Price just broke below the first 15 minutes' low ({lo:.2f}) with {volr:.1f}x the usual trading, and the bigger trend is down",
                          f"it climbs back above {lo:.2f}",
                          p.index[i], {"or_high": hi, "or_low": lo, "session": "NSE"})
        return None

    def exit_check(self, p, i, trade):
        r, m = p.iloc[i], trade["meta"]
        if trade["side"] == "LONG" and r["close"] < m["or_high"] - 0.25 * r["atr"]:
            return "price fell back into the morning range (the breakout failed)"
        if trade["side"] == "SHORT" and r["close"] > m["or_low"] + 0.25 * r["atr"]:
            return "price climbed back into the morning range (the breakout failed)"
        return None


class VWAPPullback(Strategy):
    name, mode, timeframe = "VWAP_PULLBACK", "intraday", "15m"
    plain = "Dip-buy in a strong day"
    t1_r, t2_r, trail_mult = 1.0, 2.5, 2.0
    desc = "Trend-day pullback to EMA20/VWAP, entered on a reversal bar in the direction of the daily trend"

    def applies(self, inst):
        return inst["kind"] == "equity"

    def prepare(self, df, daily=None):
        return _prep_intraday(df, daily)

    def signal_at(self, p, i, inst):
        if i < 40:
            return None
        r, q = p.iloc[i], p.iloc[i - 1]
        if r["n"] < 4 or r["tod"] > C.INTRADAY_LAST_ENTRY:
            return None
        atr, c = r["atr"], float(r["close"])
        if not atr > 0:
            return None
        slope = r["ema20"] - p["ema20"].iloc[i - 3]
        low3, high3 = float(p["low"].iloc[i - 2:i + 1].min()), float(p["high"].iloc[i - 2:i + 1].max())
        if (r["bias_up"] and c > r["vwap"] and c > r["ema20"] and slope > 0 and q["low"] <= q["ema20"] + 0.3 * atr
                and c > q["high"] and c > r["open"]):
            sl = low3 - 0.1 * atr
            if c - sl > 2 * atr:
                return None
            sl = min(sl, c - 0.4 * atr)
            return Signal(self.name, self.mode, inst["yf"], inst["name"], "LONG", c, sl, self.timeframe, 55.0,
                          "The stock dipped back to its short-term average on a rising day and has bounced up again; the bigger trend is up",
                          "it drops below the day's average price", p.index[i], {"session": "NSE"})
        if (r["bias_dn"] and c < r["vwap"] and c < r["ema20"] and slope < 0 and q["high"] >= q["ema20"] - 0.3 * atr
                and c < q["low"] and c < r["open"]):
            sl = high3 + 0.1 * atr
            if sl - c > 2 * atr:
                return None
            sl = max(sl, c + 0.4 * atr)
            return Signal(self.name, self.mode, inst["yf"], inst["name"], "SHORT", c, sl, self.timeframe, 55.0,
                          "The stock rose back to its short-term average on a falling day and has turned down again; the bigger trend is down",
                          "it climbs above the day's average price", p.index[i], {"session": "NSE"})
        return None

    def exit_check(self, p, i, trade):
        r = p.iloc[i]
        if trade["side"] == "LONG" and r["close"] < r["vwap"] and r["close"] < r["ema20"]:
            return "price dropped below the day's average"
        if trade["side"] == "SHORT" and r["close"] > r["vwap"] and r["close"] > r["ema20"]:
            return "price rose above the day's average"
        return None


# ---------------------------------------------------------------- swing (1D) --------
class Donchian20(Strategy):
    name, mode, timeframe = "DONCHIAN20", "swing", "1d"
    plain = "20-day high breakout"
    t1_r, t2_r, trail_mult, max_hold = 2.0, 5.0, 3.0, 25
    desc = "20-day high breakout in a confirmed uptrend (above 200-day, 50>200) with volume and ADX"

    def applies(self, inst):
        return inst["kind"] == "equity"

    def prepare(self, df, daily=None):
        p = _prep_daily(df)
        p["hh20"] = p["high"].rolling(20, min_periods=20).max().shift(1)
        return p

    def signal_at(self, p, i, inst):
        if i < 210:
            return None
        r = p.iloc[i]
        c, atr = float(r["close"]), r["atr"]
        if not atr > 0 or not (0.008 <= atr / c <= 0.06):
            return None
        if (c > r["hh20"] and c > r["sma200"] and r["sma50"] > r["sma200"] and r["adx"] > 20
                and r["vol_avg"] > 0 and r["volume"] >= 1.2 * r["vol_avg"] and (c - r["hh20"]) <= atr):
            return Signal(self.name, self.mode, inst["yf"], inst["name"], "LONG", c, c - 2 * atr, self.timeframe,
                          round(min(100, 40 + r["adx"]), 1),
                          f"Closed at a new 20-day high ({r['hh20']:.2f}) on {r['volume'] / r['vol_avg']:.1f}x the usual trading, in a stock that is in a long-term uptrend",
                          f"it closes back below {r['hh20'] - 0.5 * atr:.2f} within 5 days",
                          p.index[i], {"level": float(r["hh20"]), "session": "NSE"})
        return None

    def exit_check(self, p, i, trade):
        r = p.iloc[i]
        lvl = trade["meta"].get("level")
        if lvl is not None and trade.get("bars_held", 0) <= 5 and r["close"] < lvl - 0.5 * r["atr"]:
            return "the breakout failed (it closed back below the breakout level)"
        return None


class RSI2Pullback(Strategy):
    name, mode, timeframe = "RSI2_PULLBACK", "swing", "1d"
    plain = "Quick dip-buy in an uptrend"
    t1_r, t2_r, trail_mult, max_hold = 1.5, 3.0, 2.0, 7
    desc = "Short-term oversold dip (RSI-2 < 10) inside a long-term uptrend; exits when price recovers above the 5-day average"

    def applies(self, inst):
        return inst["kind"] == "equity"

    def prepare(self, df, daily=None):
        return _prep_daily(df)

    def signal_at(self, p, i, inst):
        if i < 210:
            return None
        r = p.iloc[i]
        c, atr = float(r["close"]), r["atr"]
        if not atr > 0:
            return None
        if c > r["sma200"] and r["rsi2"] < 10 and c < r["sma5"]:
            return Signal(self.name, self.mode, inst["yf"], inst["name"], "LONG", c, c - 2 * atr, self.timeframe,
                          round(60 + (10 - r["rsi2"]), 1),
                          "A sharp 1-2 day dip in a stock that is in a long-term uptrend; these often bounce back within days",
                          "I will tell you to sell once it bounces back", p.index[i], {"session": "NSE"})
        return None

    def exit_check(self, p, i, trade):
        r = p.iloc[i]
        if r["close"] > r["sma5"] or r["rsi2"] > 70:
            return "the dip has bounced back - time to take the profit"
        return None


class MomentumResume(Strategy):
    name, mode, timeframe = "MOMENTUM_RESUME", "swing", "1d"
    plain = "Strong stock restarts after a dip"
    t1_r, t2_r, trail_mult, max_hold = 2.0, 5.0, 3.0, 30
    desc = "Strong 6-month leader near its 52-week high that pulled back to EMA20 and resumed (10-day high)"

    def applies(self, inst):
        return inst["kind"] == "equity"

    def prepare(self, df, daily=None):
        p = _prep_daily(df)
        p["hh10"] = p["high"].rolling(10, min_periods=10).max().shift(1)
        p["ll10"] = p["low"].rolling(10, min_periods=10).min()
        p["hh252"] = p["high"].rolling(252, min_periods=200).max()
        p["ret126"] = p["close"] / p["close"].shift(126) - 1
        p["touch"] = (p["low"] <= p["ema20"] * 1.01).rolling(7, min_periods=7).max()
        return p

    def signal_at(self, p, i, inst):
        if i < 252:
            return None
        r = p.iloc[i]
        c, atr = float(r["close"]), r["atr"]
        if not atr > 0:
            return None
        if (c > r["sma50"] > r["sma200"] and r["ret126"] > 0.15 and c >= 0.92 * r["hh252"]
                and r["touch"] == 1 and c > r["hh10"]):
            sl = max(c - 3 * atr, r["ll10"] - 0.2 * atr)
            sl = min(sl, c - 0.8 * atr)
            return Signal(self.name, self.mode, inst["yf"], inst["name"], "LONG", c, sl, self.timeframe,
                          round(min(100, 50 + r["ret126"] * 100 / 2), 1),
                          f"A strong stock (up {r['ret126'] * 100:.0f}% in 6 months, near its 52-week high) dipped a little and is now rising again",
                          "it closes below its 50-day average price", p.index[i], {"session": "NSE"})
        return None

    def exit_check(self, p, i, trade):
        r = p.iloc[i]
        return "it closed below its 50-day average price" if r["close"] < r["sma50"] else None


# ---------------------------------------------------------------- options (underlying, 15m) --
class OptTrend(Strategy):
    name, mode, timeframe = "OPT_TREND", "option", "15m"
    plain = "Index / crude trend (options)"
    t1_r, t2_r, trail_mult = 1.5, 3.0, 2.0
    desc = "EMA9/21 cross with ADX and daily-trend agreement on the index/crude chart; trade ATM CE (up) or PE (down)"

    def applies(self, inst):
        return inst["kind"] in ("index", "commodity")

    def prepare(self, df, daily=None):
        return _prep_intraday(df, daily)

    def signal_at(self, p, i, inst):
        if i < 60:
            return None
        r = p.iloc[i]
        lo_t, hi_t = C.OPTION_WINDOW[inst["session"]]
        if not (lo_t <= r["tod"] <= hi_t):
            return None
        atr, c = r["atr"], float(r["close"])
        if not atr > 0 or not r["adx"] > 20:
            return None
        e9, e21 = p["ema9"], p["ema21"]
        up_x = e9.iloc[i] > e21.iloc[i] and (e9.iloc[i - 1] <= e21.iloc[i - 1] or e9.iloc[i - 2] <= e21.iloc[i - 2])
        dn_x = e9.iloc[i] < e21.iloc[i] and (e9.iloc[i - 1] >= e21.iloc[i - 1] or e9.iloc[i - 2] >= e21.iloc[i - 2])
        step = inst.get("step", 50)
        strike = int(round(c / step) * step)
        meta = {"session": inst["session"]}
        if up_x and c > r["ema50"] and r["bias_up"]:
            sl = float(p["low"].iloc[i - 5:i + 1].min())
            sl = min(max(sl, c - 2 * atr), c - 0.8 * atr)
            return Signal(self.name, self.mode, inst["yf"], inst["name"], "LONG", c, sl, self.timeframe,
                          round(min(100, 40 + r["adx"]), 1),
                          "The short-term trend just turned up and is strong, and the bigger trend is up too",
                          "the short-term trend turns back down", p.index[i], meta, {"type": "CE", "strike": strike})
        if dn_x and c < r["ema50"] and r["bias_dn"]:
            sl = float(p["high"].iloc[i - 5:i + 1].max())
            sl = max(min(sl, c + 2 * atr), c + 0.8 * atr)
            return Signal(self.name, self.mode, inst["yf"], inst["name"], "SHORT", c, sl, self.timeframe,
                          round(min(100, 40 + r["adx"]), 1),
                          "The short-term trend just turned down and is strong, and the bigger trend is down too",
                          "the short-term trend turns back up", p.index[i], meta, {"type": "PE", "strike": strike})
        return None

    def exit_check(self, p, i, trade):
        r = p.iloc[i]
        if trade["side"] == "LONG" and r["ema9"] < r["ema21"]:
            return "the short-term trend flipped down"
        if trade["side"] == "SHORT" and r["ema9"] > r["ema21"]:
            return "the short-term trend flipped up"
        return None


class Manual(Strategy):
    """Used to manage trades you log yourself: stop, T1 -> breakeven, ATR trail, end-of-day rule."""
    name, mode = "MANUAL", "intraday"
    plain = "Your own trade"

    def __init__(self, mode="intraday"):
        self.mode = mode
        self.timeframe = "1d" if mode == "swing" else "15m"
        self.max_hold = 40 if mode == "swing" else None

    def prepare(self, df, daily=None):
        p = df.copy()
        p["atr"] = ind.atr(p, 14)
        return p


ALL = [ORB15(), VWAPPullback(), Donchian20(), RSI2Pullback(), MomentumResume(), OptTrend()]
BY_NAME = {s.name: s for s in ALL}


def strategy_for(trade: dict) -> Strategy:
    return BY_NAME.get(trade.get("strategy")) or Manual(trade.get("mode", "intraday"))
