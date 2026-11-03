# One-time setup guide

You do this **once**. It takes about 60 to 90 minutes. After that, your daily routine is: read Telegram and tap a button when it asks you to (see `DAILY_GUIDE.md`). There is nothing to install on your computer, nothing to keep switched on, and no credit card anywhere.

Website screens change from time to time, so a button may be labelled a little differently from what is written here. If you get stuck on any step, stop, take a screenshot, and ask the person helping you. Nothing here can break anything, and you can repeat any step.

**Before you start, have these ready**
- Your email address
- Your phone with the Telegram app installed
- A notes file (or paper) to jot down a few values as you go. Look for the 📝 marks.

**What you are setting up (just so the names make sense)**

| Name | What it is for | Cost |
|---|---|---|
| GitHub | Holds the assistant's program and runs it | Free |
| Supabase | The assistant's notebook (remembers ideas, trades, results) | Free |
| Telegram bot | How the assistant messages you | Free |
| cron-job.org | An alarm clock that wakes the assistant every 15 minutes | Free |
| Streamlit | The webpage where you see everything | Free |

---

## Part 1: GitHub (the home of the program)

1. Go to **github.com** and create a free account.
2. Click the **+** at the top right, then **New repository**.
3. Name it `trading-assistant`. Choose **Public**. Leave everything else as it is. Click **Create repository**.
   *(Public is fine. Your keys and your trades are never stored in it.)*
4. On your computer, **unzip** `trading_assistant.zip`. Open the unzipped folder.
5. Back on GitHub, on the new empty repository page, click **uploading an existing file**.
6. Select **everything inside** the unzipped folder and drag it into the browser. Include the folder called `.github`.
   - On a Mac, folders starting with a dot are hidden. Press **Cmd + Shift + .** (full stop) in Finder to show them.
7. Scroll down and click **Commit changes**.
8. Click the **Actions** tab at the top. If GitHub asks, click the green button to enable workflows.

✅ **You should see:** four items on the left: `backtest`, `checkup`, `selftest`, `tick`.

9. Click `selftest`, then **Run workflow** (the grey button on the right), then the green **Run workflow**. Wait about 1 minute and refresh.

✅ **You should see:** a green tick. (If you see a red cross, send me the words on the failed line.)

## Part 2: Supabase (the notebook)

1. Go to **supabase.com** and sign up. Click **New project**.
2. Choose any name. Make up a database password and **📝 save it** (you will not need it again). Choose a region near you (Mumbai if offered). Click **Create new project** and wait a couple of minutes.
3. In the left menu click **SQL Editor**, then **New query**.
4. On your computer, open the file `supabase/schema.sql` (any text editor). Select all the text, copy it, paste it into the Supabase box, and click **Run**.

✅ **You should see:** the word "Success".

5. Click the **gear icon (Project Settings)**, then **API** (it may be called **API Keys**).
6. **📝 Copy and save two things:**
   - **Project URL** (looks like `https://abcdefgh.supabase.co`)
   - The **secret key** (also called `service_role`). It is long and starts with `eyJ` or `sb_secret_`. **Do not** use the one called "anon" or "publishable".

Treat the secret key like a bank PIN. Never post it anywhere except in the boxes this guide tells you to.

## Part 3: Telegram (how it messages you)

1. In Telegram, search for **@BotFather** (it has a blue tick). Send it: `/newbot`
2. Give your bot any name, then a username that ends in `bot`.
3. BotFather replies with a long **token** (like `123456789:ABCdef...`). **📝 Save it.**
4. Search for your new bot by its username, open it, press **Start**, and send it the message `hi`.
5. On your computer, open this web address, putting your token in place of `PASTE-TOKEN-HERE`:
   `https://api.telegram.org/botPASTE-TOKEN-HERE/getUpdates`
6. Find the part that looks like `"chat":{"id":123456789`. **📝 Save that number**: it is your **chat ID**.

Keep the token private. Your bot only ever obeys messages from your own chat.

## Part 4: Give GitHub your four values

