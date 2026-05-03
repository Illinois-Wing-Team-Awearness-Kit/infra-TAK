# v0.8.1-alpha Release Notes

## Bug Fixes

### Hotfix: v0.8.0 post-update migration caused CPU spike and Postgres exhaustion on active installs

**Symptom:** After updating to v0.8.0, systems with active TAK clients experienced sustained high CPU (load average 100+), Postgres connection pool exhaustion (~200+ concurrent sessions against `max_connections=300`), and LDAP bind latency of 120–150 seconds per request. Some operators had to reboot and roll back to recover.

**Root cause:** The v0.8.0 post-update migration patched `AUTHENTIK_HOST` in `docker-compose.yml` and restarted the LDAP outpost on **all** existing installs that had the external HTTPS URL configured — including ones that were working fine. Restarting the LDAP outpost clears all cached bind sessions (`bind_mode: cached`). With active TAK clients, every client re-authenticates simultaneously after the restart. Each bind goes through the full Authentik flow executor, which issues heavy Django policy binding queries against Postgres. The resulting thundering herd exhausted the connection pool and saturated CPU.

**Fix:** The migration now checks whether the LDAP outpost is currently connected and healthy before touching anything. If the outpost has a live websocket connection to Authentik and no TLS errors in recent logs, the migration is skipped entirely. The URL is only patched and the container only restarted if the outpost is actually broken (not connected, or showing `remote error: tls: internal error`).

**Who is affected:**
- **v0.8.0 only.** v0.7.x and earlier are unaffected.
- Installs that already had a working LDAP outpost before updating to v0.8.0 and experienced CPU/Postgres issues.
- Installs that rolled back from v0.8.0 are safe to update to v0.8.1.

**Recovery for affected v0.8.0 installs — no command line needed for most operators:**

**Step 1:** infra-TAK console → Authentik page → **Restart** → wait 2 minutes.

**Step 2:** infra-TAK console → **Update Now**.

That's it for the majority of installs. The v0.8.1 migration will see the outpost is healthy after the restart and skip the disruptive operation entirely.

> If your normal domain is unreachable due to load, access the console via the backdoor: `https://<server-IP>:5001`

---

**Advanced: only if your box is still overloaded after the restart (large deployments with many simultaneous TAK clients)**

If you have many active clients and the box is still showing high CPU and LDAP errors several minutes after the Authentik restart, Authentik's default 2 server workers are being overwhelmed by all clients re-authenticating at once. Fix via SSH:

```bash
echo 'AUTHENTIK_WEB_WORKERS=4' >> ~/authentik/.env
cd ~/authentik && docker compose restart server
```

Wait 3–5 minutes for the load to drop, then run **Update Now** from the console.

| File | Change |
|------|--------|
| `app.py` | Post-update LDAP migration now checks outpost websocket connection and TLS error state before patching — skips if outpost is connected and healthy |
| `app.py` | VERSION bumped to 0.8.1-alpha |
