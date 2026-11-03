"""Price data from Yahoo Finance (free). One batched call per timeframe keeps request counts low."""
from __future__ import annotations
import time as _time
from datetime import time
import pandas as pd
from .clock import IST

COLS = ["open", "high", "low", "close", "volume"]


def _retry(fn, tries=3):
    last = None
    for k in range(tries):
        try:
            return fn()
        except Exception as e:  # network / rate limit
            last = e
            _time.sleep(1.5 * (k + 1))
    print(f"data: giving up after {tries} tries ({type(last).__name__})")
    return None


def _clean(df: pd.DataFrame, intraday: bool) -> pd.DataFrame:
    df = df.rename(columns=lambda c: str(c).lower())
    df = df[[c for c in COLS if c in df.columns]].copy()
    df = df.dropna(subset=["close", "open", "high", "low"])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if intraday:
        df.index = df.index.tz_convert(IST) if df.index.tz is not None else df.index.tz_localize("UTC").tz_convert(IST)
    else:
        idx = df.index.tz_localize(None) if df.index.tz is not None else df.index
        df.index = idx.normalize()
    df["volume"] = df["volume"].fillna(0) if "volume" in df else 0
    return df


def download(symbols, interval: str, period: str) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        return {}
    raw = _retry(lambda: yf.download(symbols, period=period, interval=interval, group_by="ticker",
                                     auto_adjust=False, progress=False, threads=True))
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    intraday = interval not in ("1d", "5d", "1wk", "1mo")
    if isinstance(raw.columns, pd.MultiIndex):
        names = set(raw.columns.get_level_values(0))
        for s in symbols:
            if s in names:
                d = raw[s].dropna(how="all")
                if len(d):
                    out[s] = _clean(d, intraday)
    else:
        out[symbols[0]] = _clean(raw.dropna(how="all"), intraday)
    return out


def drop_incomplete_intraday(df: pd.DataFrame, minutes: int, now) -> pd.DataFrame:
    """Keep only bars that have fully closed by `now` (the newest bar is usually still forming)."""
    if df is None or df.empty:
        return df
    end = df.index + pd.Timedelta(minutes=minutes)
    return df[end <= pd.Timestamp(now)]


def drop_incomplete_daily(df: pd.DataFrame, now, session: str = "NSE") -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if df.index[-1] >= pd.Timestamp(now.date()):
        if session == "NSE" and now.time() >= time(15, 40):
            return df
        return df.iloc[:-1]
    return df


def lag_minutes(df: pd.DataFrame, minutes: int, now) -> float | None:
    if df is None or df.empty:
        return None
    return (pd.Timestamp(now) - (df.index[-1] + pd.Timedelta(minutes=minutes))).total_seconds() / 60


def last_prices(symbols, now=None) -> dict[str, float]:
    """Latest 5-minute close per symbol (the 'fresh price' used to confirm an entry)."""
    got = download(symbols, "5m", "2d")
    out = {}
    for s, d in got.items():
        if now is not None:
            d = d[d.index <= pd.Timestamp(now)]
        if len(d):
            out[s] = float(d["close"].iloc[-1])
    return out
