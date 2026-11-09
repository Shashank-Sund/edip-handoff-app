"""FastAPI entry point: the REST API under /api and the built React client.

    cd app/server && uvicorn main:app --port 8000          (mock mode by default; see config.py)

Every read takes `at` (ISO time; default the replay clock) and the caller's persona: the X-Demo-Persona header in
mock mode, the signed in user in live mode. With the live clock a read is at the current time, or at an earlier time
within EDIP_PAST_VIEW_HOURS (read only, and logged in the view log), and every action is at the current time.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictBool, StrictStr

import config
from assignment import ROLES
from data import iso, load_fixtures, ts
from logic import ActionError, Views
from personas import demo_personas, mapped_units, parse_role_map, unit_id
from tasks import DISCIPLINE
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
# Who owns a patient: an owner idle for longer than this no longer counts; the shift changes and the banner before them.
OWNERSHIP = {"stale_owner": timedelta(hours=config.STALE_OWNER_HOURS), "shifts": config.SHIFT_CHANGES,
             "shift_warning": timedelta(minutes=config.SHIFT_WARNING_MINUTES)}
if config.MODE == "live":
    from lakebase import LakebaseStore, Reloading

    SOURCE = Reloading()
    STORE = LakebaseStore()               # does not fail when Lakebase is unreachable; it retries on use
    VIEWS = Views(SOURCE.get, STORE, _templates(), STALE_BOARDING, **OWNERSHIP)
    try:
        # Handoffs signed before tasks existed get their action list as tasks (safe to repeat; see backfill_tasks).
        _added = VIEWS.backfill_tasks()
        if _added:
            log.warning("Added %d tasks from handoffs signed before tasks existed", _added)
    except StoreUnavailable as exc:   # Lakebase unreachable at start up: the next start tries again
        log.warning("Tasks from earlier sign offs not checked: %s", exc)
else:
    STORE = MemoryStore()
    VIEWS = Views(load_fixtures(config.FIXTURES), STORE, _templates(), STALE_BOARDING, **OWNERSHIP)
ROLE_MAP, _problems = parse_role_map(config.ROLE_MAP)
for _p in _problems:
    log.warning(_p)
_derived: dict = {}
_units_seen: dict = {}                    # every receiving unit the data listed since start up
_warned: set = set()
_names: dict = {}                         # live: the display name each signed in email last came with


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


def _seed() -> None:
    """Mock mode: the demo's starting owners, Jordan Reyes for the nurse role and Kai Moreno for the physician role,
    before the default clock (logic.Views.seed_owners), and a few tasks on their patients (logic.Views.seed_tasks).
    Written at start up and again after Reset."""
    personas, default_at = _current()
    who = {p["id"]: {"id": p["id"], "name": p["name"]} for p in personas}
    owners = VIEWS.seed_owners(default_at, who["ed-rn"], who["ed-md-2"])
    for row in owners:
        STORE.assign(row)
    STORE.add_tasks(VIEWS.seed_tasks(default_at, owners))


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
        _names[email.strip().lower()] = p["name"]
    # Who the person is for owning patients: the persona with demo personas, else the signed in email.
    key = p["id"] if config.DEMO_PERSONAS or not email else email.strip().lower()
    return {**p, "user_email": email, "key": key}


def _colleagues(person: dict) -> list[dict]:
    """The people who can own a patient in the caller's ED role, the caller included: the demo personas of that role,
    or live every email the role map gives a persona of that role, named as they last signed in to this process, else
    as they last acted (app.handoff_action, app.assignment), else by their email."""
    if person["role"] not in ROLES:
        return []
    return [{"id": p["id"], "name": p["name"]} for p in _people() if p["role"] == person["role"]]


def _people() -> list[dict]:
    """Everyone with a clinical role, as their persona with id and name: the demo personas, or live every email the
    role map gives a persona, named as they last signed in to this process, else as they last acted (app.handoff_action,
    app.assignment, app.task_event), else by their email. Colleagues to own a patient (_colleagues), and who a task can
    go to (tasks.py)."""
    personas, _ = _current()
    if config.DEMO_PERSONAS:
        return [p for p in personas if p["role"] in DISCIPLINE]
    by_id = {p["id"]: p for p in personas}
    try:
        # Names from earlier actions, ownership changes and tasks, so a colleague keeps their name after a restart.
        saved = STORE.names()
    except StoreUnavailable:
        saved = {}
    return sorted(({**by_id[pid], "id": email, "name": _names.get(email) or saved.get(email) or email}
                   for email, pid in ROLE_MAP.items() if pid in by_id and by_id[pid]["role"] in DISCIPLINE),
                  key=lambda c: (c["name"].lower(), c["id"]))


# The live clock: a read may look back this far (none when 0); an `at` this close to now is now, as the clocks of
# devices differ by a little. Actions are always at the server's current time.
PAST_VIEW = timedelta(hours=config.PAST_VIEW_HOURS)
NOW_SLACK = timedelta(minutes=2)
READ_ONLY = "You are looking at an earlier time, which is read only. Press Back to live, then try again."


def _parse(at: str) -> datetime:
    try:
        return ts(at)
    except (ValueError, OverflowError):          # not ISO, or out of range in UTC (year 1 at +14:00)
        raise HTTPException(400, "at must be an ISO time")


def _at(at: Optional[str]) -> datetime:
    """The replay clock: `at` within the data's week, by default its busiest hour."""
    ds = VIEWS.ds
    if not at:
        return _current()[1]
    return min(max(_parse(at), ds.start), ds.end)


