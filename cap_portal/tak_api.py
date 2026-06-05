"""
TAK provisioning client.

Group and user management → TAK Portal REST API (Authentik-backed Express app).
DataSync missions        → TAK Server Marti API directly.

TAK Portal auth: Authentik reverse-proxy headers injected on every request.
The portal trusts these headers when called from the internal network
(bypassing the public Authentik proxy).
"""

import os
import random
import string
from pathlib import Path

import httpx

# ── TAK Portal (group + user management) ──────────────────────────────────────
_PORTAL_URL           = os.environ.get("TAK_PORTAL_URL",          "http://localhost:3000")
_PORTAL_ADMIN_USER    = os.environ.get("TAK_PORTAL_ADMIN_USER",   "admin")
_PORTAL_ADMIN_UID     = os.environ.get("TAK_PORTAL_ADMIN_UID",    "")
_PORTAL_ADMIN_GROUPS  = os.environ.get("TAK_PORTAL_ADMIN_GROUPS", "ak-admins")
_AGENCY_SUFFIX        = os.environ.get("TAK_AGENCY_SUFFIX",       "cap-il")
_MISSION_EMAIL        = os.environ.get("TAK_MISSION_EMAIL",       "")

# ── TAK Server Marti API (DataSync missions only) ──────────────────────────────
_TAK_HOST        = os.environ.get("TAK_HOST",        "localhost")
_TAK_PORT        = int(os.environ.get("TAK_ADMIN_PORT", "8443"))
_TAK_USER        = os.environ.get("TAK_ADMIN_USER",  "")
_TAK_PASS        = os.environ.get("TAK_ADMIN_PASS",  "")
_TAK_VERIFY_SSL  = os.environ.get("TAK_VERIFY_SSL",  "false").lower() == "true"
_TAK_CREATOR_UID = os.environ.get("TAK_CREATOR_UID", "cap-portal")
_CERT_P12        = Path(__file__).parent / "admin.p12"
_CERT_PASS       = os.environ.get("TAK_CERT_PASS",   "atakatak")
_BASE_URL        = f"https://{_TAK_HOST}:{_TAK_PORT}"

_TAK_SPECIAL = "-_!@#$%^&*(){}[]+=~`|:;<>,./?]"


# ── helpers ────────────────────────────────────────────────────────────────────

def _portal_client() -> httpx.Client:
    """httpx client for TAK Portal with Authentik proxy headers."""
    return httpx.Client(
        base_url=_PORTAL_URL,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-authentik-username": _PORTAL_ADMIN_USER,
            "X-authentik-uid":      _PORTAL_ADMIN_UID,
            "X-authentik-groups":   _PORTAL_ADMIN_GROUPS,
        },
        timeout=15.0,
    )


def _marti_client() -> httpx.Client:
    """httpx client for direct TAK Server Marti API (DataSync missions)."""
    cert = None
    if _CERT_P12.exists():
        try:
            from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
            from cryptography.hazmat.primitives.serialization.pkcs12 import load_pkcs12
            import tempfile
            p12 = load_pkcs12(_CERT_P12.read_bytes(), _CERT_PASS.encode())
            tmp_cert = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
            tmp_key  = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
            tmp_cert.write(p12.cert.certificate.public_bytes(Encoding.PEM)); tmp_cert.flush(); tmp_cert.close()
            tmp_key.write(p12.key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())); tmp_key.flush(); tmp_key.close()
            cert = (tmp_cert.name, tmp_key.name)
        except Exception:
            cert = None

    return httpx.Client(
        base_url=_BASE_URL,
        auth=(_TAK_USER, _TAK_PASS),
        verify=_TAK_VERIFY_SSL,
        cert=cert,
        timeout=15.0,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )


def _generate_password(length: int = 18) -> str:
    """Generate a password satisfying TAK Server's complexity policy."""
    rng = random.SystemRandom()
    pool = string.ascii_letters + string.digits + _TAK_SPECIAL
    required = [
        rng.choice(string.ascii_uppercase),
        rng.choice(string.ascii_lowercase),
        rng.choice(string.digits),
        rng.choice(_TAK_SPECIAL),
    ]
    rest = [rng.choice(pool) for _ in range(length - len(required))]
    chars = required + rest
    rng.shuffle(chars)
    return "".join(chars)


