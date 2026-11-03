"""Telegram input. Normal use is just tapping the buttons under each message. Typed commands are optional extras.
Only messages from YOUR chat id are obeyed."""
from __future__ import annotations
import datetime as dt
from .universe import normalize_symbol, display_name, inst_for
from . import actions, stats, rating as R, settings

HELP = ("<b>You mostly don't need to type anything.</b> Tap the buttons under each message.\n\n"
        "Optional extras:\n"
        "/pause - stop NEW trade ideas (I still warn you about trades you're in)\n"
        "/resume - start ideas again\n"
        "/trades - list your open trades\n"
        "/score - how well each idea type is doing\n"
        "/sold 12 2990 - record that you sold trade #12 at 2990\n"
        "/bought RELIANCE 2950 2925 - log a trade I didn't suggest (buy price, then stop-loss)\n\n"
        "I check Telegram every 15 minutes, so replies can take up to 15 minutes. "
        "The dashboard shows everything instantly.")


def _num(tok):
    try:
        return float(tok.replace(",", "").replace("₹", ""))
    except ValueError:
        return None


def scorecard_text(db, now) -> str:
    since = now - dt.timedelta(days=120)
    rows = db.select("trades", [("status", "eq", "CLOSED"), ("source", "eq", "system"), ("closed_at", "gte", since)])
    g = {}
    for r in rows:
        g.setdefault((r["strategy"], r["mode"]), []).append(r["r_multiple"])
    if not g:
        return "No practice trades have finished yet. Give it a few weeks - I'll show results here."
    from .strategies import BY_NAME
    out = ["<b>How each idea type is doing</b> (last 4 months, including ideas I held back)"]
    for (s, m), rs in sorted(g.items()):
        rt = R.rate(rs, None)
        plain = BY_NAME[s].plain if s in BY_NAME else s
        out.append(f"{rt['emoji']} {plain}: {rt['text']}")
    return "\n".join(out)


def handle_callback(db, data: str, now) -> str:
    action, _, tid = data.partition(":")
    if not tid.isdigit():
        return ""
    if action == "took":
        return actions.took(db, int(tid), now)
    if action == "skip":
        return actions.skip(db, int(tid))
    if action == "sold":
        return actions.sold(db, int(tid), None, now)
    return ""


def handle(db, text: str, now: dt.datetime) -> str:
    parts = text.strip().split()
    if not parts:
        return ""
    cmd = parts[0].lower().split("@")[0]
    args = parts[1:]
    if cmd in ("/start", "/help"):
        return HELP
    if cmd in ("/pause", "/resume"):
        s = db.kv_get("settings", {}) or {}
        s["paused"] = cmd == "/pause"
        db.kv_set("settings", s)
        return "⏸ Paused. No new trade ideas until you send /resume." if cmd == "/pause" else "▶️ Back on. I'll send ideas again."
    if cmd == "/trades":
        rows = db.select("trades", [("status", "eq", "OPEN"), ("source", "eq", "user")])
        if not rows:
            return "You have no open trades."
        return "\n".join(f"#{r['id']} {r['name']} - bought at {r['entry']:g}, stop-loss {r['sl']:g}" for r in rows)
    if cmd == "/score":
        return scorecard_text(db, now)
    if cmd == "/sold":
        if len(args) < 1 or _num(args[0]) is None:
            return "Usage: /sold 12 2990  (trade number, then the price you sold at)"
        price = _num(args[1]) if len(args) > 1 else None
        return actions.sold(db, int(_num(args[0])), price, now)
    if cmd in ("/bought", "/open"):
        if len(args) < 3:
            return "Usage: /bought RELIANCE 2950 2925  (name, buy price, stop-loss)"
        sym = normalize_symbol(args[0])
        side = "SHORT" if (len(args) > 1 and args[1].upper() in ("SHORT", "SELL")) else "LONG"
        nums = [x for x in map(_num, args[1:]) if x is not None]
        words = [a.lower() for a in args[1:] if _num(a) is None]
        if len(nums) < 2:
            return "Usage: /bought RELIANCE 2950 2925  (name, buy price, stop-loss)"
        entry, sl = nums[0], nums[1]
        if (side == "LONG" and sl >= entry) or (side == "SHORT" and sl <= entry):
            return "The stop-loss must be below your buy price (or above it, for a sell-first trade)."
        inst = inst_for(sym)
        mode = next((w for w in words if w in ("intraday", "swing", "option")), "option" if inst["kind"] != "equity" else "intraday")
        risk, d = abs(entry - sl), (1 if side == "LONG" else -1)
        qty = nums[3] if len(nums) > 3 else None
        row = dict(source="user", strategy="MANUAL", symbol=sym, name=display_name(sym), mode=mode,
                   timeframe="1d" if mode == "swing" else "15m", side=side, entry=entry, initial_sl=sl, sl=sl,
                   t1=nums[2] if len(nums) > 2 else entry + d * 1.5 * risk, t2=entry + d * 3 * risk, qty=qty, status="OPEN",
                   t1_hit=False, best=entry, bars_held=0, taken=True, tracked=True,
                   meta={"session": inst["session"], "open_date": str(now.date()), "via": "telegram"})
        r = db.insert("trades", row)
        return f"✅ Logged <b>#{r['id']}</b> {display_name(sym)} at {entry:g}, stop-loss {sl:g}. I'll watch it and tell you when to move the stop-loss or leave."
    return "I didn't understand that. Send /help to see what I can do."


def process(db, tg, now, chat_id):
    offset = db.kv_get("tg_offset", None)
    ups = tg.get_updates(offset)
    last, n = offset, 0
    for u in ups:
        last = u["update_id"] + 1
        cq = u.get("callback_query")
        if cq:
            msg = cq.get("message") or {}
            if str((msg.get("chat") or {}).get("id")) != str(chat_id):
                continue
            try:
                reply = handle_callback(db, cq.get("data", ""), now)
            except Exception as e:
                reply = f"Sorry, that didn't work ({type(e).__name__})."
            tg.answer_callback(cq.get("id", ""), "Done")
            if reply:
                tg.send(reply)
            n += 1
            continue
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != str(chat_id):
            continue
        txt = m.get("text") or ""
        if txt.startswith("/"):
            try:
                reply = handle(db, txt, now)
            except Exception as e:
                reply = f"Sorry, that didn't work ({type(e).__name__})."
            if reply:
                tg.send(reply)
            n += 1
    if last != offset:
        db.kv_set("tg_offset", last)
    return n
