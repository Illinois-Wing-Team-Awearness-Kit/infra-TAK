"""
EServices login + MFA flow for capnhq.gov.

Flow:
  1. GET login page  → extract __RequestVerificationToken
  2. POST credentials → 302 → MFASelection → 302 → MFAVerifyCode
  3. GET MFAVerifyCode page → extract key + fresh __RequestVerificationToken
  4. POST TOTP code   → 302 → WMIRS (session established)
"""

import re
import httpx
from typing import Optional

BASE_URL = "https://www.capnhq.gov"
WMIRS_PATH = "/WMIRS/Default.aspx"

LOGIN_URL = f"{BASE_URL}/Auth/Identity/Account/eServicesLogin"
MFA_POST_URL = f"{BASE_URL}/Auth/MyAccount/MFAVerifyCode"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class AuthError(Exception):
    pass


def _extract_token(html: str) -> str:
    """Extract __RequestVerificationToken from an HTML page."""
    match = re.search(
        r'<input[^>]+name="__RequestVerificationToken"[^>]+value="([^"]+)"',
        html,
    )
    if not match:
        raise AuthError("Could not find __RequestVerificationToken in page")
    return match.group(1)


def _extract_hidden(html: str, name: str) -> Optional[str]:
    """Extract a hidden input field value by name."""
    match = re.search(
        rf'<input[^>]+name="{re.escape(name)}"[^>]+value="([^"]*)"',
        html,
    )
    return match.group(1) if match else None


def login(capid: str, password: str, totp_code: str) -> httpx.Client:
    """
    Authenticate to EServices and return an httpx.Client with valid session cookies.

    Args:
        capid:     CAP ID number (as string)
        password:  EServices password — held in memory only, never persisted
        totp_code: Current 6-digit TOTP code from authenticator app

    Returns:
        httpx.Client with session cookies set, ready for WMIRS requests

    Raises:
        AuthError: on any failure in the auth flow
    """
    client = httpx.Client(
        headers=HEADERS,
        follow_redirects=False,
        timeout=30.0,
    )

    # Step 1: GET login page
    resp = client.get(f"{LOGIN_URL}?ReturnUrl=%2FWMIRS%2FDefault.aspx")
    if resp.status_code != 200:
        raise AuthError(f"Login page returned {resp.status_code}")
    csrf_token = _extract_token(resp.text)

    # Step 2: POST credentials
    resp = client.post(
        f"{LOGIN_URL}?ReturnUrl=%2FWMIRS%2FDefault.aspx",
        data={
            "Input.CAPID": capid,
            "__Invariant": "Input.CAPID",
            "Input.Password": password,
            "__RequestVerificationToken": csrf_token,
        },
    )
    if resp.status_code != 302:
        raise AuthError(f"Login POST returned {resp.status_code} (expected 302)")

    # Step 3: Follow MFASelection → MFAVerifyCode redirect chain
    location = resp.headers.get("location", "")
    if "/MFASelection" not in location:
        raise AuthError(f"Unexpected redirect after login: {location}")

    mfa_select_url = location if location.startswith("http") else f"{BASE_URL}{location}"
    resp = client.get(mfa_select_url)
    if resp.status_code != 302:
        raise AuthError(f"MFASelection returned {resp.status_code} (expected 302)")

    location = resp.headers.get("location", "")
    if "/MFAVerifyCode" not in location:
        raise AuthError(f"Unexpected redirect from MFASelection: {location}")

    mfa_verify_url = location if location.startswith("http") else f"{BASE_URL}{location}"

    # Extract UUID-B (key) from the MFAVerifyCode URL
    key_match = re.search(r"[?&]key=([0-9a-fA-F-]+)", mfa_verify_url)
    if not key_match:
        raise AuthError("Could not extract MFA key from redirect URL")
    mfa_key = key_match.group(1)

    return_url_match = re.search(r"[?&][Rr]eturn[Uu]rl=([^&]+)", mfa_verify_url)
    return_url = return_url_match.group(1) if return_url_match else "%2FWMIRS%2FDefault.aspx"

    # Step 4: GET MFAVerifyCode page for fresh CSRF token
    resp = client.get(mfa_verify_url)
    if resp.status_code != 200:
        raise AuthError(f"MFAVerifyCode page returned {resp.status_code}")
    mfa_csrf = _extract_token(resp.text)

    # Step 5: POST TOTP code
    resp = client.post(
        MFA_POST_URL,
        data={
            "Key": mfa_key,
            "ReturnURL": return_url,
            "Token": totp_code.strip(),
            "RememberVerification": "true",
            "__RequestVerificationToken": mfa_csrf,
        },
    )
    if resp.status_code != 302:
        raise AuthError(f"MFA POST returned {resp.status_code} (expected 302)")

    final_location = resp.headers.get("location", "")
    if "WMIRS" not in final_location and "wmirs" not in final_location.lower():
        raise AuthError(
            f"MFA POST did not redirect to WMIRS (got: {final_location}). "
            "TOTP code may be wrong or expired."
        )

    # Verify session by loading WMIRS
    wmirs_url = final_location if final_location.startswith("http") else f"{BASE_URL}{final_location}"
    resp = client.get(wmirs_url, follow_redirects=True)
    if resp.status_code != 200:
        raise AuthError(f"WMIRS page returned {resp.status_code} after auth")
    if "eServicesLogin" in str(resp.url):
        raise AuthError("Session not established — redirected back to login")

    return client
