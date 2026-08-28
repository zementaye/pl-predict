"""Rules engine shared by the Telegram bot and the web app.

Nothing in this file knows about Telegram Updates, HTTP requests, or any
particular front end — it only takes plain values in and returns plain
dicts/values out, so bot.py and webapp.py can both call it and stay in sync.
"""

from datetime import datetime, timezone

import api
import db

MAX_SCORE = 9


def clamp(n):
    return max(0, min(MAX_SCORE, n))


def outcome(h, a):
    if h > a:
        return "H"
    if h < a:
        return "A"
    return "D"


def calc_points(pred_h, pred_a, act_h, act_a):
    if pred_h == act_h and pred_a == act_a:
        return 3
    if outcome(pred_h, pred_a) == outcome(act_h, act_a):
        return 1
    return 0


def player_name(row, players):
    for p in players:
        if p["telegram_id"] == row["telegram_id"]:
            return p["name"]
    return str(row["telegram_id"])


def allowed_predictor(gw, preds, players):
    """Whose turn it is right now, or None if no valid turn exists."""
    # A predictor game requires two registered players. An old active
    # gameweek can still exist after someone is removed, so never let the
    # missing opponent crash the web API with StopIteration.
    if len(players) < 2:
        return None
    if len(preds) == 0:
        return gw["starter_id"]
    if len(preds) == 1:
        return next(
            (p["telegram_id"] for p in players if p["telegram_id"] != gw["starter_id"]),
            None,
        )
    return None


def determine_starter(players, last_gw, fallback_telegram_id):
    """Turn alternates every gameweek. First-ever gameweek: whoever kicks it off."""
    if last_gw is None:
        return fallback_telegram_id
    return next(
        (p["telegram_id"] for p in players if p["telegram_id"] != last_gw["starter_id"]),
        fallback_telegram_id,
    )


def submit_prediction(chat_id, telegram_id, display_name, pred_h, pred_a, wildcard):
    """Apply one player's prediction, enforcing turn order, the no-duplicate-score
    rule, and the kickoff cutoff. Returns a plain dict:
      ok, message, gw (or None), next_player (telegram_id or None),
      chat_announcement (text worth relaying to the group, or None)
    """
    players = db.get_players()
    if not any(p["telegram_id"] == telegram_id for p in players):
        return {"ok": False, "message": "You're not registered. Send /start to the bot first.",
                "gw": None, "next_player": None, "chat_announcement": None}

    gw = db.get_active_gameweek(chat_id)
    if not gw:
        return {"ok": False, "message": "No active fixture. Run /newgameweek then pick a match first.",
                "gw": None, "next_player": None, "chat_announcement": None}

    try:
        kickoff_dt = datetime.fromisoformat(gw["kickoff"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > kickoff_dt:
            return {"ok": False, "message": "Kickoff has already passed — predictions are closed for this match.",
                    "gw": gw, "next_player": None, "chat_announcement": None}
    except ValueError:
        pass

    preds = db.get_predictions(gw["id"])
    if len(players) < 2:
        return {
            "ok": False,
            "message": "Need 2 registered players before making predictions.",
            "gw": gw,
            "next_player": None,
            "chat_announcement": None,
        }
    allowed = allowed_predictor(gw, preds, players)
    if allowed is None:
        return {"ok": False, "message": "Both predictions are already in for this match.",
                "gw": gw, "next_player": None, "chat_announcement": None}
    if telegram_id != allowed:
        allowed_name = next(p["name"] for p in players if p["telegram_id"] == allowed)
        return {"ok": False, "message": f"Not your turn — waiting on {allowed_name}.",
                "gw": gw, "next_player": None, "chat_announcement": None}

    if len(preds) == 1 and preds[0]["pred_home"] == pred_h and preds[0]["pred_away"] == pred_a:
        return {"ok": False, "message": f"{pred_h}-{pred_a} is already taken — pick a different score.",
                "gw": gw, "next_player": None, "chat_announcement": None}

    db.add_prediction(gw["id"], telegram_id, pred_h, pred_a, wildcard)
    preds = db.get_predictions(gw["id"])

    wc_tag = " 🃏" if wildcard else ""
    if len(preds) == 1:
        other = next(p for p in players if p["telegram_id"] != telegram_id)
        msg = (f"{display_name} predicted {pred_h}-{pred_a}{wc_tag}. "
               f"{other['name']}, your turn — any score except {pred_h}-{pred_a}.")
        return {"ok": True, "message": msg, "gw": gw, "next_player": other["telegram_id"],
                "chat_announcement": msg}
    else:
        db.mark_gameweek_status(gw["id"], "predicted")
        lines = [f"Both predictions locked in for {gw['home_team']} vs {gw['away_team']}:"]
        for p in preds:
            tag = " 🃏" if p["wildcard"] else ""
            lines.append(f"- {player_name(p, players)}: {p['pred_home']}-{p['pred_away']}{tag}")
        lines.append("I'll check the result after full time (or run /results manually).")
        text = "\n".join(lines)
        return {"ok": True, "message": text, "gw": gw, "next_player": None, "chat_announcement": text}


def lock_in_match(chat_id, gw_number, match_id, home, away, kickoff, starter_id):
    """Returns the newly-active gameweek, or None if someone else locked in a
    (different) fixture for this chat in the same instant — the caller should
    show a friendly "someone beat you to it" message rather than proceeding as
    if their own fixture won."""
    gw_id = db.create_gameweek(
        chat_id=chat_id, gw_number=gw_number, match_id=match_id,
        home=home, away=away, kickoff=kickoff, starter_id=starter_id,
    )
    if gw_id is None:
        return None
    return db.get_active_gameweek(chat_id)


def check_and_score_gameweek(gw):
    """Fetches the match result and scores it if finished. Returns the
    announcement text, or None if the match hasn't finished yet. Raises on
    API failure — callers should catch and log."""
    match = api.get_match(gw["match_id"])

    if match["status"] != "FINISHED":
        return None

    act_h = match["score"]["fullTime"]["home"]
    act_a = match["score"]["fullTime"]["away"]
    players = db.get_players()
    preds = db.get_predictions(gw["id"])

    lines = [f"FULL TIME: {gw['home_team']} {act_h}-{act_a} {gw['away_team']}"]
    for p in preds:
        pts = calc_points(p["pred_home"], p["pred_away"], act_h, act_a)
        if p["wildcard"]:
            pts *= 2
        db.set_points(p["id"], pts)
        tag = " 🃏 (doubled)" if p["wildcard"] else ""
        lines.append(f"- {player_name(p, players)} predicted {p['pred_home']}-{p['pred_away']}{tag} -> +{pts} pts")

    db.finish_gameweek(gw["id"], act_h, act_a)
    return "\n".join(lines)
