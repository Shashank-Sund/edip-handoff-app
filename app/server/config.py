"""Runtime settings from the environment. No secrets here: live mode reads and writes data as the app's own identity.
Databricks Apps forwards the signed in user in headers (X-Forwarded-Email, X-Forwarded-Preferred-Username); the app
only reads them to record who acted or looked, and to map the user to a role (EDIP_ROLE_MAP).

  EDIP_MODE        mock (default): fixtures and in memory actions, demo personas, replay clock
                   live: Lakebase synced tables and native tables
  EDIP_FIXTURES    mock data folder (default app/fixtures/data, built by app/fixtures/build.py)
  EDIP_CLOCK       start the replay clock at this ISO time (default: the busiest boarding hour of the week)
  EDIP_CLOCK_MODE  replay (default): the clock replays the data's week, which suits the synthetic backfill;
                   live: the clock is the current time, for feeds that are flowing now
  EDIP_DEMO_PERSONAS  true (default): anyone can switch between demo personas; actions also record the signed in
                   user's email. false: the signed in user's role comes from EDIP_ROLE_MAP
  EDIP_ROLE_MAP    JSON {"email": "persona id"}, for example {"rn@example.org": "unit-sgh-micu"}; emails match
                   without regard to case; anyone not in it, or mapped to a persona id that does not exist, gets the
                   read only viewer role (persona ids: app/README.md)
  EDIP_SYNTHETIC_DATA  true only when the data is synthetic: required for demo personas and the replay clock
  EDIP_TIMEZONE    the facilities' time zone, an IANA name (default America/New_York); the server shows times in it
                   and the client gets it from /api/config
  EDIP_STALE_BOARDING_HOURS  with EDIP_CLOCK_MODE=live: a patient with an admit decision and no ED departure for
                   longer than this (default 24) is left off the board and the boarding counts, as a feed that
                   missed the departure would otherwise keep them boarding forever
  EDIP_PAST_VIEW_HOURS  with EDIP_CLOCK_MODE=live: how far back (default 72 hours, at most 8760; 0 turns it off) a clinician
                   can look at the board and handoffs as they were at an earlier time. Read only: edits, attestations,
                   sign offs and read backs always carry the server's current time, and are refused while the client
                   looks at an earlier time. A time older than the limit is refused (400); a later one than now is
                   now, and one within 2 minutes of now counts as now (clocks of devices differ). Every view of an
                   earlier time is a row in app.view_log (who, what, the time viewed, when); if that row cannot be
                   written, the view is refused. A draft version shows from when the handoff job wrote it
                   (created_ts) or its as_of, whichever is later, so an earlier time shows the drafts as they were
                   then; a canceled admission shows canceled from the cancel's time (retracted_ts), and encounter
                   details are as the data is now (app/README.md)
  EDIP_ACTIONS_TTL live mode: seconds the app reuses the clinician actions it read from Lakebase before reading them
                   again (default 2), so every app process sees the actions the others saved
  EDIP_PG_ENDPOINT live mode: projects/<p>/branches/<b>/endpoints/<e> (PGHOST and the rest come from the app's
                   postgres resource)
"""
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[2]
MODE = os.getenv("EDIP_MODE", "mock")
FIXTURES = Path(os.getenv("EDIP_FIXTURES", ROOT / "app" / "fixtures" / "data"))
CLOCK = os.getenv("EDIP_CLOCK")
TEMPLATES = Path(os.getenv("EDIP_TEMPLATES", ROOT / "ai" / "templates"))
# Demo personas and the replay clock let anyone act as any clinician at any time: fine for synthetic data only.
# They work only when EDIP_SYNTHETIC_DATA is true (mock mode always is).
SYNTHETIC = MODE == "mock" or os.getenv("EDIP_SYNTHETIC_DATA", "false").lower() == "true"
CLOCK_MODE = os.getenv("EDIP_CLOCK_MODE", "replay") if SYNTHETIC else "live"
DEMO_PERSONAS = SYNTHETIC and os.getenv("EDIP_DEMO_PERSONAS", "true").lower() == "true"
ROLE_MAP = os.getenv("EDIP_ROLE_MAP", "{}")
STATIC = Path(os.getenv("EDIP_STATIC", Path(__file__).resolve().parent / "static"))


def _timezone(name: str) -> str:
    try:
        ZoneInfo(name)
        return name
    except (ZoneInfoNotFoundError, ValueError):
        logging.getLogger("edip").warning("EDIP_TIMEZONE %r is not a known time zone; using America/New_York", name)
        return "America/New_York"


TIMEZONE = _timezone(os.getenv("EDIP_TIMEZONE") or "America/New_York")
STALE_BOARDING_HOURS = float(os.getenv("EDIP_STALE_BOARDING_HOURS", "24"))
ACTIONS_TTL = float(os.getenv("EDIP_ACTIONS_TTL", "2"))
PAST_VIEW_HOURS = float(os.getenv("EDIP_PAST_VIEW_HOURS", "72"))
if not 0 <= PAST_VIEW_HOURS <= 24 * 365:             # also nan; inf would not fit a timedelta
    logging.getLogger("edip").warning("EDIP_PAST_VIEW_HOURS %r is not 0 to 8760; using %s", PAST_VIEW_HOURS,
                                      "8760" if PAST_VIEW_HOURS > 0 else "0")
    PAST_VIEW_HOURS = 24.0 * 365 if PAST_VIEW_HOURS > 0 else 0.0
