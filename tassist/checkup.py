"""One-click setup check. Run it from GitHub: Actions -> checkup -> Run workflow.
It tests every connection and sends you a Telegram message in plain words (or, if Telegram itself is the problem,
the GitHub log says so). Prints only OK/FAILED lines - never secrets."""
from __future__ import annotations
import datetime as dt
import sys
from . import config as C, data, news, settings, clock
from .db import SupabaseDB, MemoryDB
from .telegram import Telegram

TABLES = ("kv", "trades", "signals", "news", "runs", "backtests")


def main() -> int:
    dry = C.env("DRY_RUN") == "1"
    url, key = C.env("SUPABASE_URL"), C.env("SUPABASE_SERVICE_KEY")
    db = MemoryDB() if dry else (SupabaseDB(url, key) if (url and key) else None)
    tg = Telegram(C.env("TELEGRAM_BOT_TOKEN"), C.env("TELEGRAM_CHAT_ID"), dry=dry)
    rows: list[tuple[bool, str, str]] = []
    info: list[str] = []

    def check(label, fn, fix):
        try:
            ok = bool(fn())
        except Exception as e:
            ok = False
            print(f"   ({label}: {type(e).__name__})")
        rows.append((ok, label, "" if ok else fix))
        print(("OK     " if ok else "FAILED ") + label)

    def secrets_present():
        return db is not None

    check("Secrets are filled in (database address + key)", secrets_present,
          "In GitHub: Settings > Secrets and variables > Actions, add SUPABASE_URL and SUPABASE_SERVICE_KEY exactly as in the guide (Part 4).")
    if db is not None:
        def db_rw():
            db.kv_set("checkup", {"ts": dt.datetime.now(dt.timezone.utc).isoformat()})
            return db.kv_get("checkup") is not None
        check("Database connection works", db_rw,
              "The Supabase address or key is wrong, or you forgot to run the schema.sql step. Re-copy both from Supabase > Project Settings > API (Part 2 and 4).")
        for tname in TABLES:
            check(f"Database table '{tname}' exists", lambda t=tname: db.select(t, limit=1) is not None,
                  "Open Supabase > SQL Editor, paste all of supabase/schema.sql and press Run (Part 2).")
    check("Telegram can message you", lambda: tg.send("🔧 <b>Setup check</b> - if you can read this, Telegram is connected."),
          "Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Make sure you sent your bot a message first (Part 3).")
    check("Market prices are available (Yahoo)", lambda: len(data.download(["^NSEI"], "1d", "1y").get("^NSEI", [])) > 100,
          "Usually temporary. Wait 10 minutes and run this check again.")
    check("Intraday prices are available", lambda: len(data.download(["RELIANCE.NS"], "15m", "5d").get("RELIANCE.NS", [])) > 0,
          "Usually temporary. Wait 10 minutes and run this check again (no data is normal on weekends).")
    check("News feeds are reachable", lambda: len(news.fetch_feeds()) > 0,
          "Usually temporary. The assistant still works without news.")
    if db is not None:
        def clock_seen():
            last = db.kv_get("last_tick")
            return bool(last) and (clock.now_ist() - dt.datetime.fromisoformat(last["ts"])).total_seconds() < 1800
        ok = False
        try:
            ok = clock_seen()
        except Exception:
            pass
        info.append("🕒 Automatic 15-minute clock: " + ("running ✅" if ok else "not seen yet (normal until you finish Part 6 of the guide)"))
        print("INFO   automatic clock " + ("running" if ok else "not seen yet"))

    failed = [r for r in rows if not r[0]]
    L = ["🔧 <b>Setup check</b>"]
    for ok, label, fix in rows:
        L.append(f"{'✅' if ok else '❌'} {label}")
        if fix:
            L.append(f"     → {fix}")
    L += info
    L.append("")
    L.append("🎉 <b>Everything is connected.</b> You can now relax: I'll message you when there's something to do." if not failed
             else f"⚠️ {len(failed)} thing(s) need attention (see above).")
    tg.send("\n".join(L))
    print(f"\n{len(rows) - len(failed)} of {len(rows)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
