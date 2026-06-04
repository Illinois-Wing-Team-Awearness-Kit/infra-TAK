"""
Encrypted per-user session store.

Stores httpx cookie jars as encrypted JSON blobs so sessions survive
process restarts without persisting plaintext credentials.

Each entry: { capid: { cookies: {name: value, ...}, expires_at: timestamp } }
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken

SESSION_TTL = 4 * 3600  # 4 hours

_STORE_PATH = Path(os.environ.get("SESSION_STORE_PATH", ".session_store.enc"))
_KEY_PATH = Path(os.environ.get("SESSION_KEY_PATH", ".session.key"))


def _load_or_create_key() -> bytes:
    if _KEY_PATH.exists():
        return _KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    _KEY_PATH.write_bytes(key)
    try:
        _KEY_PATH.chmod(0o600)
    except Exception:
        pass
    return key


def _fernet() -> Fernet:
    return Fernet(_load_or_create_key())


def _load_store() -> dict:
    if not _STORE_PATH.exists():
        return {}
    try:
        plaintext = _fernet().decrypt(_STORE_PATH.read_bytes())
        return json.loads(plaintext)
    except (InvalidToken, json.JSONDecodeError):
        return {}


def _save_store(store: dict) -> None:
    plaintext = json.dumps(store).encode()
    _STORE_PATH.write_bytes(_fernet().encrypt(plaintext))
    try:
        _STORE_PATH.chmod(0o600)
    except Exception:
        pass


def save_session(capid: str, client: httpx.Client) -> None:
    """Persist the session cookies from an authenticated httpx.Client."""
    store = _load_store()
    cookies = {name: value for name, value in client.cookies.items()}
    store[str(capid)] = {
        "cookies": cookies,
        "expires_at": (time.time() + SESSION_TTL),
    }
    _save_store(store)


def load_session(capid: str) -> Optional[httpx.Client]:
    """
    Return an httpx.Client with restored session cookies, or None if
    no valid session exists for this CAPID.
    """
    store = _load_store()
    entry = store.get(str(capid))
    if not entry:
        return None
    if time.time() > entry["expires_at"]:
        invalidate_session(capid)
        return None

    from wmirs_auth.eservices import HEADERS

    client = httpx.Client(headers=HEADERS, follow_redirects=False, timeout=30.0)
    for name, value in entry["cookies"].items():
        client.cookies.set(name, value, domain="www.capnhq.gov")
    return client


def invalidate_session(capid: str) -> None:
    store = _load_store()
    store.pop(str(capid), None)
    _save_store(store)


def session_expires_at(capid: str) -> Optional[datetime]:
    store = _load_store()
    entry = store.get(str(capid))
    if not entry:
        return None
    return datetime.fromtimestamp(entry["expires_at"], tz=timezone.utc)
