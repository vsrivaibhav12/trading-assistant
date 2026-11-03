"""Market clock in IST. All "what should run now" logic lives here so it can be tested."""
from __future__ import annotations
import os
from datetime import datetime, time, date
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    o = os.environ.get("NOW_IST")          # replay/testing hook: NOW_IST="2026-11-02 10:32"
    if o:
        return datetime.fromisoformat(o).replace(tzinfo=IST)
    return datetime.now(IST)


def holidays() -> set[str]:
    p = Path(__file__).resolve().parents[1] / "holidays.txt"
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.add(line)
    return out


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in holidays()


def plan(now: datetime) -> dict:
    """Which jobs are allowed to run at this moment."""
    td = is_trading_day(now.date())
    t = now.time()

    def within(a, b):
        return td and time(*a) <= t <= time(*b)

    return {
        "trading_day": td,
        "heartbeat": td and t >= time(8, 30),
        "news": within((8, 30), (23, 45)),
        "nse_scan": within((9, 30), (15, 10)),
        "nse_manage": within((9, 15), (15, 45)),
        "mcx_scan": within((9, 15), (22, 45)),
        "mcx_manage": within((9, 0), (23, 45)),
        "after_close": td and t >= time(15, 45),       # swing scan/manage + daily summary
        "weekly": td and now.weekday() == 4 and t >= time(23, 15),
    }
