import os
import re
import logging

from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto,
    WebAppInfo, MenuButtonWebApp,
)
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

import db
import api
import game
from game import clamp, player_name, allowed_predictor, determine_starter

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

SCORE_RE = re.compile(r"^\s*(\d+)\s*[-:]\s*(\d+)\s*(wildcard)?\s*$", re.IGNORECASE)
WEBAPP_URL = os.environ.get("WEBAPP_URL")


# ---------- inline keyboards ----------

def build_fixture_keyboard(fixtures):
    rows = []
    for i, m in enumerate(fixtures, start=1):
        home = m["homeTeam"]["shortName"] or m["homeTeam"]["name"]
        away = m["awayTeam"]["shortName"] or m["awayTeam"]["name"]
        rows.append([InlineKeyboardButton(f"{home} vs {away}", callback_data=f"setmatch:{i-1}")])
    return InlineKeyboardMarkup(rows)


def build_score_keyboard(home_name, away_name, h, a, wildcard):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⚽ {home_name}: {h}", callback_data="pred:noop")],
        [
            InlineKeyboardButton("-1", callback_data="pred:h-"),
            InlineKeyboardButton("+1", callback_data="pred:h+"),
        ],
        [InlineKeyboardButton(f"⚽ {away_name}: {a}", callback_data="pred:noop")],
        [
            InlineKeyboardButton("-1", callback_data="pred:a-"),
            InlineKeyboardButton("+1", callback_data="pred:a+"),
        ],
        [InlineKeyboardButton(f"🃏 Wildcard: {'ON' if wildcard else 'OFF'} (doubles your points)", callback_data="pred:wc")],
        [
            InlineKeyboardButton("✅ Submit", callback_data="pred:submit"),
            InlineKeyboardButton("❌ Cancel", callback_data="pred:cancel"),
        ],
    ])


def parse_score_keyboard(markup):
    """Reads current state off the keyboard's own button labels (rows 0/2/4 —
    see build_score_keyboard) rather than separate storage, so it survives
    bot restarts and needs no per-user state tracking."""
    rows = markup.inline_keyboard
    home_btn_text = rows[0][0].text
    away_btn_text = rows[2][0].text
    wc_btn_text = rows[4][0].text
    h = int(re.search(r": (\d+)$", home_btn_text).group(1))
    a = int(re.search(r": (\d+)$", away_btn_text).group(1))
    wildcard = "ON" in wc_btn_text
    return h, a, wildcard


# ---------- shared logic (used by both text commands and buttons) ----------

async def send_crests(bot, chat_id, gw, match=None):
    """Best-effort: post both club crests. Never blocks the main flow if it fails."""
    try:
        home_crest = away_crest = None
        if match is not None:
            home_crest = match["homeTeam"].get("crest")
            away_crest = match["awayTeam"].get("crest")
        if not home_crest or not away_crest:
            return
        await bot.send_media_group(
            chat_id=chat_id,
            media=[
                InputMediaPhoto(home_crest, caption=gw["home_team"]),
                InputMediaPhoto(away_crest, caption=gw["away_team"]),
            ],
        )
    except Exception:
        log.exception("Failed to send crest images")


async def lock_in_match(chat_id, idx, context, fixtures):
    if idx < 0 or idx >= len(fixtures):
        return None, "That number isn't in the list."

    match = fixtures[idx]
    home = match["homeTeam"]["shortName"] or match["homeTeam"]["name"]
    away = match["awayTeam"]["shortName"] or match["awayTeam"]["name"]

    gw = game.lock_in_match(
        chat_id=chat_id,
        gw_number=context.chat_data["gw_number"],
        match_id=match["id"],
        home=home,
        away=away,
        kickoff=match["utcDate"],
        starter_id=context.chat_data["starter_id"],
    )
    context.chat_data.pop("fixtures", None)
    return gw, None


async def prompt_prediction(bot, chat_id, gw, player_id, players):
    name = next(p["name"] for p in players if p["telegram_id"] == player_id)
    home = gw["home_team"]
    away = gw["away_team"]
    await bot.send_message(
        chat_id=chat_id,
        text=f"{name}, you're up — build your prediction for {home} vs {away}:",
        reply_markup=build_score_keyboard(home, away, 0, 0, False),
    )