def _portal_raise(resp: httpx.Response, action: str) -> None:
    if resp.is_success:
        return
    try:
        body = resp.json()
    except Exception:
        body = resp.text[:300].strip() or "(empty body)"
    raise RuntimeError(f"TAK Portal HTTP {resp.status_code} on {action}: {body}")


# ── public API ─────────────────────────────────────────────────────────────────

def create_group(mission_number: str) -> dict:
    """
    Create a TAK group via TAK Portal.

    Portal name format: tak_<mission_number>
    Returns the Authentik group UUID needed by create_user().
    409 (already exists) is treated as success — returns existing group's UUID.
    """
    with _portal_client() as c:
        resp = c.post(
            "/api/groups",
            json={
                "name":            f"tak_{mission_number}",
                "description":     None,
                "groupType":       "Global",
                "groupTypeDetail": None,
            },
        )

    _already_exists = (
        resp.status_code == 409
        or (resp.status_code == 400 and "already exists" in (resp.text or "").lower())
    )
    if _already_exists:
        # Group already exists — fetch its UUID
        with _portal_client() as c:
            groups_resp = c.get("/api/groups")
        _portal_raise(groups_resp, f"list groups (resolving duplicate '{mission_number}')")
        groups = groups_resp.json() if isinstance(groups_resp.json(), list) else groups_resp.json().get("groups", [])
        target_name = f"tak_{mission_number}"
        match = next((g for g in groups if g["name"] == target_name), None)
        if not match:
            raise RuntimeError(f"Group '{target_name}' reported as duplicate but not found in group list")
        return {
            "group":      mission_number,
            "group_uuid": match["pk"],
            "http_status": resp.status_code,
        }

    _portal_raise(resp, f"create group '{mission_number}'")
    data = resp.json()
    return {
        "group":      mission_number,
        "group_uuid": data["group"]["pk"],
        "http_status": resp.status_code,
    }


def create_user(mission_number: str, group_uuid: str | None = None) -> dict:
    """
    Create a TAK user via TAK Portal.

    Args:
        mission_number: CAP mission number used as the badge (e.g. '26-T-4766').
        group_uuid:     Authentik group UUID from create_group(). If omitted,
                        create_group() is called automatically.

    Returns the generated password — display it once; it is not stored.
    """
    if group_uuid is None:
        group_result = create_group(mission_number)
        group_uuid = group_result["group_uuid"]

    password = _generate_password()

    with _portal_client() as c:
        resp = c.post(
            "/api/users",
            json={
                "badge":          mission_number,
                "agencySuffix":   _AGENCY_SUFFIX,
                "firstName":      "MISSION",
                "lastName":       mission_number,
                "email":          _MISSION_EMAIL,
                "password":       password,
                "radioCallsign":  "",
                "templateIndex":  "Manual Group Selection",
                "role":           "Team Member",
                "permissions":    "user",
                "manualGroupIds": [group_uuid],
            },
        )

    _portal_raise(resp, f"create user '{mission_number}'")
    data = resp.json()
    user_obj = data.get("user", {})
    username = user_obj.get("username", f"{mission_number.lower()}{_AGENCY_SUFFIX}")

    return {
        "username":   username,
        "user_id":    user_obj.get("pk", ""),
        "password":   password,
        "group":      mission_number,
        "group_uuid": group_uuid,
        "http_status": resp.status_code,
    }


def create_datasync_mission(name: str, group: str | None = None) -> dict:
    """
    Create a TAK DataSync mission via TAK Portal (which proxies to TAK Server Marti API).

    Args:
        name:  Full mission name (e.g. 'IL-2024-001-GROUND').
        group: Ignored — TAK Portal manages group access via Authentik.
    """
    body: dict = {"description": f"CAP Operation - {name}"}

    with _portal_client() as c:
        resp = c.put(f"/api/data-sync/missions/{name}", json=body)

    if not resp.is_success:
        body_text = resp.text[:300].strip() if resp.text else "(empty body)"
        raise RuntimeError(f"TAK Portal HTTP {resp.status_code} on create mission '{name}': {body_text}")

    return {
        "mission":    name,
        "group":      group,
        "http_status": resp.status_code,
    }
