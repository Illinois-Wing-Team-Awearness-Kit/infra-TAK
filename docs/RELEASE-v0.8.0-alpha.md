# v0.8.0-alpha Release Notes

---

## Bug Fixes

### 400 errors immediately after Authentik → Update Config & Reconnect

**Symptom:** After running **Update Config & Reconnect** and seeing `✓ Reconfigure complete`, visiting any Authentik-protected service returned HTTP 400 errors on both the old and new domain.

**Root cause:** The reconfigure restarts the Authentik server and worker at the end of the process. `docker compose restart` returns as soon as Docker has issued the restart command — not when Authentik is actually ready to serve traffic. The reconfigure marked itself complete within ~4 seconds of the restart, while Authentik typically takes 1–2 minutes to boot. Any visit to a protected service during that window hit Authentik mid-boot and got a 400.

**Fix:** After restarting Authentik, the reconfigure now waits for the Authentik API to confirm it is online and healthy before logging `✓ Reconfigure complete`. The console will show `Waiting for Authentik to come back online...` during this window. Once Authentik confirms ready, `✓ Authentik is online and ready` is logged and the reconfigure finishes. Services will work immediately.

| File | Change |
|------|--------|
| `app.py` | Added `_wait_for_authentik_api` call after `docker compose restart server worker` in reconfigure flow |

---

### LDAP outpost `tls: internal error` on fresh installs with an FQDN configured

**Symptom:** On a fresh single-box install where an FQDN is set, the `ldap-1` container immediately starts logging `remote error: tls: internal error` when trying to reach `https://<fqdn>/api/v3/outposts/instances/`. Rebooting and reloading Caddy does not fix it. The LDAP outpost goes into exponential backoff (3 s → 6 s → 12 s → … → 12+ minutes between retries) and never recovers on its own.

**Root cause:** Three code paths in `app.py` configured the LDAP outpost to reach Authentik via the external HTTPS domain (`https://<fqdn>`) rather than the internal Docker service name (`http://authentik-server-1:9000`). The LDAP container and Authentik are always on the same Docker Compose stack (same box), so routing through Caddy is unnecessary. On a fresh install, Caddy may still be completing its ACME certificate challenge when the LDAP container first starts. Caddy responds to the TLS ClientHello with an `internal_error` alert (no cert yet), the LDAP outpost goes into exponential backoff, and by the time Caddy has the cert the LDAP container is waiting 10+ minutes between retries. Additionally, running **Update Config** after initial setup would re-overwrite a manually corrected `AUTHENTIK_HOST` back to the external URL.

**Fix:** All three code paths now unconditionally use `http://authentik-server-1:9000` for the LDAP outpost. No `extra_hosts` entry is needed or generated. The `AUTHENTIK_HOST` in `.env` (used by the Authentik server itself for redirect URLs) is unchanged.

**Operator action for existing installs already broken by this:** On the server, run:
```bash
cd ~/authentik
sed -i 's|AUTHENTIK_HOST:.*|AUTHENTIK_HOST: http://authentik-server-1:9000|' docker-compose.yml
docker compose up -d ldap
docker compose logs ldap -f --tail=20
```

| File | Change |
|------|--------|
| `app.py` | Removed `if _ak_host:` branch in local LDAP service generation — always use internal Docker URL |
| `app.py` | Removed HTTPS URL substitution + `extra_hosts` injection in remote deploy path |
| `app.py` | Removed compose file `AUTHENTIK_HOST` overwrite in Update Config / reconfigure domain-sync path |
