# v0.7.9-alpha Release Notes

---

## ⚠️ Action Required: Resync LDAP to TAK Server

If you haven't already done this, **do it now.**

Go to **TAK Server page → Resync LDAP to TAK Server**.

---

## Bug Fixes

### 400 errors immediately after Authentik → Update Config & Reconnect

**Symptom:** After running **Update Config & Reconnect** and seeing `✓ Reconfigure complete`, visiting any Authentik-protected service returned HTTP 400 errors on both the old and new domain.

**Root cause:** The reconfigure restarts the Authentik server and worker at the end of the process. `docker compose restart` returns as soon as Docker has issued the restart command — not when Authentik is actually ready to serve traffic. The reconfigure marked itself complete within ~4 seconds of the restart, while Authentik typically takes 1–2 minutes to boot. Any visit to a protected service during that window hit Authentik mid-boot and got a 400.

**Fix:** After restarting Authentik, the reconfigure now waits for the Authentik API to confirm it is online and healthy before logging `✓ Reconfigure complete`. The console will show `Waiting for Authentik to come back online...` during this window. Once Authentik confirms ready, `✓ Authentik is online and ready` is logged and the reconfigure finishes. Services will work immediately.

| File | Change |
|------|--------|
| `app.py` | Added `_wait_for_authentik_api` call after `docker compose restart server worker` in reconfigure flow |
