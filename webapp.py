import hashlib
import hmac
import json
import logging
import os
from urllib.parse import parse_qsl

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

import api
import db
import game

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", static_url_path="/static")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")


# ---------- Telegram auth ----------
# Mini Apps send Telegram's signed `initData` string with every request so we
# can trust who's calling without a separate login step. Verification is the
# standard HMAC scheme from https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

def _verify_init_data(init_data):
    if not init_data or not BOT_TOKEN:
        return None
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None
    user_json = pairs.get("user")
    if not user_json:
        return None
    try:
        return json.loads(user_json)
    except ValueError:
        return None


def current_telegram_user():
    """Returns the verified Telegram user dict for this request, or None if
    it wasn't opened from inside Telegram (or failed verification)."""
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    return _verify_init_data(init_data)


def the_chat_id():
    raw = db.get_setting("chat_id")
    return int(raw) if raw else None


def notify_chat(text):
    chat_id = the_chat_id()
    if not chat_id or not BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        log.exception("Failed to notify chat")


# ---------- frontend ----------

@app.route("/")
def index():
    return send_from_directory("frontend", "index.html")


# ---------- API ----------

@app.route("/api/state")
def api_state():
    user = current_telegram_user()
    players = db.get_players()

    me = None
    if user:
        registered = next((p for p in players if p["telegram_id"] == user["id"]), None)
        me = {
            "telegram_id": user["id"],
            "name": registered["name"] if registered else user.get("first_name", "You"),
            "registered": registered is not None,
        }

    chat_id = the_chat_id()
    active_gws = []
    if chat_id:
        for gw in db.get_open_gameweeks(chat_id):
            preds = db.get_predictions(gw["id"])
            allowed = game.allowed_predictor(gw, preds, players)
            edit_req = db.get_edit_request(gw["id"])
            active_gws.append({
                "id": gw["id"],
                "gw_number": gw["gw_number"],
                "home": gw["home_team"],
                "away": gw["away_team"],
                "kickoff": gw["kickoff"],
                "status": gw["status"],
                "allowed_telegram_id": allowed,
                "predictions": [
                    {
                        "telegram_id": p["telegram_id"],
                        "name": game.player_name(p, players),
                        "home": p["pred_home"],
                        "away": p["pred_away"],
                        "wildcard": p["wildcard"],
                    }
                    for p in preds
                ],
                "edit_request": {
                    "requester_id": edit_req["requester_id"],
                    "status": edit_req["status"],
                } if edit_req else None,
            })

    history_rows = db.full_history()
    history = []
    by_gw = {}
    for r in history_rows:
        gwn = r["gw_number"]
        if gwn not in by_gw:
            entry = {
                "gw_number": gwn,
                "home": r["home_team"],
                "away": r["away_team"],
                "actual_home": r["actual_home"],
                "actual_away": r["actual_away"],
                "predictions": [],
            }
            by_gw[gwn] = entry
            history.append(entry)
        by_gw[gwn]["predictions"].append({
            "name": r["name"],
            "home": r["pred_home"],
            "away": r["pred_away"],
            "wildcard": r["wildcard"],
            "points": r["points"],
        })
    history.reverse()  # most recent gameweek first

    return jsonify({
        "me": me,
        "players": [{"telegram_id": p["telegram_id"], "name": p["name"]} for p in players],
        "leaderboard": [{"name": r["name"], "total": r["total"]} for r in db.leaderboard()],
        "active_gameweeks": active_gws,
        "history": history,
        "max_score": game.MAX_SCORE,
        "setup_needed": chat_id is None,
    })


