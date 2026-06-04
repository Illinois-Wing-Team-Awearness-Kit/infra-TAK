"""
CAP Command Portal — FastAPI entry point.

Routes:
  GET  /            → redirect to /login or /portal
  GET  /login       → login page
  POST /login       → WMIRS EServices auth + set session
  POST /logout      → clear session
  GET  /portal      → admin dashboard (requires admin session)

  POST /api/group          {"mission_number": "..."}  → create TAK group
  POST /api/user           {"mission_number": "..."}  → create TAK user
  POST /api/mission/ground {"mission_number": "..."}  → create GROUND DataSync mission
  POST /api/mission/air    {"mission_number": "..."}  → create AIR DataSync mission
  POST /api/setup          {"mission_number": "..."}  → run all four operations
"""

import json
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from wmirs_auth.eservices import login as wmirs_login, AuthError
from wmirs_auth.session_store import save_session
import tak_api

_DIR = Path(__file__).parent

SECRET_KEY = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)

app = FastAPI(title="CAP Command Portal", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="cap_portal_session",
    max_age=4 * 3600,
    same_site="strict",
    https_only=False,
)
app.mount("/static", StaticFiles(directory=str(_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_DIR / "templates"))


def _load_admins() -> list[str]:
    try:
        data = json.loads((_DIR / "admin_capids.json").read_text())
        return [str(c) for c in data.get("admins", [])]
    except Exception:
        return []


def _is_admin(request: Request) -> bool:
    return bool(request.session.get("capid") and request.session.get("is_admin"))


def _auth_guard(request: Request) -> JSONResponse | None:
    """Return a 401 JSON response if the session is not an admin."""
    if not _is_admin(request):
        return JSONResponse({"success": False, "message": "Not authorized"}, status_code=401)
    return None


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return RedirectResponse("/portal" if _is_admin(request) else "/login")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if _is_admin(request):
        return RedirectResponse("/portal")
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login_submit(
    request: Request,
    capid: str = Form(...),
    password: str = Form(...),
    totp_code: str = Form(...),
):
    capid = capid.strip()
    try:
        client = wmirs_login(capid, password, totp_code.strip())
    except AuthError as e:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": str(e)},
            status_code=401,
        )

    save_session(capid, client)
    admins = _load_admins()
    request.session["capid"] = capid
    request.session["is_admin"] = capid in admins

    return RedirectResponse("/portal", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/portal", response_class=HTMLResponse)
def portal_page(request: Request):
    if not _is_admin(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        "portal.html",
        {"request": request, "capid": request.session["capid"]},
    )


# ── API ────────────────────────────────────────────────────────────────────────

class MissionRequest(BaseModel):
    mission_number: str


@app.post("/api/group")
def api_create_group(request: Request, body: MissionRequest):
    if (err := _auth_guard(request)):
        return err
    mn = body.mission_number.strip()
    if not mn:
        return JSONResponse({"success": False, "message": "Mission number required"}, status_code=400)
    try:
        data = tak_api.create_group(mn)
        return JSONResponse({"success": True, "message": f"Group '{mn}' created (IN/OUT)", "data": data})
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/user")
def api_create_user(request: Request, body: MissionRequest):
    if (err := _auth_guard(request)):
        return err
    mn = body.mission_number.strip()
    if not mn:
        return JSONResponse({"success": False, "message": "Mission number required"}, status_code=400)
    try:
        data = tak_api.create_user(mn)
        return JSONResponse({"success": True, "message": f"User '{mn}' created", "data": data})
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/mission/ground")
def api_create_ground(request: Request, body: MissionRequest):
    if (err := _auth_guard(request)):
        return err
    mn = body.mission_number.strip()
    if not mn:
        return JSONResponse({"success": False, "message": "Mission number required"}, status_code=400)
    name = f"{mn}-GROUND"
    try:
        data = tak_api.create_datasync_mission(name, group=mn)
        return JSONResponse({"success": True, "message": f"DataSync mission '{name}' created", "data": data})
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/mission/air")
def api_create_air(request: Request, body: MissionRequest):
    if (err := _auth_guard(request)):
        return err
    mn = body.mission_number.strip()
    if not mn:
        return JSONResponse({"success": False, "message": "Mission number required"}, status_code=400)
    name = f"{mn}-AIR"
    try:
        data = tak_api.create_datasync_mission(name, group=mn)
        return JSONResponse({"success": True, "message": f"DataSync mission '{name}' created", "data": data})
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/setup")
def api_setup_all(request: Request, body: MissionRequest):
    if (err := _auth_guard(request)):
        return err
    mn = body.mission_number.strip()
    if not mn:
        return JSONResponse({"success": False, "message": "Mission number required"}, status_code=400)

    ops = [
        ("group",         lambda: tak_api.create_group(mn)),
        ("user",          lambda: tak_api.create_user(mn)),
        ("ground_mission", lambda: tak_api.create_datasync_mission(f"{mn}-GROUND", mn)),
        ("air_mission",   lambda: tak_api.create_datasync_mission(f"{mn}-AIR", mn)),
    ]

    results: dict = {}
    overall_success = True
    for label, fn in ops:
        try:
            results[label] = {"success": True, "data": fn()}
        except Exception as e:
            results[label] = {"success": False, "error": str(e)}
            overall_success = False

    return JSONResponse({
        "success": overall_success,
        "message": "Mission provisioning complete" if overall_success else "Provisioning completed with errors",
        "results": results,
    })
