"""Settings the user changes from the dashboard (stored in the database, not in code)."""
from . import config as C

DEFAULTS = dict(paused=False, capital=C.CAPITAL, risk_pct=C.RISK_PCT, send_intraday=True, send_option=True,
                send_swing=True, send_momentum=True, only_good=False)


def load(db) -> dict:
    s = dict(DEFAULTS)
    try:
        s.update(db.kv_get("settings", {}) or {})
    except Exception:
        pass
    return s


def risk_amount(cfg) -> float:
    return float(cfg["capital"]) * float(cfg["risk_pct"]) / 100
