"""News layer: free RSS feeds -> keyword tags (+ optional Gemini batch tagging) -> `news` table.
The decision layer only ever sees the tags, never raw LLM opinions, so results stay testable."""
from __future__ import annotations
import json
import re
import datetime as dt
import requests
from . import config as C
from .universe import EQUITIES

GN = "https://news.google.com/rss/search?hl=en-IN&gl=IN&ceid=IN:en&q="
FEEDS = [
    ("GNews markets", GN + "NSE+Nifty+Sensex+stocks+when:1d"),
    ("GNews corporate", GN + "India+shares+results+OR+downgrade+OR+SEBI+OR+order+win+when:1d"),
    ("GNews crude", GN + "crude+oil+OPEC+MCX+when:1d"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    ("OilPrice", "https://oilprice.com/rss/main"),
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Mint", "https://www.livemint.com/rss/markets"),
]
RULES = [  # tag, polarity, regex
    ("REGULATORY_NEG", -1, r"\b(sebi (order|bans?|penalt\w*|probe)|fraud|raid(s|ed)?|insolvency|default(s|ed)?|probe|penalt(y|ies)|suspend(s|ed)?|delist\w*|resign(s|ed|ation)?)\b"),
    ("DOWNGRADE", -1, r"\b(downgrade[sd]?|cuts? target|sell rating|profit warning|misses? estimates|slump(s|ed)?|plunge[sd]?|tumble[sd]?)\b"),
    ("UPGRADE", 1, r"\b(upgrade[sd]?|raises? target|buy rating|beats? estimates|surge[sd]?|jump(s|ed)?|rall(y|ies|ied)|record high|wins? order|bags? order)\b"),
    ("EARNINGS", 0, r"\b(q[1-4] (results|earnings|profit)|quarterly results|net profit|earnings)\b"),
    ("BLOCK_DEAL", 0, r"\b(block deal|bulk deal|stake sale)\b"),
    ("MACRO", 0, r"\b(rbi|repo rate|inflation|cpi|gdp|fomc|tariffs?)\b"),
    ("CRUDE", 0, r"\b(crude|opec|brent|wti)\b"),
]
_RX = [(t, p, re.compile(rx, re.I)) for t, p, rx in RULES]
_ALIAS = [(base, re.compile(r"\b" + re.escape(a) + r"\b", re.I)) for base, al in EQUITIES.items() for a in al]


def keyword_tag(title: str) -> dict:
    tags, pol = [], 0
    for tag, p, rx in _RX:
        if rx.search(title):
            tags.append(tag)
            pol += p
    syms = sorted({b for b, rx in _ALIAS if rx.search(title)})
    sent = "NEG" if pol < 0 else "POS" if pol > 0 else "NEUTRAL"
    return dict(tags=tags, sentiment=sent, symbols=syms)


def gemini_tag(items: list[tuple[int, str]]) -> dict[int, dict]:
    """One batched call. Returns {} on any problem so callers fall back to keywords."""
    key = C.env("GEMINI_API_KEY")
    if not key or not items:
        return {}
    model = C.env("GEMINI_MODEL", "gemini-2.5-flash")
    prompt = ("Tag Indian stock-market headlines. Return ONLY a JSON array of objects "
              '{"i":int,"sentiment":"POS|NEG|NEUTRAL","tags":[subset of REGULATORY_NEG,DOWNGRADE,UPGRADE,EARNINGS,BLOCK_DEAL,MACRO,CRUDE],'
              '"symbols":[NSE tickers from this list only: ' + ",".join(EQUITIES) + "]}. Sentiment is for the named company's stock.\n"
              + "\n".join(f"{i}. {t}" for i, t in items))
    try:
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          headers={"x-goog-api-key": key, "Content-Type": "application/json"}, timeout=40,
                          json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}})
        if r.status_code != 200:
            print(f"gemini: HTTP {r.status_code}, using keyword tags")
            return {}
        txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        arr = json.loads(re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip())
        return {int(o["i"]): o for o in arr if isinstance(o, dict) and "i" in o}
    except Exception as e:
        print(f"gemini: {type(e).__name__}, using keyword tags")
        return {}


def fetch_feeds() -> list[dict]:
    import feedparser
    out, ok = [], 0
    for name, url in FEEDS:
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (compatible; tassist/1.0)"})
            if r.status_code != 200:
                continue
            ok += 1
            for e in feedparser.parse(r.content).entries[:40]:
                title = re.sub(r"\s+", " ", (e.get("title") or "")).strip()
                link = e.get("link")
                if not title or not link:
                    continue
                t = e.get("published_parsed") or e.get("updated_parsed")
                pub = dt.datetime(*t[:6], tzinfo=dt.timezone.utc) if t else dt.datetime.now(dt.timezone.utc)
                out.append(dict(source=name, title=title[:300], link=link, published=pub))
        except Exception as ex:
            print(f"news: {name} failed ({type(ex).__name__})")
    print(f"news: {ok}/{len(FEEDS)} feeds ok, {len(out)} items")
    return out


def refresh(db) -> int:
    items = fetch_feeds()
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)
    seen = {r["link"] for r in db.select("news", [("published", "gte", since)], limit=2000)}
    fresh, seen_now = [], set()
    for it in sorted(items, key=lambda x: x["published"], reverse=True):
        if it["link"] in seen or it["link"] in seen_now or it["published"] < since:
            continue
        seen_now.add(it["link"])
        fresh.append(it)
    fresh = fresh[:60]
    llm = gemini_tag([(i, it["title"]) for i, it in enumerate(fresh[:40])])
    for i, it in enumerate(fresh):
        k = keyword_tag(it["title"])
        g = llm.get(i)
        if g:
            k["tags"] = sorted(set(k["tags"]) | set(g.get("tags") or []))
            k["sentiment"] = g.get("sentiment") if g.get("sentiment") in ("POS", "NEG", "NEUTRAL") else k["sentiment"]
            k["symbols"] = sorted(set(k["symbols"]) | {s for s in (g.get("symbols") or []) if s in EQUITIES})
        it.update(k)
    if fresh:
        try:
            db.insert("news", fresh)
        except Exception as e:
            print(f"news: insert failed ({str(e)[:80]})")
            return 0
    return len(fresh)


def recent(db, hours=24) -> list[dict]:
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    return db.select("news", [("published", "gte", since)], order="published.desc", limit=400)


def flags_for(rows: list[dict], base_symbol: str) -> dict:
    """Summarise recent headlines for one stock: negative / positive / earnings flags + the top headline."""
    mine = [r for r in rows if base_symbol in (r.get("symbols") or [])]
    neg = any(r.get("sentiment") == "NEG" for r in mine)
    pos = any(r.get("sentiment") == "POS" for r in mine)
    earn = any("EARNINGS" in (r.get("tags") or []) for r in mine)
    head = mine[0]["title"] if mine else None
    return dict(neg=neg, pos=pos, earnings=earn, headline=head, count=len(mine))
