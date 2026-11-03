"""FastAPI entry point: the REST API under /api and the built React client.

    cd app/server && uvicorn main:app --port 8000          (mock mode by default; see config.py)

Every read takes `at` (ISO time; default the replay clock) and the caller's persona: the X-Demo-Persona header in
mock mode, the signed in user in live mode.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from data import iso, load_fixtures, ts
from logic import ActionError, Views
from personas import demo_personas
from workflow import MemoryStore

app = FastAPI(title="ED to inpatient handoff", docs_url="/api/docs", openapi_url="/api/openapi.json")
api = APIRouter(prefix="/api")

if config.MODE == "live":
    from lakebase import LakebaseStore, Reloading

    SOURCE = Reloading()
    STORE = LakebaseStore()
    VIEWS = Views(SOURCE.get, STORE)
else:
    STORE = MemoryStore()
    VIEWS = Views(load_fixtures(config.FIXTURES), STORE)
DS = VIEWS.ds                     # the data at start up; views always read VIEWS.ds (live mode reloads it)
PERSONAS = demo_personas(DS.meta.get("units", []))
ROLE_MAP = json.loads(config.ROLE_MAP or "{}")
DEFAULT_AT = ts(config.CLOCK) if config.CLOCK else DS.busiest()


def _person(request: Request) -> dict:
    email = request.headers.get("x-forwarded-email")
    pid = (request.headers.get("x-demo-persona") if config.DEMO_PERSONAS else ROLE_MAP.get(email or "")) or "ed-rn"
    p = next((x for x in PERSONAS if x["id"] == pid), None)
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
        return DEFAULT_AT
    try:
        t = ts(at)
    except ValueError:
        raise HTTPException(400, "at must be an ISO time")
    return min(max(t, ds.start), ds.end)


class EditBody(BaseModel):
    element: str
    text: Optional[str] = None
    items: Optional[list[dict[str, Any]]] = None


class AttestBody(BaseModel):
    rule: str
    option: str


class SignBody(BaseModel):
    callback: str


class AckBody(BaseModel):
    readback: str
    questions: Optional[str] = None


@api.get("/config")
def get_config() -> dict[str, Any]:
    ds = VIEWS.ds
    return {"mode": config.MODE, "clock": config.CLOCK_MODE, "demo_personas": config.DEMO_PERSONAS,
            "source": ds.source, "drafts_from": ds.meta.get("drafts_from"),
            "models": ds.meta.get("models", []), "window": {"start": iso(ds.start), "end": iso(ds.end)},
            "default_at": iso(DEFAULT_AT), "facilities": ds.meta.get("facilities", []), "units": ds.meta.get("units", []),
            "personas": PERSONAS if config.DEMO_PERSONAS else [], "store": STORE.kind,
            "templates": [{"id": "sbar_nurse", "name": "SBAR", "audience": "Nurse"},
                          {"id": "ipass_physician", "name": "I-PASS", "audience": "Physician"},
                          {"id": "custom_sea58", "name": "Joint Commission minimum", "audience": "Any"}]}


@api.get("/templates")
def get_templates() -> list[dict[str, Any]]:
    import yaml

    elements = yaml.safe_load((config.TEMPLATES / "elements.yaml").read_text())["elements"]
    out = []
    for tid in ("sbar_nurse", "ipass_physician", "custom_sea58"):
        t = yaml.safe_load((config.TEMPLATES / f"{tid}.yaml").read_text())
        out.append({"id": tid, **{k: t.get(k) for k in ("name", "audience", "description", "sections", "rules", "narrative")},
                    "elements": {e: elements[e] for s in t["sections"] for e in s["elements"]}})
    return out


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
    return {"status": "ok", "mode": config.MODE, "store": STORE.kind, "encounters": len(ds.enc),
            "drafts": sum(len(v) for v in ds.versions.values()), "now": iso(datetime.now(timezone.utc))}


app.include_router(api)

if (config.STATIC / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=config.STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = config.STATIC / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(config.STATIC / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def no_client():
        return JSONResponse({"message": "API only: build the client (app/client) or run its dev server."})
