"""Runtime settings from the environment. No secrets here: live mode authenticates as the app's identity and,
for user actions, on behalf of the signed in user (Databricks Apps forwards the user's identity in headers).

  EDIP_MODE        mock (default): fixtures and in memory actions, demo personas, replay clock
                   live: Lakebase synced tables and native tables
  EDIP_FIXTURES    mock data folder (default app/fixtures/data, built by app/fixtures/build.py)
  EDIP_CLOCK       start the replay clock at this ISO time (default: the busiest boarding hour of the week)
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODE = os.getenv("EDIP_MODE", "mock")
FIXTURES = Path(os.getenv("EDIP_FIXTURES", ROOT / "app" / "fixtures" / "data"))
CLOCK = os.getenv("EDIP_CLOCK")
TEMPLATES = Path(os.getenv("EDIP_TEMPLATES", ROOT / "ai" / "templates"))
STATIC = Path(os.getenv("EDIP_STATIC", Path(__file__).resolve().parent / "static"))