async def submit_prediction(chat_id, user, pred_h, pred_a, wildcard, context):
    """Returns (ok, message, gw, players, next_player_id_or_None)."""
    result = game.submit_prediction(chat_id, user.id, user.first_name, pred_h, pred_a, wildcard)
    players = db.get_players()
    return result["ok"], result["message"], result["gw"], players, result["next_player"]


# ---------- commands ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Anchors the web app to this chat — the game only ever has one active
    # group, so the last chat to run /start is treated as "the" chat.
    db.set_setting("chat_id", str(update.effective_chat.id))
    result = db.add_player(user.id, user.first_name)
    if result == "added":
        await update.message.reply_text(f"You're in, {user.first_name}! Registered as a predictor.")
    elif result == "already":
        await update.message.reply_text("You're already registered.")
    else:
        await update.message.reply_text("This game already has two players registered.")


async def players_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    players = db.get_players()
    if not players:
        await update.message.reply_text("No one registered yet. Send /start to join.")
        return
    names = "\n".join(f"- {p['name']}" for p in players)
    await update.message.reply_text(f"Registered predictors:\n{names}")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [
        "/start - register yourself (max 2 players)",
        "/app - open the PL Predictor web app" if WEBAPP_URL else None,
        "/newgameweek [matchday] - fetch PL fixtures, tap one to pick it",
        "/predict - open the score keypad (or type /predict 2-1 [wildcard])",
        "/pending - see whose turn it is / current fixture",
        "/results - manually check if the current match has finished and score it",
        "/table - see the points standings",
        "/history - see past results and predictions",
        "",
        "🃏 Wildcard: toggle it on your prediction to double whatever points you earn. "
        "Renews every gameweek, no limit, each player chooses independently.",
    ]
    await update.message.reply_text("\n".join(l for l in lines if l is not None))


async def app_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WEBAPP_URL:
        await update.message.reply_text(
            "The web app isn't set up yet — set WEBAPP_URL in the environment first."
        )
        return
    await update.message.reply_text(
        "Open the app:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⚽ Open PL Predictor", web_app=WebAppInfo(url=WEBAPP_URL)),
        ]]),
    )


async def newgameweek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    players = db.get_players()
    if len(players) < 2:
        await update.message.reply_text("Need 2 registered players first — both of you send /start.")
        return

    if db.get_active_gameweek(chat_id):
        await update.message.reply_text("There's already an active gameweek. Finish it before starting a new one.")
        return

    matchday = int(context.args[0]) if context.args else None
    try:
        if matchday is None:
            matchday = api.get_current_matchday()
        matches = api.get_matches_for_matchday(matchday)
    except Exception as e:
        log.exception("API error")
        await update.message.reply_text(f"Couldn't fetch fixtures: {e}")
        return

    if not matches:
        await update.message.reply_text("No fixtures found for that matchday.")
        return

    last_gw = db.get_last_gameweek(chat_id)
    starter_id = determine_starter(players, last_gw, update.effective_user.id)

    context.chat_data["fixtures"] = matches
    context.chat_data["gw_number"] = matchday
    context.chat_data["starter_id"] = starter_id

    starter_name = next(p["name"] for p in players if p["telegram_id"] == starter_id)
    await update.message.reply_text(
        f"Matchday {matchday} fixtures — {starter_name} predicts first this week.\nTap one to lock it in:",
        reply_markup=build_fixture_keyboard(matches),
    )


async def on_setmatch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    fixtures = context.chat_data.get("fixtures")
    if not fixtures:
        await query.answer("This fixture list has expired — run /newgameweek again.", show_alert=True)
        return
    await query.answer()

    idx = int(query.data.split(":")[1])
    match = fixtures[idx] if 0 <= idx < len(fixtures) else None
    gw, err = await lock_in_match(chat_id, idx, context, fixtures)
    if err:
        await query.edit_message_text(err)
        return

    await query.edit_message_text(f"Locked in: {gw['home_team']} vs {gw['away_team']}.")
    await send_crests(context.bot, chat_id, gw, match)

    players = db.get_players()
    await prompt_prediction(context.bot, chat_id, gw, gw["starter_id"], players)


