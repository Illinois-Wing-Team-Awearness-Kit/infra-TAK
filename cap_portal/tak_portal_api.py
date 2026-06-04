"""
Optional TAK Portal (AdventureSeeker423/TAK-Portal) client.
Used only for enrollment QR code retrieval.
Calls are made with Authentik proxy headers so the backend
acts as the admin user without going through Caddy.
"""

import os
import httpx

TAK_PORTAL_URL    = os.environ.get("TAK_PORTAL_URL", "")
ADMIN_USER        = os.environ.get("TAK_PORTAL_ADMIN_USER", "admin")
ADMIN_UID         = os.environ.get("TAK_PORTAL_ADMIN_UID", "")
ADMIN_GROUPS      = os.environ.get("TAK_PORTAL_ADMIN_GROUPS", "ak-admins")

_HEADERS = {
    "x-authentik-username": ADMIN_USER,
    "x-authentik-uid":      ADMIN_UID,
    "x-authentik-groups":   ADMIN_GROUPS,
    "Content-Type":         "application/json",
}


def is_configured() -> bool:
    return bool(TAK_PORTAL_URL and ADMIN_UID)


def get_enrollment_qr(user_id: str, username: str) -> dict:
    """
    Returns {"enrollUrl": str, "qrCode": str (base64 PNG), "token": str, "expiresAt": str}
    """
    url = f"{TAK_PORTAL_URL.rstrip('/')}/api/users/{user_id}/enroll-qr"
    resp = httpx.post(url, headers=_HEADERS, json={"username": username}, timeout=15)
    resp.raise_for_status()
    return resp.json()
