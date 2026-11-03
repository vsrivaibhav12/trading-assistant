"""Settings. Secrets come from environment variables (GitHub Secrets); nothing sensitive lives in the repo."""
import os
from datetime import time


def env(name, default=None):
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


# --- your numbers (set as GitHub *Variables*, never in code) -------------------------
CAPITAL = float(env("CAPITAL", "100000"))      # trading capital in INR, used only for position sizing
RISK_PCT = float(env("RISK_PCT", "1.0"))       # % of capital risked per trade

# --- round-trip cost as % of entry price. Check against a real contract note! --------
COST_PCT = {"intraday": 0.06, "swing": 0.25, "option": 0.05}

# --- scanning / timing (IST) ---------------------------------------------------------
TOP_N_INTRADAY = 15                            # stage-1 scanner shortlists this many stocks for 15m chart reads
INTRADAY_LAST_ENTRY = time(14, 30)
OPTION_WINDOW = {"NSE": (time(9, 45), time(14, 45)), "MCX": (time(9, 30), time(22, 30))}
EOD_EXIT = {"NSE": time(15, 15), "MCX": time(23, 15)}

# --- alert hygiene -------------------------------------------------------------------
MAX_ALERTS_PER_TICK = 4
MAX_ALERTS_PER_DAY = 12
MAX_OPEN_SHADOW = 80
SWING_DEDUPE_DAYS = 5

# --- self-muting: a strategy with a bad recent record stops alerting (still tracked) --
MUTE_MIN_TRADES = 30
MUTE_WINDOW = 30
MUTE_AVG_R = -0.05

# --- misc ----------------------------------------------------------------------------
NEWS_EVERY_MIN = 30
STALE_MIN = 75            # data older than this during a session is treated as stale
DUP_TICK_SEC = 240        # ignore a second tick that starts within this many seconds