1. In GitHub, open your `trading-assistant` repository. Click **Settings** (top row), then on the left **Secrets and variables**, then **Actions**.
2. Click **New repository secret**. Add these four, one at a time. The *name* must be exactly as shown (capitals, underscores). Paste the value with no spaces or quotation marks.

| Name | Value |
|---|---|
| `SUPABASE_URL` | the Project URL from Part 2 |
| `SUPABASE_SERVICE_KEY` | the secret key from Part 2 |
| `TELEGRAM_BOT_TOKEN` | the token from Part 3 |
| `TELEGRAM_CHAT_ID` | the chat ID number from Part 3 |

*(Optional, skip if unsure: `GEMINI_API_KEY`, a free key from aistudio.google.com that helps sort news. It works fine without.)*

## Part 5: Check that everything is connected

1. Go to the **Actions** tab, click **checkup**, then **Run workflow**, then the green **Run workflow**.
2. Wait about a minute. **Look at Telegram.**

✅ **You should see:** a message called "Setup check" with ✅ next to each line. If a line has ❌, the message tells you in plain words which part to redo. Fix it and run **checkup** again. You can run it as many times as you like.

*(The line about the "automatic 15-minute clock" will say "not seen yet". That is normal until Part 6.)*

## Part 6: The alarm clock (cron-job.org)

This part has two steps: make a safe key, then set the alarm.

**6a. Make a key that can only start the assistant**
1. In GitHub, click your **profile picture** (top right), **Settings**, then at the very bottom of the left menu **Developer settings**, then **Personal access tokens**, then **Fine-grained tokens**, then **Generate new token**.
2. Name: `alarm`. Expiration: pick the longest. **📝 Write the expiry date in your phone calendar** (when it expires, alerts stop until you make a new one).
3. Under **Repository access** choose **Only select repositories** and pick `trading-assistant`.
4. Under **Permissions**, open **Repository permissions**, find **Actions** and set it to **Read and write**. Leave everything else alone.
5. Click **Generate token**. **📝 Copy it now** (it starts with `github_pat_`). You cannot see it again.

**6b. Set the alarm**
1. Go to **cron-job.org**, sign up free, and click **Create cronjob**.
2. **URL:** (replace YOUR-GITHUB-USERNAME)
   `https://api.github.com/repos/YOUR-GITHUB-USERNAME/trading-assistant/actions/workflows/tick.yml/dispatches`
3. **Schedule:** choose a **custom** schedule: minutes `2, 17, 32, 47`; hours `8` to `23`; days **Monday to Friday**. Set the time zone to **Asia/Kolkata**.
4. Open **Advanced** and set:
   - Request method: **POST**
   - Add these four headers (name = value):
     - `Authorization` = `Bearer github_pat_...` (your token from 6a, with the word Bearer and a space)
     - `Accept` = `application/vnd.github+json`
     - `X-GitHub-Api-Version` = `2022-11-28`
     - `Content-Type` = `application/json`
   - Request body: `{"ref":"main"}`
5. Turn on **notify me on failure**. Save.
6. Click **Test run**.

✅ **You should see:** a success result (the number **204**), and a new `tick` run appearing in GitHub's Actions tab within seconds.

Now run **checkup** again (Part 5). The clock line should say **running ✅**.

## Part 7: The webpage (Streamlit)

1. Go to **share.streamlit.io** and sign in with GitHub.
2. Click **Create app**, choose your `trading-assistant` repository, branch `main`, and for **Main file path** type `dashboard/app.py`.
3. Open **Advanced settings**, find **Secrets**, and paste this (with your real values and a password you invent):
   ```
   SUPABASE_URL = "https://abcdefgh.supabase.co"
   SUPABASE_SERVICE_KEY = "your-secret-key"
   APP_PASSWORD = "choose-a-password"
   ```
4. Click **Deploy**. The first build takes a few minutes.
5. **Make it private:** open the app, click the **⋮** menu, **Settings**, **Sharing**, choose *Only specific people can view this app*, and add your own email.
6. Open the **⚙️ Settings** page in the app and enter **how much money you trade with** and **how much you are willing to lose on one trade** (1% is a careful choice). Click **Save settings**.
7. **On your phone:** open the app's address in your browser and use *Add to Home Screen* so it opens like an app.

