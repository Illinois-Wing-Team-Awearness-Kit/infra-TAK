# v0.8.2-alpha Release Notes

## Bug Fixes

### Fix: automatically set `AUTHENTIK_WEB_WORKERS=4` on update to prevent bind storms

**Problem:** On installs with many active TAK clients, Authentik's default of 2 server workers was insufficient to handle all clients re-authenticating simultaneously after a restart (thundering herd). This caused flow executor runtimes of 100–200 seconds, LDAP bind failures, and ongoing instability even after recovery attempts.

**Fix:** The post-update migration now automatically sets `AUTHENTIK_WEB_WORKERS=4` in `~/authentik/.env` if it is not already set to 4 or higher, then restarts only the Authentik server container. The LDAP outpost is **not** restarted — bind caches are preserved. The server restart takes a few seconds and operators will not notice any disruption.

**Who is affected:**
- All installs on v0.8.0 or v0.8.1 that have not manually set `AUTHENTIK_WEB_WORKERS`.
- The migration runs automatically on **Update Now** — no action required.

**Recovery for operators still stuck after updating to v0.8.1:**

Just run **Update Now** — the v0.8.2 migration will apply the workers fix automatically without requiring any command line access.

> If your console is unreachable, use the backdoor at `https://<server-IP>:5001`

| File | Change |
|------|--------|
| `app.py` | Post-update migration sets `AUTHENTIK_WEB_WORKERS=4` in `.env` and restarts server if workers < 4 |
| `app.py` | VERSION bumped to 0.8.2-alpha |
