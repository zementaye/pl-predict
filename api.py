import os
import requests

BASE_URL = "https://api.football-data.org/v4"


def _headers():
    return {"X-Auth-Token": os.environ["FOOTBALL_DATA_API_KEY"]}


def get_current_matchday():
    r = requests.get(f"{BASE_URL}/competitions/PL", headers=_headers(), timeout=15)
    r.raise_for_status()
    return r.json()["currentSeason"]["currentMatchday"]


def get_matches_for_matchday(matchday):
    r = requests.get(
        f"{BASE_URL}/competitions/PL/matches",
        headers=_headers(),
        params={"matchday": matchday},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["matches"]


def get_match(match_id):
    r = requests.get(f"{BASE_URL}/matches/{match_id}", headers=_headers(), timeout=15)
    r.raise_for_status()
    return r.json()