def _hours(td: timedelta) -> str:
    h = td.total_seconds() / 3600
    return f"{h:g} hour{'' if h == 1 else 's'}"


def _read_at(at: Optional[str], request: Request, page: str, csn: Optional[str] = None) -> datetime:
    """The time a read shows. Replay: the replay clock. Live: now, or an earlier time within EDIP_PAST_VIEW_HOURS,
    which is written to the view log first; when the log cannot be written the view is refused (StoreUnavailable)."""
    if config.CLOCK_MODE != "live":
        return _at(at)
    now = datetime.now(timezone.utc)
    if not at:
        return now
    t = _parse(at)
    if t >= now - NOW_SLACK:
        return now                            # now, or a later time: there is nothing later than now
    if not PAST_VIEW:
        raise HTTPException(400, "Looking at an earlier time is turned off in this app.")
    if t < now - PAST_VIEW:
        raise HTTPException(400, f"You can look back up to {_hours(PAST_VIEW)}. Pick a later time, or go back to live.")
    p = _person(request)
    STORE.log_view({"page": page, "csn": csn, "at": t, "by": p["name"], "role": p["role"], "user_email": p.get("user_email")})
    return t


def _act_at(at: Optional[str]) -> datetime:
    """The time an action applies to. Replay: the replay clock. Live: the server's current time, never an earlier
    one; an action sent while the client looks at an earlier time is refused."""
    if config.CLOCK_MODE != "live":
        return _at(at)
    now = datetime.now(timezone.utc)
    if at and _parse(at) < now - NOW_SLACK:
        raise HTTPException(409, READ_ONLY)
    return now


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


class OwnerBody(BaseModel):
    kind: StrictStr                             # claim (Take), assign or release
    to: Optional[StrictStr] = None              # assign: the colleague's id (/api/colleagues)


class TakeoverBody(BaseModel):
    colleague: StrictStr
    csns: list[StrictStr]


class TaskBody(BaseModel):
    text: StrictStr
    role: Optional[StrictStr] = None            # nurse or physician; with `to`, the colleague's role
    to: Optional[StrictStr] = None              # a colleague's id (the task's people)
    due: Optional[StrictStr] = None             # ISO time


class TaskChangeBody(BaseModel):
    kind: StrictStr                             # done, not_needed, reassign, due or undo
    reason: Optional[StrictStr] = None          # not_needed
    role: Optional[StrictStr] = None            # reassign
    to: Optional[StrictStr] = None              # reassign
    due: Optional[StrictStr] = None             # due: ISO time, or none to clear it


FIELD = {"element": "item", "text": "text", "items": "action items", "restore": "restore choice", "rule": "rule",
         "option": "attestation", "entry": "entered value", "entry_note": "note", "remove": "remove choice", "callback": "callback number", "readback": "read back", "questions": "questions",
         "kind": "choice of Take, Assign or Release", "to": "colleague", "colleague": "colleague", "csns": "patients",
         "role": "role", "due": "due time", "reason": "reason"}
# A change to a task (/api/tasks/<id>) names its kind differently.
TASK_FIELD = FIELD | {"kind": "task change"}


def plain_validation(errors: list[dict], fields: dict = FIELD) -> str:
    """A request body the API cannot use, in one plain sentence (the static demo says the same)."""
    e = errors[0] if errors else {}
    loc = [x for x in e.get("loc", ()) if x != "body"]
    if not loc or not isinstance(loc[0], str):
        return "The request could not be read. Reload the page and try again."
    label = fields.get(loc[0], loc[0])
    if e.get("type") == "missing" and len(loc) == 1:
        return f"The request is missing the {label}."
    return f"The request has a value the app cannot use for the {label}."


@app.exception_handler(RequestValidationError)
async def _invalid(request: Request, exc: RequestValidationError):
    fields = TASK_FIELD if request.url.path.startswith("/api/tasks/") else FIELD
    return JSONResponse({"detail": plain_validation(exc.errors(), fields)}, status_code=422)


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
            **({"past_view_hours": config.PAST_VIEW_HOURS} if config.CLOCK_MODE == "live" else {}),
            "shifts": {"changes": [f"{h:02d}:{m:02d}" for h, m in config.SHIFT_CHANGES],
                       "warning_minutes": config.SHIFT_WARNING_MINUTES, "stale_owner_hours": config.STALE_OWNER_HOURS},
            "templates": [{"id": "sbar_nurse", "name": "SBAR", "audience": "Nurse"},
                          {"id": "ipass_physician", "name": "I-PASS", "audience": "Physician"},
                          {"id": "custom_sea58", "name": "Joint Commission minimum", "audience": "Any"}]}


