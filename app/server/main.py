"""FastAPI entry point: the REST API under /api and the built React client.

    cd app/server && uvicorn main:app --port 8000          (mock mode by default; see config.py)

Every read takes `at` (ISO time; default the replay clock) and the caller's persona: the X-Demo-Persona header in
mock mode, the signed in user in live mode.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictBool, StrictStr

import config
from data import iso, load_fixtures, ts
from logic import ActionError, Views
from personas import demo_personas, mapped_units, parse_role_map, unit_id
from workflow import MemoryStore, StoreUnavailable

log = logging.getLogger("edip")
app = FastAPI(title="ED to inpatient handoff", docs_url="/api/docs", openapi_url="/api/openapi.json")
api = APIRouter(prefix="/api")
TEMPLATE_IDS = ("sbar_nurse", "ipass_physician", "custom_sea58")


@lru_cache(maxsize=1)
def _templates() -> list[dict]:
    import yaml

    elements = yaml.safe_load((config.TEMPLATES / "elements.yaml").read_text())["elements"]
    out = []
    for tid in TEMPLATE_IDS:
        t = yaml.safe_load((config.TEMPLATES / f"{tid}.yaml").read_text())
        out.append({"id": tid, **{k: t.get(k) for k in ("name", "audience", "description", "sections", "rules", "narrative")},
                    "elements": {e: elements[e] for s in t["sections"] for e in s["elements"]}})
    return out


# With the live clock, a patient with no ED departure long after the admit decision is a departure the feeds missed.
STALE_BOARDING = timedelta(hours=config.STALE_BOARDING_HOURS) if config.CLOCK_MODE == "live" else None
if config.MODE == "live":
    from lakebase import LakebaseStore, Reloading

    SOURCE = Reloading()
    STORE = LakebaseStore()               # does not fail when Lakebase is unreachable; it retries on use
    VIEWS = Views(SOURCE.get, STORE, _templates(), STALE_BOARDING)
else:
    STORE = MemoryStore()
    VIEWS = Views(load_fixtures(config.FIXTURES), STORE, _templates(), STALE_BOARDING)
ROLE_MAP, _problems = parse_role_map(config.ROLE_MAP)
for _p in _problems:
    log.warning(_p)
_derived: dict = {}
_units_seen: dict = {}                    # every receiving unit the data listed since start up
_warned: set = set()


def _current():
    """Personas and the default replay time follow the data, which live mode loads (and reloads) after start up.
    A unit persona stays once seen, or when the role map names it, even when the unit has no patients now."""
    ds = VIEWS.ds
    if _derived.get("ds") is not ds:
        for u in ds.meta.get("units", []):
            _units_seen[unit_id(u["facility"], u["code"])] = u
        units = list(_units_seen.values()) + mapped_units(ROLE_MAP, set(_units_seen))
        _derived.update(ds=ds, personas=demo_personas(units),
                        default_at=ts(config.CLOCK) if config.CLOCK else ds.busiest())
        if not config.DEMO_PERSONAS:
            known = {p["id"] for p in _derived["personas"]}
            for email, pid in ROLE_MAP.items():
                if pid not in known and pid not in _warned:
                    _warned.add(pid)
                    log.warning("EDIP_ROLE_MAP: %s maps to %s, which is not a persona; they get the viewer role", email, pid)
    return _derived["personas"], _derived["default_at"]


def _person(request: Request) -> dict:
    email = request.headers.get("x-forwarded-email")
    personas, _ = _current()
    if config.DEMO_PERSONAS:
        pid = request.headers.get("x-demo-persona") or "ed-rn"
    else:
        pid = ROLE_MAP.get((email or "").strip().lower(), "viewer")
    p = next((x for x in personas if x["id"] == pid), None)
    if p is None and not config.DEMO_PERSONAS:
        if pid not in _warned:
            _warned.add(pid)
            log.warning("EDIP_ROLE_MAP: persona %s does not exist; %s gets the viewer role", pid, email)
        p = next(x for x in personas if x["id"] == "viewer")
    if p is None:
        raise HTTPException(400, f"unknown persona {pid}")
    if not config.DEMO_PERSONAS and email:
        p = {**p, "name": request.headers.get("x-forwarded-preferred-username") or email}
    return {**p, "user_email": email}


def _at(at: Optional[str]) -> datetime:
    if config.CLOCK_MODE == "live":
        return datetime.now(timezone.utc)
    ds = VIEWS.ds
    if not at:
        return _current()[1]
    try:
        t = ts(at)
    except ValueError:
        raise HTTPException(400, "at must be an ISO time")
    return min(max(t, ds.start), ds.end)


class Item(BaseModel):
    text: StrictStr
    owner: Optional[StrictStr] = None
    due: Optional[StrictStr] = None


class EditBody(BaseModel):
    element: StrictStr
    text: Optional[StrictStr] = None
    items: Optional[list[Item]] = None
    restore: Optional[StrictBool] = None


class AttestBody(BaseModel):
    rule: StrictStr
    option: StrictStr
    entry: Optional[StrictStr] = None           # the words for a typed choice: the isolation type, the allergen
    entry_note: Optional[StrictStr] = None      # its note, such as the reaction to the allergen
    remove: Optional[StrictBool] = None         # take an entered allergy (entry) off the list


class SignBody(BaseModel):
    callback: StrictStr


class AckBody(BaseModel):
    readback: StrictStr
    questions: Optional[StrictStr] = None


FIELD = {"element": "item", "text": "text", "items": "action items", "restore": "restore choice", "rule": "rule",
         "option": "attestation", "entry": "entered value", "entry_note": "note", "remove": "remove choice", "callback": "callback number", "readback": "read back", "questions": "questions"}


def plain_validation(errors: list[dict]) -> str:
    """A request body the API cannot use, in one plain sentence (the static demo says the same)."""
    e = errors[0] if errors else {}
    loc = [x for x in e.get("loc", ()) if x != "body"]
    if not loc or not isinstance(loc[0], str):
        return "The request could not be read. Reload the page and try again."
    label = FIELD.get(loc[0], loc[0])
    if e.get("type") == "missing" and len(loc) == 1:
        return f"The request is missing the {label}."
    return f"The request has a value the app cannot use for the {label}."


@app.exception_handler(RequestValidationError)
async def _invalid(request: Request, exc: RequestValidationError):
    return JSONResponse({"detail": plain_validation(exc.errors())}, status_code=422)


@app.exception_handler(StoreUnavailable)
async def _unavailable(request: Request, exc: StoreUnavailable):
    return JSONResponse({"detail": str(exc)}, status_code=503)


@api.get("/config")
def get_config() -> dict[str, Any]:
    ds = VIEWS.ds
    personas, default_at = _current()
    return {"mode": config.MODE, "clock": config.CLOCK_MODE, "demo_personas": config.DEMO_PERSONAS,
            "ready": not getattr(ds, "error", None), "detail": getattr(ds, "error", None),
            "source": ds.source, "drafts_from": ds.meta.get("drafts_from"),
            "models": ds.meta.get("models", []), "window": {"start": iso(ds.start), "end": iso(ds.end)},
            "default_at": iso(default_at), "facilities": ds.meta.get("facilities", []), "units": ds.meta.get("units", []),
            "personas": personas if config.DEMO_PERSONAS else [], "store": STORE.kind, "timezone": config.TIMEZONE,
            "templates": [{"id": "sbar_nurse", "name": "SBAR", "audience": "Nurse"},
                          {"id": "ipass_physician", "name": "I-PASS", "audience": "Physician"},
                          {"id": "custom_sea58", "name": "Joint Commission minimum", "audience": "Any"}]}


@api.get("/templates")
def get_templates() -> list[dict[str, Any]]:
    return _templates()


@api.get("/board")
def get_board(request: Request, at: Optional[str] = None) -> dict[str, Any]:
    return VIEWS.board(_at(at), _person(request))


@api.get("/handoffs/{csn}")
def get_case(csn: str, request: Request, at: Optional[str] = None) -> dict[str, Any]:
    c = VIEWS.case(csn, _at(at), _person(request))
    if c is None:
        raise HTTPException(404, "This patient is not on your board at this time.")
    return c


@api.get("/analytics")
def get_analytics(at: Optional[str] = None) -> dict[str, Any]:
    return VIEWS.analytics(_at(at))


def _act(kind: str, csn: str, template: str, request: Request, at: Optional[str], body: dict) -> dict[str, Any]:
    try:
        return VIEWS.act(kind, csn, template, _at(at), _person(request), body)
    except ActionError as exc:
        raise HTTPException(409, str(exc))


@api.post("/handoffs/{csn}/{template}/edit")
def post_edit(csn: str, template: str, body: EditBody, request: Request, at: Optional[str] = None):
    return _act("edit", csn, template, request, at, body.model_dump())


@api.post("/handoffs/{csn}/{template}/attest")
def post_attest(csn: str, template: str, body: AttestBody, request: Request, at: Optional[str] = None):
    return _act("attest", csn, template, request, at, body.model_dump())


@api.post("/handoffs/{csn}/{template}/sign")
def post_sign(csn: str, template: str, body: SignBody, request: Request, at: Optional[str] = None):
    return _act("sign", csn, template, request, at, body.model_dump())


@api.post("/handoffs/{csn}/{template}/ack")
def post_ack(csn: str, template: str, body: AckBody, request: Request, at: Optional[str] = None):
    return _act("ack", csn, template, request, at, body.model_dump())


@api.get("/me")
def get_me(request: Request) -> dict[str, Any]:
    return _person(request)


@api.post("/demo/reset")
def post_reset() -> dict[str, str]:
    if config.MODE != "mock":
        raise HTTPException(403, "reset is only available in mock mode")
    STORE.reset()
    return {"status": "reset"}


@api.get("/health")
def health() -> dict[str, Any]:
    ds = VIEWS.ds
    return {"status": "ok" if not getattr(ds, "error", None) else "not ready", "detail": getattr(ds, "error", None),
            "mode": config.MODE, "store": STORE.kind, "encounters": len(ds.enc),
            "drafts": sum(len(v) for v in ds.versions.values()), "now": iso(datetime.now(timezone.utc))}


app.include_router(api)

if (config.STATIC / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=config.STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        # Serve only files inside the static folder: a path with ../ (or %2e%2e) must never reach other files.
        root = config.STATIC.resolve()
        f = (root / path).resolve()
        if path and f.is_relative_to(root) and f.is_file():
            return FileResponse(f)
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(root / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def no_client():
        return JSONResponse({"message": "API only: build the client (app/client) or run its dev server."})
