"""Performance maths shared by the bot (auto-mute) and the dashboard (scorecard). Light imports only."""
from __future__ import annotations
import math
from . import config as C


def perf(rs: list[float]) -> dict:
    rs = [float(x) for x in rs if x is not None and x == x]
    n = len(rs)
    if n == 0:
        return dict(n=0, win_rate=None, avg_r=None, total_r=0.0, pf=None, max_dd=0.0, t_stat=0.0, avg_win=None,
                    avg_loss=None, payoff=None, verdict="No trades yet")
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    avg = sum(rs) / n
    sd = math.sqrt(sum((x - avg) ** 2 for x in rs) / (n - 1)) if n > 1 else 0.0
    t = avg / (sd / math.sqrt(n)) if sd > 0 else 0.0
    cum, peak, dd = 0.0, 0.0, 0.0
    for x in rs:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    gl = abs(sum(losses))
    pf = (sum(wins) / gl) if gl > 0 else None
    aw = sum(wins) / len(wins) if wins else 0.0
    al = abs(sum(losses) / len(losses)) if losses else 0.0
    return dict(n=n, win_rate=len(wins) / n * 100, avg_r=avg, total_r=sum(rs), pf=pf, max_dd=dd, t_stat=t,
                avg_win=aw, avg_loss=al, payoff=(aw / al if al > 0 else None), verdict=verdict(n, avg, t))


def verdict(n: int, avg_r: float, t_stat: float) -> str:
    if n < 30:
        return f"Too early ({n}/30 trades)"
    if avg_r <= 0:
        return "Not working"
    if t_stat >= 1.65:
        return "Edge likely"
    return "Unproven"


def mute_status(closed_rows: list[dict], overrides: dict | None = None) -> dict:
    """{(strategy, mode): 'ACTIVE'|'MUTED'} from the last MUTE_WINDOW closed system trades per strategy."""
    overrides = overrides or {}
    groups: dict[tuple, list] = {}
    for r in closed_rows:            # rows are expected newest-first
        groups.setdefault((r["strategy"], r["mode"]), []).append(r["r_multiple"])
    out = {}
    for key, rs in groups.items():
        recent = [x for x in rs[:C.MUTE_WINDOW] if x is not None]
        muted = len(recent) >= C.MUTE_MIN_TRADES and (sum(recent) / len(recent)) < C.MUTE_AVG_R
        out[key] = "MUTED" if muted else "ACTIVE"
    for name, flag in overrides.items():
        for key in list(out) + [(name, m) for m in ("intraday", "swing", "option")]:
            if key[0] == name:
                out[key] = "MUTED" if flag else "ACTIVE"
    return out