@api.get("/templates")
def get_templates() -> list[dict[str, Any]]:
    return _templates()


@api.get("/board")
def get_board(request: Request, at: Optional[str] = None) -> dict[str, Any]:
    return VIEWS.board(_read_at(at, request, "board"), _person(request))


@api.get("/handoffs/{csn}")
def get_case(csn: str, request: Request, at: Optional[str] = None) -> dict[str, Any]:
    c = VIEWS.case(csn, _read_at(at, request, "handoff", csn), _person(request))
    if c is None:
        raise HTTPException(404, "This patient is not on your board at this time.")
    return c


@api.get("/analytics")
def get_analytics(request: Request, at: Optional[str] = None) -> dict[str, Any]:
    return VIEWS.analytics(_read_at(at, request, "analytics"))


def _act(kind: str, csn: str, template: str, request: Request, at: Optional[str], body: dict) -> dict[str, Any]:
    try:
        return VIEWS.act(kind, csn, template, _act_at(at), _person(request), body)
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


@api.post("/handoffs/{csn}/owner")
def post_owner(csn: str, body: OwnerBody, request: Request, at: Optional[str] = None):
    """Take, Assign to a colleague, or Release the patient, for the caller's ED role."""
    t, p = _act_at(at), _person(request)
    try:
        return VIEWS.own(body.kind, csn, t, p, body.to, _colleagues(p))
    except ActionError as exc:
        raise HTTPException(409, str(exc))


@api.get("/colleagues")
def get_colleagues(request: Request) -> list[dict[str, Any]]:
    """Who can own a patient in the caller's ED role (the Assign list); empty for other roles."""
    p = _person(request)
    return [c | {"you": c["id"] == p["key"]} for c in _colleagues(p)]


@api.get("/takeover")
def get_takeover(request: Request, at: Optional[str] = None, colleague: Optional[str] = None) -> dict[str, Any]:
    t, p = _read_at(at, request, "takeover"), _person(request)
    try:
        return VIEWS.takeover(t, p, colleague, _colleagues(p))
    except ActionError as exc:
        raise HTTPException(409, str(exc))


@api.post("/takeover")
def post_takeover(body: TakeoverBody, request: Request, at: Optional[str] = None) -> dict[str, Any]:
    t, p = _act_at(at), _person(request)
    try:
        return VIEWS.take_over(t, p, body.colleague, body.csns, _colleagues(p))
    except ActionError as exc:
        raise HTTPException(409, str(exc))


@api.get("/tasks")
def get_tasks(request: Request, at: Optional[str] = None, scope: str = "mine") -> dict[str, Any]:
    """The Tasks page: the caller's open tasks across patients (scope mine), or every task on their board (all)."""
    if scope not in ("mine", "all"):
        raise HTTPException(400, "scope must be mine or all")
    return VIEWS.my_tasks(_read_at(at, request, "tasks"), _person(request), scope)


@api.get("/handoffs/{csn}/tasks")
def get_patient_tasks(csn: str, request: Request, at: Optional[str] = None) -> dict[str, Any]:
    t = VIEWS.patient_tasks(csn, _read_at(at, request, "handoff", csn), _person(request), _people())
    if t is None:
        raise HTTPException(404, "This patient is not on your board at this time.")
    return t


@api.post("/handoffs/{csn}/tasks")
def post_task(csn: str, body: TaskBody, request: Request, at: Optional[str] = None) -> dict[str, Any]:
    """A task added by hand; returns the patient's tasks."""
    t, p = _act_at(at), _person(request)
    try:
        return VIEWS.add_task(csn, t, p, body.model_dump(), _people())
    except ActionError as exc:
        raise HTTPException(409, str(exc))


@api.post("/tasks/{task_id}")
def post_task_change(task_id: str, body: TaskChangeBody, request: Request, at: Optional[str] = None) -> dict[str, Any]:
    """Done, not needed, reassign, a new due time, or undo; returns the patient's tasks."""
    t, p = _act_at(at), _person(request)
    try:
        return VIEWS.task_act(int(task_id) if re.fullmatch(r"[0-9]{1,15}", task_id) else -1, t, p, body.model_dump(), _people())
    except ActionError as exc:
        raise HTTPException(409, str(exc))


@api.get("/me")
def get_me(request: Request) -> dict[str, Any]:
    return _person(request)


@api.post("/demo/reset")
def post_reset() -> dict[str, str]:
    if config.MODE != "mock":
        raise HTTPException(403, "reset is only available in mock mode")
    STORE.reset()
    _seed()
    return {"status": "reset"}


@api.get("/health")
def health() -> dict[str, Any]:
    ds = VIEWS.ds
    return {"status": "ok" if not getattr(ds, "error", None) else "not ready", "detail": getattr(ds, "error", None),
            "mode": config.MODE, "store": STORE.kind, "encounters": len(ds.enc),
            "drafts": sum(len(v) for v in ds.versions.values()), "now": iso(datetime.now(timezone.utc))}


app.include_router(api)
if config.MODE == "mock":
    _seed()

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
