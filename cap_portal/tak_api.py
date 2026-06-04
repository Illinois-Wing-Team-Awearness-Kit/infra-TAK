"""
TAK Server Marti REST API client.

Wraps the admin endpoints used to provision TAK resources for CAP missions:
  - /user-management/api/new-user          (create TAK user)
  - /user-management/api/update-group-users (create/update group access)
  - /Marti/api/missions/{name}             (create DataSync mission)

Auth: HTTP Basic with the TAK admin account (webadmin / TAK_ADMIN_PASS).
SSL:  Verification disabled by default — TAK Server commonly uses self-signed certs.
      Set TAK_VERIFY_SSL=true if a valid cert is present.
"""

import os
import secrets

import httpx

_TAK_HOST = os.environ.get("TAK_HOST", "localhost")
_TAK_PORT = int(os.environ.get("TAK_ADMIN_PORT", "8443"))
_TAK_USER = os.environ.get("TAK_ADMIN_USER", "")
_TAK_PASS = os.environ.get("TAK_ADMIN_PASS", "")
_TAK_VERIFY_SSL = os.environ.get("TAK_VERIFY_SSL", "false").lower() == "true"
_TAK_CREATOR_UID = os.environ.get("TAK_CREATOR_UID", "cap-portal")

_BASE_URL = f"https://{_TAK_HOST}:{_TAK_PORT}"


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=_BASE_URL,
        auth=(_TAK_USER, _TAK_PASS),
        verify=_TAK_VERIFY_SSL,
        timeout=15.0,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )


def _raise_for_status(resp: httpx.Response, action: str) -> None:
    if resp.is_success:
        return
    body = resp.text[:300].strip() if resp.text else "(empty body)"
    raise RuntimeError(f"TAK Server HTTP {resp.status_code} on {action}: {body}")


def create_group(mission_number: str) -> dict:
    """
    Create (or update) a TAK group with both IN and OUT directions — bidirectional R/W.

    IN  direction: devices in this group can send data to TAK Server.
    OUT direction: TAK Server pushes data to devices in this group.
    Together they give full read/write channel access.
    """
    with _client() as c:
        resp = c.put(
            "/user-management/api/update-group-users",
            json={
                "groupname": mission_number,
                "usersInGroupListIN": [],
                "usersInGroupListOUT": [],
            },
        )
    _raise_for_status(resp, f"create group '{mission_number}'")
    return {
        "group": mission_number,
        "directions": ["IN", "OUT"],
        "http_status": resp.status_code,
    }


def create_user(mission_number: str) -> dict:
    """
    Create a TAK user named <mission_number>, automatically assigned to
    the matching group with R/W (IN + OUT) access.

    Returns the generated password — display it to the admin; it is not stored.
    """
    password = secrets.token_urlsafe(16)
    with _client() as c:
        resp = c.post(
            "/user-management/api/new-user",
            json={
                "username": mission_number,
                "password": password,
                "groupListIN": [mission_number],
                "groupListOUT": [mission_number],
            },
        )
    _raise_for_status(resp, f"create user '{mission_number}'")
    return {
        "username": mission_number,
        "password": password,
        "group": mission_number,
        "http_status": resp.status_code,
    }


def create_datasync_mission(name: str, group: str | None = None) -> dict:
    """
    Create a TAK DataSync mission.

    Args:
        name:  Full mission name (e.g. 'IL-2024-001-GROUND').
        group: TAK group to associate the mission with (optional).
    """
    params: dict = {
        "creatorUid": _TAK_CREATOR_UID,
        "description": f"CAP Operation — {name}",
    }
    if group:
        params["group"] = group

    with _client() as c:
        resp = c.put(f"/Marti/api/missions/{name}", params=params)
    _raise_for_status(resp, f"create mission '{name}'")
    return {
        "mission": name,
        "group": group,
        "http_status": resp.status_code,
    }
