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
    """Whose turn it is right now, or None if both predictions are already in."""
    if len(preds) == 0:
        return gw["starter_id"]
    if len(preds) == 1:
        return next(p["telegram_id"] for p in players if p["telegram_id"] != gw["starter_id"])
    return None


def determine_starter(players, last_gw, fallback_telegram_id):
    """Turn alternates every gameweek. First-ever gameweek: whoever kicks it off."""
    if last_gw is None:
        return fallback_telegram_id
    return next(p["telegram_id"] for p in players if p["telegram_id"] != last_gw["starter_id"])


def open_gameweeks(chat_id):
    """All not-yet-finished fixtures for this chat, soonest kickoff first."""
    return db.get_open_gameweeks(chat_id)


def _resolve_gameweek(chat_id, gw_id):
    """Looks up an explicit fixture by id (used by the app, which always
    knows which card the person tapped) and makes sure it actually belongs
    to this chat and isn't finished yet."""
    if gw_id is None:
        return None, "No fixture specified."
    gw = db.get_gameweek(gw_id)
    if not gw or gw["chat_id"] != chat_id:
        return None, "That fixture doesn't exist."
    if gw["status"] == "finished":
        return None, "That fixture is already finished."
    return gw, None


def gameweek_awaiting_me(chat_id, telegram_id, players):
    """For text-based bot commands that don't name a fixture: the soonest
    open fixture where it's this player's turn to predict, so working
    through several open fixtures in order 'just works' one /predict at a
    time. Falls back to the soonest open fixture overall (e.g. so /pending
    still has something to show) if it isn't this player's turn on any."""
    gws = db.get_open_gameweeks(chat_id)
    if not gws:
        return None
    for gw in gws:
        preds = db.get_predictions(gw["id"])
        if allowed_predictor(gw, preds, players) == telegram_id:
            return gw
    return gws[0]


def gameweek_with_my_pending_edit(chat_id, telegram_id, players, role):
    """Finds the open fixture relevant to an edit-request action, when the
    caller (bot text command) didn't name one explicitly.
    role='request'  -> soonest fixture where I've already predicted (so I
                        have something to ask to change).
    role='approve'  -> soonest fixture with a pending edit request from the
                        other player.
    role='submit'   -> soonest fixture where my edit request is approved.
    """
    gws = db.get_open_gameweeks(chat_id)
    for gw in gws:
        edit_req = db.get_edit_request(gw["id"])
        if role == "request":
            preds = db.get_predictions(gw["id"])
            if any(p["telegram_id"] == telegram_id for p in preds):
                return gw
        elif role == "approve":
            if edit_req and edit_req["status"] == "pending" and edit_req["requester_id"] != telegram_id:
                return gw
        elif role == "submit":
            if edit_req and edit_req["status"] == "approved" and edit_req["requester_id"] == telegram_id:
                return gw
    return None


def submit_prediction(chat_id, telegram_id, display_name, pred_h, pred_a, wildcard, gw_id=None):
    """Apply one player's prediction, enforcing turn order, the no-duplicate-score
    rule, and the kickoff cutoff. Returns a plain dict:
      ok, message, gw (or None), next_player (telegram_id or None),
      chat_announcement (text worth relaying to the group, or None)

    gw_id names the fixture explicitly (the app always passes this, since
    several fixtures can be open at once). When omitted (bot text commands),
    it's resolved to whichever open fixture is waiting on this player.
    """
    players = db.get_players()
    if not any(p["telegram_id"] == telegram_id for p in players):
        return {"ok": False, "message": "You're not registered. Send /start to the bot first.",
                "gw": None, "next_player": None, "chat_announcement": None}

    if gw_id is not None:
        gw, err = _resolve_gameweek(chat_id, gw_id)
        if err:
            return {"ok": False, "message": err, "gw": None, "next_player": None, "chat_announcement": None}
    else:
        gw = gameweek_awaiting_me(chat_id, telegram_id, players)
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


def _kickoff_passed(gw):
    try:
        kickoff_dt = datetime.fromisoformat(gw["kickoff"].replace("Z", "+00:00"))
        return datetime.now(timezone.utc) > kickoff_dt
    except ValueError:
        return False


def request_edit(chat_id, telegram_id, display_name, gw_id=None):
    """A player asks to change a prediction they've already submitted. Takes
    effect only once the other player approves it via approve_edit()."""
    players = db.get_players()
    if not any(p["telegram_id"] == telegram_id for p in players):
        return {"ok": False, "message": "You're not registered. Send /start to the bot first.",
                "chat_announcement": None}

    if gw_id is not None:
        gw, err = _resolve_gameweek(chat_id, gw_id)
        if err:
            return {"ok": False, "message": err, "chat_announcement": None}
    else:
        gw = gameweek_with_my_pending_edit(chat_id, telegram_id, players, "request")
    if not gw:
        return {"ok": False, "message": "No active fixture.", "chat_announcement": None}

    if _kickoff_passed(gw):
        return {"ok": False, "message": "Kickoff has already passed — predictions are locked.",
                "chat_announcement": None}

    preds = db.get_predictions(gw["id"])
    if not any(p["telegram_id"] == telegram_id for p in preds):
        return {"ok": False, "message": "You haven't predicted yet this gameweek — nothing to edit.",
                "chat_announcement": None}

    other = next((p for p in players if p["telegram_id"] != telegram_id), None)
    db.create_edit_request(gw["id"], telegram_id)
    other_name = other["name"] if other else "the other player"
    msg = (f"{display_name} wants to change their prediction for {gw['home_team']} vs {gw['away_team']}. "
           f"{other_name}, run /approveedit (or approve it in the app) to allow it.")
    return {"ok": True, "message": "Edit request sent \u2014 waiting on the other player to approve it.",
            "chat_announcement": msg}


