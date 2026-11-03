"""Runtime settings from the environment. No secrets here: live mode authenticates as the app's identity and,
for user actions, on behalf of the signed in user (Databricks Apps forwards the user's identity in headers).

  EDIP_MODE        mock (default): fixtures and in memory actions, demo personas, replay clock
                   live: Lakebase synced tables and native tables
  EDIP_FIXTURES    mock data folder (default app/fixtures/data, built by app/fixtures/build.py)
  EDIP_CLOCK       start the replay clock at this ISO time (default: the busiest boarding hour of the week)
  EDIP_CLOCK_MODE  replay (default): the clock replays the data's week, which suits the synthetic backfill;
                   live: the clock is the current time, for feeds that are flowing now
  EDIP_DEMO_PERSONAS  true (default): anyone can switch between demo personas; actions also record the signed in
                   user's email. false: the signed in user's role comes from EDIP_ROLE_MAP
  EDIP_ROLE_MAP    JSON {"email": "persona id"}, for example {"rn@example.org": "unit-sgh-micu"}; anyone not in it
                   gets the read only viewer role
  EDIP_SYNTHETIC_DATA  true only when the data is synthetic: required for demo personas and the replay clock
  EDIP_PG_ENDPOINT live mode: projects/<p>/branches/<b>/endpoints/<e> (PGHOST and the rest come from the app's
                   postgres resource)
"""
import os
from pathlib import Path

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
