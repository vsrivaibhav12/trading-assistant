# Trading assistant (free, serverless)

Watches NSE stocks, Nifty / Bank Nifty and crude every 15 minutes and sends plain-English Telegram alerts with
tap-buttons; paper-trades every idea to measure what really works; sends a monthly momentum stock list; and shows
the results on a private, plain-language dashboard.

- **Setup (once):** `SETUP_GUIDE.md`.  **Daily use:** `DAILY_GUIDE.md` (3 steps).
- **Check your setup:** GitHub Actions -> `checkup` -> Run workflow (reports on Telegram in plain words).
- **Layers:** `news.py`, `scanner.py`, `data.py` (information) -> `strategies.py`, `rotation.py`, `decision.py`, `rating.py` (decisions)
  -> `engine.py` (trade lifecycle) -> `run_tick.py` (orchestrator) -> Telegram + Supabase + `dashboard/app.py`.
- **Test it:** `python -m tassist.selftest` (offline, 100+ checks including no-lookahead tests).
- **Re-test strategies on history:** `python -m tassist.backtest all` (also runs monthly by itself).
- **Add a strategy:** write a class in `tassist/strategies.py` (`prepare`, `signal_at`, `exit_check`, `plain`) and append it to `ALL`.

Decision-support only. You place every order. Nothing here is proven to be profitable; the ratings exist to find out.