@app.route("/api/predict", methods=["POST"])
def api_predict():
    user = current_telegram_user()
    if not user:
        return jsonify({"ok": False, "message": "Open this from Telegram to predict."}), 401

    chat_id = the_chat_id()
    if not chat_id:
        return jsonify({"ok": False, "message": "No game set up yet — send /start to the bot in the group first."}), 400

    body = request.get_json(silent=True) or {}
    try:
        h = game.clamp(int(body["home"]))
        a = game.clamp(int(body["away"]))
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "message": "Invalid score."}), 400
    wildcard = bool(body.get("wildcard"))
    gw_id = body.get("gw_id")

    result = game.submit_prediction(chat_id, user["id"], user.get("first_name", "Player"), h, a, wildcard, gw_id=gw_id)
    if result["ok"] and result.get("chat_announcement"):
        notify_chat(result["chat_announcement"] + "\n\n(via the app)")

    return jsonify({"ok": result["ok"], "message": result["message"]})


@app.route("/api/requestedit", methods=["POST"])
def api_requestedit():
    user = current_telegram_user()
    if not user:
        return jsonify({"ok": False, "message": "Open this from Telegram to do that."}), 401

    chat_id = the_chat_id()
    if not chat_id:
        return jsonify({"ok": False, "message": "No game set up yet."}), 400

    body = request.get_json(silent=True) or {}
    result = game.request_edit(chat_id, user["id"], user.get("first_name", "Player"), gw_id=body.get("gw_id"))
    if result["ok"] and result.get("chat_announcement"):
        notify_chat(result["chat_announcement"] + "\n\n(via the app)")
    return jsonify({"ok": result["ok"], "message": result["message"]})


@app.route("/api/approveedit", methods=["POST"])
def api_approveedit():
    user = current_telegram_user()
    if not user:
        return jsonify({"ok": False, "message": "Open this from Telegram to do that."}), 401

    chat_id = the_chat_id()
    if not chat_id:
        return jsonify({"ok": False, "message": "No game set up yet."}), 400

    body = request.get_json(silent=True) or {}
    result = game.approve_edit(chat_id, user["id"], user.get("first_name", "Player"), gw_id=body.get("gw_id"))
    if result["ok"] and result.get("chat_announcement"):
        notify_chat(result["chat_announcement"] + "\n\n(via the app)")
    return jsonify({"ok": result["ok"], "message": result["message"]})


@app.route("/api/editpredict", methods=["POST"])
def api_editpredict():
    user = current_telegram_user()
    if not user:
        return jsonify({"ok": False, "message": "Open this from Telegram to predict."}), 401

    chat_id = the_chat_id()
    if not chat_id:
        return jsonify({"ok": False, "message": "No game set up yet."}), 400

    body = request.get_json(silent=True) or {}
    try:
        h = game.clamp(int(body["home"]))
        a = game.clamp(int(body["away"]))
    except (KeyError, TypeError, ValueError):
        return jsonify({"ok": False, "message": "Invalid score."}), 400
    wildcard = bool(body.get("wildcard"))

    result = game.edit_prediction(chat_id, user["id"], user.get("first_name", "Player"), h, a, wildcard, gw_id=body.get("gw_id"))
    if result["ok"] and result.get("chat_announcement"):
        notify_chat(result["chat_announcement"] + "\n\n(via the app)")
    return jsonify({"ok": result["ok"], "message": result["message"]})


@app.route("/api/newgameweek")
def api_newgameweek_fixtures():
    """Lists fixtures for a matchday so the app can offer them to tap — mirrors
    the bot's /newgameweek, but stateless: nothing is written until /api/lockmatch."""
    matchday_param = request.args.get("matchday")
    try:
        matchday = int(matchday_param) if matchday_param else api.get_current_matchday()
        matches = api.get_matches_for_matchday(matchday)
    except Exception as e:
        log.exception("football-data.org API error")
        return jsonify({"ok": False, "message": f"Couldn't fetch fixtures: {e}"}), 502

    fixtures = [
        {
            "match_id": m["id"],
            "home": m["homeTeam"]["shortName"] or m["homeTeam"]["name"],
            "away": m["awayTeam"]["shortName"] or m["awayTeam"]["name"],
            "home_crest": m["homeTeam"].get("crest"),
            "away_crest": m["awayTeam"].get("crest"),
            "kickoff": m["utcDate"],
        }
        for m in matches
        if not db.match_id_taken(m["id"])
    ]
    return jsonify({"ok": True, "matchday": matchday, "fixtures": fixtures})


