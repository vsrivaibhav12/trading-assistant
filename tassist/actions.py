"""What a button tap (Telegram or dashboard) does. Light imports only so the dashboard can use it."""
from __future__ import annotations
from . import engine

COPY_KEYS = ("strategy", "symbol", "name", "mode", "timeframe", "side", "entry", "initial_sl", "sl", "t1", "t2", "qty",
             "status", "t1_hit", "best", "bars_held", "last_bar")


def _get(db, tid):
    rows = db.select("trades", [("id", "eq", int(tid))])
    return rows[0] if rows else None


def took(db, tid, now=None) -> str:
    t = _get(db, tid)
    if not t:
        return "I couldn't find that idea."
    if t["source"] == "user" or (t.get("meta") or {}).get("user_copy"):
        return "Already tracking this as your trade."
    twin = {k: t.get(k) for k in COPY_KEYS if k in t}
    twin.update(source="user", taken=True, tracked=True, meta={**(t.get("meta") or {}), "from_signal": t["id"], "mine": True})
    row = db.insert("trades", twin)
    meta = dict(t.get("meta") or {})
    meta.update(muted_followups=True, user_copy=row["id"])
    db.update("trades", t["id"], {"meta": meta})
    return f"✅ Got it. I'm now tracking <b>{t['name']}</b> as YOUR trade and will tell you when to move the stop-loss or leave."


def skip(db, tid) -> str:
    t = _get(db, tid)
    if not t:
        return "I couldn't find that idea."
    meta = dict(t.get("meta") or {})
    meta.update(muted_followups=True, skipped=True)
    db.update("trades", t["id"], {"meta": meta})
    return f"👍 Skipped <b>{t['name']}</b>. I won't send updates on it."


def sold(db, tid, price=None, now=None) -> str:
    t = _get(db, tid)
    if not t or t["status"] != "OPEN" or t["source"] != "user":
        return "That trade is already closed."
    meta = t.get("meta") or {}
    px = price if price else (meta.get("exit_suggested") or {}).get("price")
    if not px:
        return "I need the price you sold at (for example: /sold 12 2990)."
    engine.close_trade(t, float(px), "MANUAL", now)
    db.update("trades", t["id"], {k: t[k] for k in ("status", "exit_price", "exit_reason", "closed_at", "r_multiple", "pnl_pct")})
    q = t.get("qty")
    sign = 1 if t["side"] == "LONG" else -1
    money = f" About ₹{abs((px - t['entry']) * q * sign):,.0f} {'profit' if (px - t['entry']) * sign >= 0 else 'loss'}." if q else ""
    return f"✅ Recorded: <b>{t['name']}</b> closed at {px:,.2f}.{money}"