def approve_edit(chat_id, telegram_id, display_name, gw_id=None):
    """The other player agrees to let the requester submit a new score."""
    players = db.get_players()
    if gw_id is not None:
        gw, err = _resolve_gameweek(chat_id, gw_id)
        if err:
            return {"ok": False, "message": err, "chat_announcement": None}
    else:
        gw = gameweek_with_my_pending_edit(chat_id, telegram_id, players, "approve")
    if not gw:
        return {"ok": False, "message": "No active fixture.", "chat_announcement": None}

    req = db.get_edit_request(gw["id"])
    if not req or req["status"] != "pending":
        return {"ok": False, "message": "No pending edit request right now.", "chat_announcement": None}
    if req["requester_id"] == telegram_id:
        return {"ok": False, "message": "You can't approve your own edit request \u2014 the other player needs to.",
                "chat_announcement": None}

    db.approve_edit_request(gw["id"])
    requester_name = next((p["name"] for p in players if p["telegram_id"] == req["requester_id"]), "They")
    msg = f"{display_name} approved it \u2014 {requester_name} can now submit a new score."
    return {"ok": True, "message": "Approved.", "chat_announcement": msg}


def edit_prediction(chat_id, telegram_id, display_name, pred_h, pred_a, wildcard, gw_id=None):
    """Overwrites the requester's existing prediction, but only once the
    other player has approved the pending edit request."""
    players = db.get_players()
    if gw_id is not None:
        gw, err = _resolve_gameweek(chat_id, gw_id)
        if err:
            return {"ok": False, "message": err, "gw": None, "chat_announcement": None}
    else:
        gw = gameweek_with_my_pending_edit(chat_id, telegram_id, players, "submit")
    if not gw:
        return {"ok": False, "message": "No active fixture.", "gw": None, "chat_announcement": None}

    req = db.get_edit_request(gw["id"])
    if not req or req["status"] != "approved" or req["requester_id"] != telegram_id:
        return {"ok": False, "message": "You don't have an approved edit request right now \u2014 run /requestedit first.",
                "gw": gw, "chat_announcement": None}

    if _kickoff_passed(gw):
        return {"ok": False, "message": "Kickoff has already passed \u2014 predictions are locked.",
                "gw": gw, "chat_announcement": None}

    preds = db.get_predictions(gw["id"])
    other_pred = next((p for p in preds if p["telegram_id"] != telegram_id), None)
    if other_pred and other_pred["pred_home"] == pred_h and other_pred["pred_away"] == pred_a:
        return {"ok": False, "message": f"{pred_h}-{pred_a} is already taken \u2014 pick a different score.",
                "gw": gw, "chat_announcement": None}

    db.update_prediction(gw["id"], telegram_id, pred_h, pred_a, wildcard)
    db.clear_edit_request(gw["id"])

    wc_tag = " \U0001f3b4" if wildcard else ""
    msg = f"{display_name} changed their prediction for {gw['home_team']} vs {gw['away_team']} to {pred_h}-{pred_a}{wc_tag}."
    return {"ok": True, "message": msg, "gw": gw, "chat_announcement": msg}


def resolve_missed_gameweek(chat_id, gw_id):
    """Closes out a fixture that kicked off without full predictions: scores
    0 for whoever didn't predict, so it stops sitting stuck forever. Doesn't
    touch anyone who *did* predict in time — their prediction still scores
    against the real result if it's available."""
    gw = db.get_gameweek(gw_id)
    if not gw or gw["chat_id"] != chat_id:
        return {"ok": False, "message": "That fixture doesn't exist."}
    if gw["status"] == "finished":
        return {"ok": False, "message": "That fixture is already finished."}
    if not _kickoff_passed(gw):
        return {"ok": False, "message": "Kickoff hasn't passed yet for this fixture."}

    players = db.get_players()
    preds = db.get_predictions(gw["id"])
    already_predicted = {p["telegram_id"] for p in preds}
    for p in players:
        if p["telegram_id"] not in already_predicted:
            db.add_prediction(gw["id"], p["telegram_id"], 0, 0, False)

    # Use the real result if it's available, so anyone who *did* predict in
    # time still scores properly against it — only the missed side gets 0.
    act_h = act_a = None
    try:
        match = api.get_match(gw["match_id"])
        act_h = match["score"]["fullTime"]["home"]
        act_a = match["score"]["fullTime"]["away"]
    except Exception:
        pass
    if act_h is None or act_a is None:
        act_h, act_a = 0, 0

    for p in db.get_predictions(gw["id"]):
        if p["telegram_id"] in already_predicted:
            pts = calc_points(p["pred_home"], p["pred_away"], act_h, act_a)
            if p["wildcard"]:
                pts *= 2
        else:
            pts = 0
        db.set_points(p["id"], pts)

    db.finish_gameweek(gw["id"], act_h, act_a)
    msg = f"Closed out {gw['home_team']} vs {gw['away_team']} \u2014 missed predictions scored 0."
    return {"ok": True, "message": msg, "chat_announcement": msg}


def lock_in_match(chat_id, gw_number, match_id, home, away, kickoff, starter_id):
    gw_id = db.create_gameweek(
        chat_id=chat_id, gw_number=gw_number, match_id=match_id,
        home=home, away=away, kickoff=kickoff, starter_id=starter_id,
    )
    return db.get_gameweek(gw_id)


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
