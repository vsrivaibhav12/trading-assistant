"""Everything the user reads. Rules: rupees not "R", everyday words not jargon, one clear action per message."""
from __future__ import annotations
import html as _html
from . import config as C
from .engine import risk_of, is_long

def e(x):
    return _html.escape(str(x), quote=False)


MOOD = {"UP": "strong 📈", "DOWN": "weak 📉", "RANGE": "sideways ↔️", "UNKNOWN": "unclear"}


def money(x, signed=False):
    s = f"₹{abs(x):,.0f}"
    return (("+" if x >= 0 else "-") + s) if signed else s


def level(x, inst, fx=None):
    if inst.get("fx") and fx:
        return f"₹{x * fx:,.0f} (WTI ${x:,.2f})"
    if inst["kind"] == "index":
        return f"{x:,.0f}"
    return f"₹{x:,.2f}"


def _pnl(t, price):
    q = t.get("qty")
    if not q:
        return ""
    p = (price - t["entry"]) * q * (1 if is_long(t) else -1)
    return f" - about {money(abs(p))} {'profit' if p >= 0 else 'loss'}"


def fmt_signal(sig, strat, t, inst, qty, rating, news_head, warns, fx=None, cfg=None):
    long = sig.side == "LONG"
    risk = risk_of(t)
    lv = lambda x: level(x, inst, fx)
    name = e(sig.name)
    if sig.option:
        kind = "CALL" if long else "PUT"
        head = f"{'🟢' if long else '🔴'} <b>BUY {name} {sig.option['strike']} {kind}</b>  (idea #{t['id']})"
        what = f"You make money if {name} goes {'up' if long else 'down'}. Same-day trade."
    elif long:
        head = f"🟢 <b>BUY {name}</b>  (idea #{t['id']})"
        what = "Short-term trade, for a few days to weeks." if sig.mode == "swing" else "Same-day trade."
    else:
        head = f"🔴 <b>SELL {name} first, buy back later</b>  (idea #{t['id']})"
        what = "Same-day trade only (sell now, buy back before the market closes)."
    L = [head, f"<i>{e(strat.plain)}. {what}</i>", "",
         f"<b>Confidence:</b> {rating['emoji']} {rating['label']} - {e(rating['text'])}"]
    if rating["key"] in ("UNPROVEN",):
        L.append("🧪 <b>Practice idea - please don't use real money on this yet.</b>")
    L.append("")
    if sig.mode == "swing":
        L.append(f"<b>Buy tomorrow</b> near the opening price (today's close was {lv(t['entry'])}). "
                 f"Skip it if it opens above {lv(t['entry'] + 0.5 * risk)} or below {lv(t['entry'] - 0.5 * risk)}.")
    elif sig.option:
        L.append(f"<b>Buy</b> while {name} is around {lv(t['entry'])} (skip if it has already moved more than "
                 f"{abs(0.5 * risk):,.0f} points).")
    else:
        L.append(f"<b>{'Buy' if long else 'Sell'}</b> around {lv(t['entry'])} (skip if it has already moved past "
                 f"{lv(t['entry'] + 0.5 * risk)} or {lv(t['entry'] - 0.5 * risk)}).")
    L.append(f"<b>Stop-loss:</b> {'sell' if long else 'buy back'} if it reaches {lv(t['initial_sl'])}" +
             (f" (applies to {name}, not the option price)" if sig.option else ""))
    if qty:
        L.append(f"<b>Suggested size:</b> {qty} shares - if the stop-loss is hit you lose about {money(qty * risk)}.")
    L += [f"<b>First target:</b> {lv(t['t1'])} - then I'll tell you to move your stop-loss to your {'buy' if long else 'sell'} price.",
          f"<b>Final target:</b> {lv(t['t2'])} - {'sell' if long else 'buy back'} everything."]
    if sig.timeframe == "15m":
        L.append(f"<b>Finish by:</b> {C.EOD_EXIT[sig.meta.get('session', 'NSE')].strftime('%-I:%M %p').lower()} today.")
    elif strat.max_hold:
        L.append(f"<b>Time limit:</b> if nothing has happened in {strat.max_hold} trading days, I'll tell you to sell.")
    L += ["", f"<b>Why:</b> {e(sig.reason)}.", f"<b>I'll tell you to leave early if:</b> {e(sig.invalid)}."]
    if news_head:
        L.append(f"<b>In the news:</b> {e(news_head[:140])}")
    for w in warns:
        L.append(f"⚠️ {e(w)}")
    L += ["", "<i>Tap a button so I know. If you ignore this, I'll keep sending updates in case you took it.</i>"]
    return "\n".join(L)