@app.route("/api/lockmatch", methods=["POST"])
def api_lockmatch():
    user = current_telegram_user()
    if not user:
        return jsonify({"ok": False, "message": "Open this from Telegram to do that."}), 401

    chat_id = the_chat_id()
    if not chat_id:
        return jsonify({"ok": False, "message": "No game set up yet — send /start to the bot in the group first."}), 400

    players = db.get_players()
    if len(players) < 2:
        return jsonify({"ok": False, "message": "Need 2 registered players first — both of you send /start to the bot."}), 400

    body = request.get_json(silent=True) or {}
    required = ("matchday", "match_id", "home", "away", "kickoff")
    if not all(k in body for k in required):
        return jsonify({"ok": False, "message": "Missing fixture details."}), 400

    if db.match_id_taken(body["match_id"]):
        return jsonify({"ok": False, "message": "That fixture's already locked in."}), 400

    last_gw = db.get_last_gameweek(chat_id)
    starter_id = game.determine_starter(players, last_gw, user["id"])
    try:
        gw = game.lock_in_match(
            chat_id=chat_id,
            gw_number=body["matchday"],
            match_id=body["match_id"],
            home=body["home"],
            away=body["away"],
            kickoff=body["kickoff"],
            starter_id=starter_id,
        )
    except Exception as e:
        log.exception("Failed to lock in fixture")
        return jsonify({"ok": False, "message": f"Couldn't lock that fixture in: {e}"}), 502
    starter_name = next(p["name"] for p in players if p["telegram_id"] == starter_id)
    notify_chat(
        f"New gameweek locked in via the app: {gw['home_team']} vs {gw['away_team']}. "
        f"{starter_name} predicts first."
    )
    return jsonify({"ok": True, "message": "Locked in."})


@app.route("/api/resolvemissed", methods=["POST"])
def api_resolvemissed():
    user = current_telegram_user()
    if not user:
        return jsonify({"ok": False, "message": "Open this from Telegram to do that."}), 401

    chat_id = the_chat_id()
    if not chat_id:
        return jsonify({"ok": False, "message": "No game set up yet."}), 400

    body = request.get_json(silent=True) or {}
    gw_id = body.get("gw_id")
    if gw_id is None:
        return jsonify({"ok": False, "message": "No fixture specified."}), 400

    result = game.resolve_missed_gameweek(chat_id, gw_id)
    if result["ok"] and result.get("chat_announcement"):
        notify_chat(result["chat_announcement"] + "\n\n(via the app)")
    return jsonify({"ok": result["ok"], "message": result["message"]})


@app.route("/api/results", methods=["POST"])
def api_results():
    chat_id = the_chat_id()
    if not chat_id:
        return jsonify({"ok": False, "message": "No game set up yet."}), 400

    body = request.get_json(silent=True) or {}
    gw_id = body.get("gw_id")

    if gw_id is not None:
        gw = db.get_gameweek(gw_id)
        if not gw or gw["chat_id"] != chat_id or gw["status"] != "predicted":
            return jsonify({"ok": False, "message": "That fixture isn't fully predicted and awaiting a result."})
        candidates = [gw]
    else:
        candidates = [g for g in db.get_open_gameweeks(chat_id) if g["status"] == "predicted"]
        if not candidates:
            return jsonify({"ok": False, "message": "No fixture is fully predicted and awaiting a result right now."})

    checked_texts = []
    any_finished = False
    for gw in candidates:
        try:
            text = game.check_and_score_gameweek(gw)
        except Exception as e:
            log.exception("Failed to fetch match result for gameweek %s", gw["id"])
            continue
        if text is not None:
            any_finished = True
            checked_texts.append(text)
            notify_chat(text)

    if not any_finished:
        msg = "Match hasn't finished yet." if gw_id is not None else "None of those matches have finished yet."
        return jsonify({"ok": False, "message": msg})

    return jsonify({"ok": True, "message": "\n\n".join(checked_texts)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=True)
