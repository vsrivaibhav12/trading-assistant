"""Plain-language confidence rating for each strategy, built from LIVE paper results first and history second.
  PROVEN    30+ live paper trades, positive average, and statistically distinguishable from luck
  PROMISING positive but not yet proven (live), or positive on history only
  UNPROVEN  not enough evidence either way
  WEAK      evidence says it loses money -> its ideas are NOT sent (still tracked silently, so it can earn its way back)
History alone can never make a strategy PROVEN: only live results can."""
from __future__ import annotations
from .stats import perf

LABELS = {
    "PROVEN": ("🟢", "Proven in live practice"),
    "PROMISING": ("🟡", "Looks promising"),
    "UNPROVEN": ("⚪", "Too early to tell"),
    "WEAK": ("🔴", "Not working"),
}


def rate(forward_rs: list, backtest: dict | None) -> dict:
    f = perf(forward_rs)
    b = backtest or {}
    bn, bavg, bt = b.get("trades") or 0, b.get("avg_r"), b.get("t_stat") or 0
    if f["n"] >= 30:
        key = "WEAK" if f["avg_r"] <= 0 else ("PROVEN" if f["t_stat"] >= 1.65 else "PROMISING")
        basis, st = "live practice", f
    elif bn >= 100 and bavg is not None:
        key = "WEAK" if bavg < -0.03 else ("PROMISING" if (bavg > 0.03 and bt >= 1.0) else "UNPROVEN")
        basis = "past history"
        st = dict(n=bn, win_rate=b.get("win_rate"), payoff=b.get("payoff"), avg_r=bavg, avg_win=b.get("avg_win_r"),
                  avg_loss=b.get("avg_loss_r"))
    elif bn > 0 and bavg is not None:
        key, basis = "UNPROVEN", "past history"
        st = dict(n=bn, win_rate=b.get("win_rate"), payoff=b.get("payoff"), avg_r=bavg, avg_win=b.get("avg_win_r"),
                  avg_loss=b.get("avg_loss_r"))
    else:
        key, basis, st = "UNPROVEN", "no results yet", (f if f["n"] else None)
    emoji, label = LABELS[key]
    return dict(key=key, emoji=emoji, label=label, basis=basis, stats=st, live_n=f["n"], text=describe(key, basis, st))


def describe(key, basis, st) -> str:
    if not st or not st.get("n") or st.get("win_rate") is None:
        return "no track record yet"
    win = f"wins about {st['win_rate']:.0f} of every 100 trades"
    pay = st.get("payoff")
    size = f"; a typical win is {pay:.1f}x a typical loss" if pay else ""
    few = " - too few trades to trust" if st["n"] < 30 else ""
    return f"{win}{size} ({st['n']} trades, {basis}{few})"


def best_per_mode(ratings: dict) -> dict:
    """{mode: strategy name} - highest-rated, then highest average, per mode."""
    order = {"PROVEN": 3, "PROMISING": 2, "UNPROVEN": 1, "WEAK": 0}
    out = {}
    for (name, mode), r in ratings.items():
        avg = (r["stats"] or {}).get("avg_r") or -9
        score = (order[r["key"]], avg)
        if mode not in out or score > out[mode][0]:
            out[mode] = (score, name)
    return {m: v[1] for m, v in out.items()}


def latest_backtests(db) -> dict:
    out = {}
    for r in sorted(db.select("backtests"), key=lambda x: str(x.get("run_at"))):
        out[(r["strategy"], r["mode"])] = r
    return out


def compute_ratings(db, closed=None) -> dict:
    """{(strategy, mode): rating} from live practice trades + the latest history test."""
    from .strategies import ALL
    if closed is None:
        closed = db.select("trades", [("status", "eq", "CLOSED"), ("source", "eq", "system")], order="closed_at.desc", limit=800)
    bt = latest_backtests(db)
    out = {}
    for s in ALL:
        rs = [r["r_multiple"] for r in closed if r["strategy"] == s.name and r["mode"] == s.mode and r["r_multiple"] is not None]
        out[(s.name, s.mode)] = rate(rs, bt.get((s.name, s.mode)))
    return out
