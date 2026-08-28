# PL Predictor Bot

A Telegram bot for you and your friend's weekly Premier League score-prediction game.
Everything is free: the bot code, the SQLite storage (just a local file, no database
service needed), and the fixture/result data.

## Rules it enforces
- Whoever's "turn" it is predicts first each gameweek; the turn **alternates every
  gameweek automatically**.
- The second predictor **cannot pick the exact same scoreline** as the first.
- Scoring: **1 point** for correctly picking the winner (or draw), **3 points** for
  the exact scoreline (not stacked on top of the 1 — getting the exact score already
  means you got the winner right).
- A running total is kept forever in `predictor.db`.

## 1. Get a Telegram bot token (free)
1. Message [@BotFather](https://t.me/BotFather) on Telegram.
2. Send `/newbot`, follow the prompts, and copy the token it gives you.
3. Add the bot to your group chat.

## 2. Get a free football-data.org API key
1. Sign up at https://www.football-data.org/client/register — free tier is enough
   (Premier League is included, rate limit is 10 requests/minute).
2. Copy your API token from your account page.

## 3. Get a free Neon Postgres database
1. Sign up at https://neon.tech (free tier is plenty for this — two players, a
   handful of rows per gameweek).
2. Create a project (any name/region).
3. On the project dashboard, copy the **connection string** — it looks like
   `postgresql://user:password@ep-xxxx.region.aws.neon.tech/dbname?sslmode=require`.
4. That's your `DATABASE_URL`. The bot creates its own tables automatically the
   first time it runs — nothing else to set up in Neon.

## 4. Set up the project
```bash
cd pl-predictor-bot
python -m venv venv
source venv/bin/activate      # on Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```
Edit `.env` and paste in your bot token, API key, and Neon connection string.

## 5. Run it
```bash
python bot.py
```
Leave this running — it needs to stay online to receive Telegram messages and to
auto-check for finished matches every 30 minutes.

## 6. Using it in your group
1. Both of you send `/start` in the group (registers you as the two players — a
   third person can't register).
2. Whoever wants to kick things off runs `/newgameweek` — it pulls that matchday's
   fixtures and tells you whose turn it is to predict first.
3. Pick the match you're competing on with `/setmatch <number>`.
4. First predictor runs `/predict 2-1` (any score). Second predictor runs `/predict`
   with anything except that exact score.
5. After the match finishes, the bot auto-scores it (or run `/results` to check
   immediately). Check the table anytime with `/table`, or `/history` for the full log.

Full command list: `/help`

## 7. The web app (optional but recommended)
There's also a mobile-friendly Telegram Mini App (`webapp.py`) with a scoreboard for
predicting, a live standings table, and full history — nicer than typing commands.
It runs as its **own** small web service, separate from the bot, sharing the same
Neon database.

### Deploy it (Render, free)
1. On Render: New → Web Service → same GitHub repo, but:
   - **Build command**: `pip install -r requirements.txt`
   - **Start command**: `gunicorn webapp:app`
   - **Environment variables**: `TELEGRAM_BOT_TOKEN`, `FOOTBALL_DATA_API_KEY`,
     `DATABASE_URL` — the same values as the bot service.
2. Deploy. Copy the URL Render gives this service (e.g.
   `https://pl-predict-web.onrender.com`).
3. On the **bot** service, add an environment variable `WEBAPP_URL` set to that URL
   (no trailing slash), and redeploy the bot.

That's two free Render services total — the bot and the web app — both hitting the
same database, so nothing needs to be kept in sync manually.

### Telegram's HTTPS requirement
Mini Apps only launch from `https://` URLs, which Render gives you automatically —
nothing extra to configure there.

### Open it
- In the group chat, send `/app` — the bot replies with an "Open PL Predictor"
  button.
- In a private chat with the bot, the app is also available from Telegram's
  built-in menu button (bottom-left, next to the message box). Groups don't
  support that button for web apps, which is why `/app` is the primary way in.

### First-time setup note
The web app figures out which chat to read/write by remembering the last chat
that ran `/start` — so run `/start` in your group (as described in step 6) before
using the web app for the first time.

## Keeping it running for free (no server costs)
Telegram bots need to run continuously, so pick one of these — all $0:
- **Simplest**: run it on a machine that's already on, like a spare laptop, desktop,
  or a Raspberry Pi at home, using `tmux`/`screen` or a systemd service so it survives
  reboots and logouts.
- **Free-tier cloud VM**: Oracle Cloud's "Always Free" tier or similar give you a
  small VM free forever — install Python, copy the project over, run it the same way.
  This avoids the "spins down when idle" problem that free web-hosting tiers
  (Render, Railway, etc.) have.
- **Render.com free tier**: works, with two caveats — see below.

### Deploying to Render (free tier)
Render's free web services sleep after 15 minutes of no traffic, and only wake up
when they receive an HTTP request. The bot now supports **webhook mode** so an
incoming Telegram message is what wakes it (the very first message after a sleep
may take a few seconds longer).

1. Push this repo to GitHub (see main instructions above).
2. On Render: New → Web Service → connect your GitHub repo.
3. Settings:
   - **Build command**: `pip install -r requirements.txt`
   - **Start command**: `python bot.py`
   - **Environment variables**: `TELEGRAM_BOT_TOKEN`, `FOOTBALL_DATA_API_KEY`,
     `DATABASE_URL` (your Neon connection string), and `WEBHOOK_URL` set to your
     Render service's URL (e.g. `https://pl-predict.onrender.com` — no trailing
     slash). Render sets `PORT` automatically, you don't need to add it.
4. Deploy. Once it's live, send `/start` in your Telegram group — that request wakes
   the service and registers the webhook.

Since points now live in Neon rather than a local file, they persist across Render
restarts and redeploys — the earlier ephemeral-storage caveat no longer applies.

## Notes
- All data lives in Neon Postgres — the bot and the web app share it, so nothing
  needs manual syncing between them.
- `game.py` holds the scoring/turn rules shared by the bot and the web app, so the
  two front ends can never disagree on them.
- If you'd rather predict on a specific match every week (e.g. always a Saturday
  3pm kickoff) rather than picking from the list, just always pick the same fixture
  number — the alternating-turn and no-duplicate-score logic works the same either way.
