# v0.7.7-alpha Release Notes

---

## ⚠️ Action Required: Resync LDAP to TAK Server

If you haven't already done this, **do it now.**

Go to **TAK Server page → Resync LDAP to TAK Server**.

---

## Bug Fixes

### Authentik proxy provider external_host not updated on domain change (v0.7.6 fix incomplete)

**Symptom:** After changing a service domain in Caddy → Service Domains and running **Authentik → Update Config & Reconnect**, the console showed `✓ Proxy provider already exists: infra-TAK` but the External host field in Authentik Admin was never updated. The Authentik server logs confirmed no PATCH request was issued — identical to v0.7.5 behaviour.

**Root cause:** The v0.7.6 fix used a POST→catch-400→search→PATCH chain. The silent `except Exception` around the PATCH swallowed any error and fell through to the short log line with no PATCH ever reaching Authentik. Additionally the Authentik provider search API does not reliably return results for names containing hyphens in all versions.

**Fix:** Replaced the fragile POST→catch-400 dance with a GET-first upsert: list all existing proxy providers once at the start of the loop, PATCH if found (with clear log `✓ Proxy provider updated: infra-TAK → https://newdomain.com`), POST if not found. Any failure now logs the actual error instead of silently passing.

This same reliable pattern is now used for all infra-TAK console proxy providers (infra-TAK, MediaMTX). TAK Portal, Node-RED, and Federation Hub providers also updated to use `_get_service_domain()` so custom domain overrides are respected.

**After updating:** Run **Authentik → Update Config & Reconnect** — you will now see `✓ Proxy provider updated: infra-TAK → https://<new-domain>` in the console output confirming the PATCH executed.

| File | Change |
|------|--------|
| `app.py` | GET-first upsert for infra-TAK/MediaMTX proxy providers; TAK Portal, Node-RED providers use `_get_service_domain()` for custom domain support |
