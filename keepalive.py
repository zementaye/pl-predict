"""Decides whether the keep-alive self-ping should fire right now.

Render's free tier gives each workspace a fixed monthly pool of instance
hours. Pinging every 10 minutes around the clock keeps a service awake 24/7
and burns the pool; pinging only on the days you actually use the app keeps
it awake then, and lets it sleep (and stop counting hours) the rest of the
week. A real request on a sleeping day still wakes the service on demand.

Env vars (set on each Render service that runs a self-ping):
  PING_DAYS  comma-separated weekday numbers, Monday=0 ... Sunday=6.
             Default "5,6" (Saturday, Sunday).
  PING_TZ    IANA timezone name used to decide what "Saturday" means.
             Default "UTC". Set it to your own timezone, otherwise the
             weekend starts/ends at UTC midnight rather than yours.
"""
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


def should_ping(now=None):
    raw_days = os.environ.get("PING_DAYS", "5,6")
    try:
        days = {int(d) for d in raw_days.split(",") if d.strip()}
    except ValueError:
        log.warning("Bad PING_DAYS=%r, falling back to Sat/Sun", raw_days)
        days = {5, 6}

    tz_name = os.environ.get("PING_TZ", "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        log.warning("Unknown PING_TZ=%r, falling back to UTC", tz_name)
        tz = timezone.utc

    now = now or datetime.now(tz)
    return now.weekday() in days
