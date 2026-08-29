import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    telegram_id BIGINT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gameweeks (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT,
    gw_number INTEGER,
    match_id INTEGER UNIQUE,
    home_team TEXT,
    away_team TEXT,
    kickoff TEXT,
    starter_id BIGINT,
    status TEXT DEFAULT 'awaiting_predictions',  -- awaiting_predictions -> predicted -> finished
    actual_home INTEGER,
    actual_away INTEGER
);

CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    gameweek_id INTEGER REFERENCES gameweeks(id),
    telegram_id BIGINT,
    pred_home INTEGER,
    pred_away INTEGER,
    points INTEGER,
    wildcard BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(gameweek_id, telegram_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS edit_requests (
    id SERIAL PRIMARY KEY,
    gameweek_id INTEGER REFERENCES gameweeks(id) UNIQUE,
    requester_id BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'  -- pending -> approved (row is deleted once the edit is used)
);
"""

# Runs on every startup. CREATE TABLE IF NOT EXISTS above won't add columns to
# a table that already exists (e.g. on Neon from before this feature), so this
# migrates existing deployments forward. Safe to run repeatedly.
MIGRATIONS = """
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS wildcard BOOLEAN NOT NULL DEFAULT FALSE;
"""


@contextmanager
def get_conn():
    conn = psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute(MIGRATIONS)


def add_player(telegram_id, name):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM players")
            existing = cur.fetchall()
            if any(p["telegram_id"] == telegram_id for p in existing):
                return "already"
            if len(existing) >= 2:
                return "full"
            cur.execute("INSERT INTO players (telegram_id, name) VALUES (%s, %s)", (telegram_id, name))
            return "added"


def get_players():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM players")
            return cur.fetchall()


def remove_player(telegram_id):
    """Remove a registered predictor by Telegram user ID."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM players WHERE telegram_id=%s RETURNING telegram_id, name", (telegram_id,))
            return cur.fetchone()


def get_last_gameweek(chat_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM gameweeks WHERE chat_id=%s ORDER BY id DESC LIMIT 1", (chat_id,)
            )
            return cur.fetchone()


def get_active_gameweek(chat_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM gameweeks WHERE chat_id=%s AND status IN ('awaiting_predictions','predicted') "
                "ORDER BY id DESC LIMIT 1",
                (chat_id,),
            )
            return cur.fetchone()


def create_gameweek(chat_id, gw_number, match_id, home, away, kickoff, starter_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO gameweeks (chat_id, gw_number, match_id, home_team, away_team, kickoff, starter_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (chat_id, gw_number, match_id, home, away, kickoff, starter_id),
            )
            return cur.fetchone()["id"]


def get_predictions(gameweek_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM predictions WHERE gameweek_id = %s ORDER BY id", (gameweek_id,)
            )
            return cur.fetchall()


def add_prediction(gameweek_id, telegram_id, home, away, wildcard=False):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO predictions (gameweek_id, telegram_id, pred_home, pred_away, wildcard) "
                "VALUES (%s,%s,%s,%s,%s)",
                (gameweek_id, telegram_id, home, away, wildcard),
            )


def update_prediction(gameweek_id, telegram_id, home, away, wildcard):
    """Overwrites an existing prediction in place (used for agreed edits —
    unlike add_prediction this does not create a new row)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE predictions SET pred_home=%s, pred_away=%s, wildcard=%s "
                "WHERE gameweek_id=%s AND telegram_id=%s RETURNING id",
                (home, away, wildcard, gameweek_id, telegram_id),
            )
            return cur.fetchone()


def get_edit_request(gameweek_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM edit_requests WHERE gameweek_id=%s", (gameweek_id,))
            return cur.fetchone()


def create_edit_request(gameweek_id, requester_id):
    """Opens (or re-opens) a pending edit request for a gameweek. Only one
    open request per gameweek at a time — a fresh call always resets it back
    to pending under the new requester."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO edit_requests (gameweek_id, requester_id, status) VALUES (%s,%s,'pending') "
                "ON CONFLICT (gameweek_id) DO UPDATE SET requester_id=EXCLUDED.requester_id, status='pending'",
                (gameweek_id, requester_id),
            )


def approve_edit_request(gameweek_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE edit_requests SET status='approved' WHERE gameweek_id=%s RETURNING *",
                (gameweek_id,),
            )
            return cur.fetchone()


def clear_edit_request(gameweek_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM edit_requests WHERE gameweek_id=%s", (gameweek_id,))


def mark_gameweek_status(gameweek_id, status):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE gameweeks SET status=%s WHERE id=%s", (status, gameweek_id))


def finish_gameweek(gameweek_id, actual_home, actual_away):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE gameweeks SET status='finished', actual_home=%s, actual_away=%s WHERE id=%s",
                (actual_home, actual_away, gameweek_id),
            )


def set_points(prediction_id, points):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE predictions SET points=%s WHERE id=%s", (points, prediction_id))


def predicted_gameweeks_awaiting_result():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM gameweeks WHERE status='predicted'")
            return cur.fetchall()


def leaderboard():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pl.telegram_id, pl.name, COALESCE(SUM(p.points),0) as total "
                "FROM players pl LEFT JOIN predictions p ON p.telegram_id = pl.telegram_id "
                "GROUP BY pl.telegram_id, pl.name ORDER BY total DESC"
            )
            return cur.fetchall()


def set_setting(key, value):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (key, value) VALUES (%s,%s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, value),
            )


def get_setting(key):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
            row = cur.fetchone()
            return row["value"] if row else None


def full_history():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT g.gw_number, g.home_team, g.away_team, g.actual_home, g.actual_away, "
                "pl.name, p.pred_home, p.pred_away, p.points, p.wildcard "
                "FROM gameweeks g JOIN predictions p ON p.gameweek_id=g.id "
                "JOIN players pl ON pl.telegram_id=p.telegram_id "
                "WHERE g.status='finished' ORDER BY g.gw_number, pl.name"
            )
            return cur.fetchall()