async def setmatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Text fallback for tapping a fixture button."""
    chat_id = update.effective_chat.id
    fixtures = context.chat_data.get("fixtures")
    if not fixtures:
        await update.message.reply_text("Run /newgameweek first to see the fixture list.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /setmatch <number>")
        return
    idx = int(context.args[0]) - 1
    match = fixtures[idx] if 0 <= idx < len(fixtures) else None
    gw, err = await lock_in_match(chat_id, idx, context, fixtures)
    if err:
        await update.message.reply_text(err)
        return

    await update.message.reply_text(f"Locked in: {gw['home_team']} vs {gw['away_team']}.")
    await send_crests(context.bot, chat_id, gw, match)

    players = db.get_players()
    await prompt_prediction(context.bot, chat_id, gw, gw["starter_id"], players)


async def predict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    players = db.get_players()

    if not any(p["telegram_id"] == user.id for p in players):
        await update.message.reply_text("You're not registered. Send /start first.")
        return

    gw = db.get_active_gameweek(chat_id)
    if not gw:
        await update.message.reply_text("No active fixture. Run /newgameweek then pick a match first.")
        return

    if not context.args:
        # No score typed — open the interactive keypad instead.
        preds = db.get_predictions(gw["id"])
        allowed = allowed_predictor(gw, preds, players)
        if allowed is None:
            await update.message.reply_text("Both predictions are already in for this match.")
            return
        if user.id != allowed:
            allowed_name = next(p["name"] for p in players if p["telegram_id"] == allowed)
            await update.message.reply_text(f"Not your turn — waiting on {allowed_name}.")
            return
        await prompt_prediction(context.bot, chat_id, gw, user.id, players)
        return

    m = SCORE_RE.match(" ".join(context.args))
    if not m:
        await update.message.reply_text("Couldn't parse that. Use a format like /predict 2-1 or /predict 2-1 wildcard")
        return
    pred_h, pred_a, wildcard = int(m.group(1)), int(m.group(2)), bool(m.group(3))

    ok, msg, gw, players, next_player = await submit_prediction(chat_id, user, pred_h, pred_a, wildcard, context)
    await update.message.reply_text(msg)
    if ok and next_player:
        await prompt_prediction(context.bot, chat_id, gw, next_player, players)


async def on_prediction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = update.effective_user
    action = query.data.split(":")[1]

    gw = db.get_active_gameweek(chat_id)
    if not gw:
        await query.answer("No active fixture right now.", show_alert=True)
        return
    players = db.get_players()
    preds = db.get_predictions(gw["id"])
    allowed = allowed_predictor(gw, preds, players)

    if allowed is None:
        await query.answer("Both predictions are already in for this match.", show_alert=True)
        return
    if user.id != allowed:
        allowed_name = next(p["name"] for p in players if p["telegram_id"] == allowed)
        await query.answer(f"Not your turn — waiting on {allowed_name}.", show_alert=True)
        return

    h, a, wildcard = parse_score_keyboard(query.message.reply_markup)
    home_name, away_name = gw["home_team"], gw["away_team"]

    if action == "noop":
        await query.answer()
        return
    if action == "cancel":
        await query.answer()
        await query.edit_message_text("Prediction cancelled. Run /predict to try again when you're ready.")
        return
    if action == "h+":
        h = clamp(h + 1)
    elif action == "h-":
        h = clamp(h - 1)
    elif action == "a+":
        a = clamp(a + 1)
    elif action == "a-":
        a = clamp(a - 1)
    elif action == "wc":
        wildcard = not wildcard
    elif action == "submit":
        await query.answer()
        ok, msg, gw, players, next_player = await submit_prediction(chat_id, user, h, a, wildcard, context)
        if ok:
            await query.edit_message_text(msg)
            if next_player:
                await prompt_prediction(context.bot, chat_id, gw, next_player, players)
        else:
            await query.edit_message_text(msg)
        return

    await query.answer()
    await query.edit_message_reply_markup(reply_markup=build_score_keyboard(home_name, away_name, h, a, wildcard))


async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    gw = db.get_active_gameweek(chat_id)
    if not gw:
        await update.message.reply_text("No active fixture right now. Run /newgameweek to start one.")
        return
    players = db.get_players()
    preds = db.get_predictions(gw["id"])
    msg = f"Current fixture: {gw['home_team']} vs {gw['away_team']} (GW{gw['gw_number']})\n"
    if len(preds) == 0:
        starter_name = next(p["name"] for p in players if p["telegram_id"] == gw["starter_id"])
        msg += f"Waiting on {starter_name} to predict first."
    elif len(preds) == 1:
        other = next(p for p in players if p["telegram_id"] != gw["starter_id"])
        msg += f"{player_name(preds[0], players)} predicted {preds[0]['pred_home']}-{preds[0]['pred_away']}. Waiting on {other['name']}."
    else:
        msg += "Both predictions are in, waiting on full time."
    await update.message.reply_text(msg)


async def check_and_score_gameweek(gw, bot):
    try:
        text = game.check_and_score_gameweek(gw)
    except Exception:
        log.exception("Failed to fetch match %s", gw["match_id"])
        return None

    if text is None:
        return None

    if bot is not None:
        try:
            await bot.send_message(chat_id=gw["chat_id"], text=text)
        except Exception:
            log.exception("Failed to announce result")
    return text


async def results_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    gw = db.get_active_gameweek(chat_id)
    if not gw or gw["status"] != "predicted":
        await update.message.reply_text("No fixture is fully predicted and awaiting a result right now.")
        return
    text = await check_and_score_gameweek(gw, context.bot)
    if text is None:
        await update.message.reply_text("Match hasn't finished yet — I'll keep checking automatically.")


async def auto_check_job(context: ContextTypes.DEFAULT_TYPE):
    for gw in db.predicted_gameweeks_awaiting_result():
        await check_and_score_gameweek(gw, context.bot)


async def table_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.leaderboard()
    if not rows:
        await update.message.reply_text("No points on the board yet.")
        return
    lines = ["Standings:"]
    for r in rows:
        lines.append(f"{r['name']}: {r['total']} pts")
    await update.message.reply_text("\n".join(lines))


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.full_history()
    if not rows:
        await update.message.reply_text("No finished gameweeks yet.")
        return
    lines = []
    last_gw = None
    for r in rows:
        if r["gw_number"] != last_gw:
            lines.append(f"\nGW{r['gw_number']}: {r['home_team']} {r['actual_home']}-{r['actual_away']} {r['away_team']}")
            last_gw = r["gw_number"]
        tag = " 🃏" if r["wildcard"] else ""
        lines.append(f"  {r['name']}: {r['pred_home']}-{r['pred_away']}{tag} (+{r['points']})")
    await update.message.reply_text("\n".join(lines))


async def post_init(app):
    # The chat "menu button" (bottom-left, next to the message box) only
    # supports web_app in private chats — Telegram doesn't allow it in
    # groups, so this is a bonus for whoever DMs the bot directly. In the
    # group itself, use /app to get a tappable button instead.
    if WEBAPP_URL:
        try:
            await app.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="Predictor", web_app=WebAppInfo(url=WEBAPP_URL))
            )
        except Exception:
            log.exception("Failed to set menu button")


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    db.init_db()

    app = ApplicationBuilder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("app", app_cmd))
    app.add_handler(CommandHandler("players", players_cmd))
    app.add_handler(CommandHandler("newgameweek", newgameweek))
    app.add_handler(CommandHandler("setmatch", setmatch))
    app.add_handler(CommandHandler("predict", predict))
    app.add_handler(CommandHandler("pending", pending))
    app.add_handler(CommandHandler("results", results_cmd))
    app.add_handler(CommandHandler("table", table_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CallbackQueryHandler(on_setmatch_callback, pattern=r"^setmatch:"))
    app.add_handler(CallbackQueryHandler(on_prediction_callback, pattern=r"^pred:"))

    app.job_queue.run_repeating(auto_check_job, interval=1800, first=30)

    webhook_url = os.environ.get("WEBHOOK_URL")
    if webhook_url:
        port = int(os.environ.get("PORT", 10000))
        log.info("Bot starting in webhook mode on port %s...", port)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=token,
            webhook_url=f"{webhook_url}/{token}",
        )
    else:
        log.info("Bot starting in polling mode...")
        app.run_polling()


if __name__ == "__main__":
    main()