def signal_buttons(tid):
    return [[("✅ I bought it", f"took:{tid}"), ("⏭ Skip this one", f"skip:{tid}")]]


def fmt_event(t, ev, inst=None, fx=None):
    inst = inst or {"kind": "equity"}
    lv = lambda x: level(x, inst, fx)
    name, long = e(t["name"]), is_long(t)
    typ = ev["type"]
    if typ == "T1":
        return (f"🎯 <b>{name}: first target reached</b>\nMove your stop-loss to <b>{lv(ev['new_sl'])}</b> "
                f"(your {'buy' if long else 'sell'} price). From here this trade can't lose money. "
                f"You can also {'sell' if long else 'buy back'} half now to lock in some profit.")
    if typ == "TRAIL":
        return f"↗️ <b>{name}</b> is moving your way.\nMove your stop-loss {'up' if long else 'down'} to <b>{lv(ev['new_sl'])}</b>."
    act = "SELL" if long else "BUY BACK"
    pnl = _pnl(t, ev["price"])
    r = ev["reason"]
    if r == "SETUP_LOST":
        return (f"⚠️ <b>{act} {name} now</b>\nThe reason for this trade no longer holds: {e(ev.get('why', ''))}. "
                f"It's best to leave now (price about {lv(ev['price'])}{pnl}).")
    if r in ("STOP", "TRAIL_STOP"):
        return f"⛔ <b>{name}: stop-loss reached</b> at {lv(ev['price'])}{pnl}.\n{act.capitalize()} if you haven't already."
    if r == "TARGET":
        return f"🏁 <b>{name}: final target reached</b> ({lv(ev['price'])}{pnl}).\n{act.capitalize()} everything."
    return f"⏰ <b>{act} {name} now</b>\n{e(ev.get('why', 'time is up'))}. Price about {lv(ev['price'])}{pnl}."


def sold_buttons(tid):
    return [[("✅ I've sold it", f"sold:{tid}")]]


def fmt_heartbeat(now, regime, n_watch, learning, open_names, note=""):
    L = [f"☀️ <b>Good morning</b> - {now.strftime('%A %d %B')}",
         f"Market mood: <b>{MOOD.get(regime, regime)}</b>",
         f"I'm watching {n_watch} stocks plus Nifty, Bank Nifty and crude oil. I'll message you only when there is a trade worth "
         f"taking - usually 0 to 2 a day."]
    if learning:
        L.append("🧪 I'm still learning which ideas work. Ideas marked 🧪 are for practice only.")
    if open_names:
        L.append("Trades I'm still watching for you: " + ", ".join(open_names))
    if note:
        L.append(note)
    return "\n".join(L)


def fmt_summary(now, sent_today, closed_today, risk_amt, open_names):
    net = sum((r.get("r_multiple") or 0) for r in closed_today) * risk_amt
    L = [f"🌙 <b>Today in short</b> - {now.strftime('%d %B')}",
         f"Ideas I sent you: {sent_today}",
         f"Trades that finished: {len(closed_today)}" +
         (f" - net about <b>{money(net, True)}</b> if you followed each idea with the suggested size" if closed_today else "")]
    L.append("Still open: " + (", ".join(open_names) if open_names else "none"))
    return "\n".join(L)


def fmt_rotation(month_label, ok, buys, sells, keeps, rating, cfg, n_target):
    L = [f"📅 <b>Monthly stock list - {month_label}</b>"]
    if not ok:
        L += ["Nifty is below its 200-day average price, which is a weak market. My rule says: <b>stay in cash</b>.",
              ("SELL all: " + ", ".join(sells)) if sells else "Nothing to buy right now. I'll check again next month."]
    else:
        L.append("Nifty is above its 200-day average price ✅ - a healthy market to hold stocks.")
        if buys:
            L.append("🟢 <b>BUY (new this month):</b> " + ", ".join(buys))
        if sells:
            L.append("🔴 <b>SELL (dropped out):</b> " + ", ".join(sells))
        if keeps:
            L.append("⚪ <b>KEEP:</b> " + ", ".join(keeps))
        if cfg.get("capital"):
            L.append(f"Put roughly equal money in each: about {money(cfg['capital'] / n_target)} per stock if you invest {money(cfg['capital'])}.")
        L.append("Why: these are the stocks that have risen the most, most smoothly, over the past 6 to 12 months.")
    L += ["", f"<b>Confidence:</b> {rating['emoji']} {rating['label']} - {e(rating['text'])}",
          "⚠️ Momentum can fall sharply when the market turns, and short-term gains are taxed - check before selling anything you've held under a year."]
    return "\n".join(L)
