# v0.7.8-alpha Release Notes

---

## ⚠️ Action Required: Resync LDAP to TAK Server

If you haven't already done this, **do it now.**

Go to **TAK Server page → Resync LDAP to TAK Server**.

---

## Bug Fixes

### Authentik proxy provider update returns 400 on domain change (v0.7.7 fix incomplete)

**Symptom:** After updating to v0.7.7 and running **Authentik → Update Config & Reconnect**, the console showed `⚠ Proxy provider PATCH failed: infra-TAK: HTTP Error 400: Bad Request`. The External host field in Authentik was still not updated.

**Root cause:** The v0.7.7 fix sent only `external_host` and `cookie_domain` in the PATCH payload. Authentik's proxy provider API requires several other fields to be present for validation — a partial payload with just two fields fails with 400.

**Fix:** Now performs a GET on the existing provider first to retrieve the full current object, updates only `external_host` and `cookie_domain` in that object, then sends a PUT with the complete payload. This satisfies Authentik's validation. Error responses now include the actual error body so failures are no longer opaque.

**After updating:** Run **Authentik → Update Config & Reconnect** — you will see `✓ Proxy provider updated: infra-TAK → https://<new-domain>` confirming the update succeeded.

| File | Change |
|------|--------|
| `app.py` | GET current provider → modify fields → PUT full object instead of partial PATCH |