If the page ever shows "this app has gone to sleep", just press the wake-up button. Nothing is lost.

## Part 8: What to expect (please read)

- **The assistant starts in learning mode.** Every idea is tracked as a *practice trade*, including ideas it decides not to send you, so it can measure honestly what works. Ideas from approaches that are not yet proven carry a 🧪 label: **practice only, do not use real money yet.**
- **You will not hear from it often.** Most days are 0 to 2 messages. A quiet day means nothing looked worth it, not that it is broken (the morning message confirms it is alive).
- **Plan on 6 to 8 weeks of practice** before putting real money behind any idea. Check **📊 Is it working?** now and then. 🟢 means proven in live practice, 🟡 looks promising, ⚪ too early to tell, 🔴 not working (those ideas are not sent).
- **Today's starting picture** (from tests on past prices; these are *history*, not promises):
  - 🔴 Not working, so silent: *20-day high breakout* and *Dip-buy in a strong day*.
  - ⚪ Too early to tell: *Morning breakout*, *Quick dip-buy in an uptrend*, *Strong stock restarts after a dip*, and *Index / crude trend (options)*. Their ideas arrive marked 🧪.
  - 🟡 *Monthly stock list*: rated on published research, not on my own flattering test.
- When you decide to use real money, open **⚙️ Settings** and tick *Only send ideas from approaches that look good*.

## Part 9: If something stops working

**Step 1, always: run `checkup`** (Part 5). It tells you in plain words what is wrong.

| What you notice | What it usually is |
|---|---|
| No morning message on a weekday | The alarm clock stopped. Open cron-job.org and look at the last result. Most often your GitHub key (Part 6a) has **expired**: make a new one and replace it in the Authorization header |
| Dashboard says "I haven't checked the market for N minutes" | Same as above |
| Message "Something went wrong behind the scenes" once or twice | Usually a temporary glitch. If it keeps coming, run `checkup` |
| Taps on buttons seem slow | Normal: I read your taps every 15 minutes. The webpage buttons are instant |
| Dashboard asleep | Press the wake-up button |
| No ideas for days | Normal in weak or sideways markets, or while ideas are still unproven. Look at **🔍 Details → All ideas** to see what was held back and why |

## Part 10: Honest limits

- **Nothing here is proven to make money.** The ideas are well-known ones; the tests so far say most do not clearly work on Indian stocks after costs. The point of the assistant is to find that out safely, with practice trades, before your money is at risk.
- **The monthly stock list** is the idea with the best published evidence (NSE's own momentum index has beaten the Nifty 200 by roughly 5% a year since 2005, partly backtested), but it can fall sharply in bad markets and short-term gains are taxed. My own history test of it looks far too good because it uses today's surviving big companies, so ignore those numbers.
- **Checks happen every 15 minutes**, so a fast move can pass your stop-loss between checks. When you buy, **also place a stop-loss order with your broker**, and use my messages to move it.
- **Prices come free from Yahoo Finance.** They can be delayed or briefly missing; the assistant skips a check (and tells you) if the prices look old. Compare a few prices with your broker in the first week.
- **Crude oil:** Yahoo has US crude, not the Indian MCX contract. Messages show an approximate rupee equivalent. Always confirm the price with your broker.
- **Options:** I tell you which call or put to buy and when to leave, based on the *index or crude price*. I do not see the option's own price, so check it and its liquidity yourself.
- **Costs and holidays:** estimated costs are in `tassist/config.py`; exchange holidays go in `holidays.txt` (the assistant also notices days with no prices).
- **Free services can change their rules.** GitHub's terms for automated jobs are a grey area for a light job like this. The worst realistic outcome is the workflow being switched off. Your records stay safe in Supabase and I can move it elsewhere.
- **Not tested here:** the live connections to your own accounts (Supabase, Telegram, cron-job.org, GitHub, Streamlit). `checkup` verifies them for you.
- **You place every order.** This is a helper, not advice.
