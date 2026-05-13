# v0.9.16-alpha — Authentik Worker CPU Hotfix + Caddy Update Button

**Date:** 2026-05-13
**Type:** Hotfix + minor UI feature. Drop-in update — no operator pre-flight, no migrations to run manually.

---

## What the operator does

**Click Update Now in the Console.** That's it.

After the update, the Authentik worker CPU will drop from ~26% sustained to near-idle. No restarts required — the cleanup runs against the live Authentik API during the post-update migration ladder.

---

## What this fixes

After upgrading Authentik to 2026.2.3 (or any recent version), operators saw the `authentik-worker-1` container sitting at ~25–30% CPU continuously. The worker logs showed the same error firing every 30 seconds:

```
DockerException: Error while fetching server API version:
  ('Connection aborted.', FileNotFoundError(2, 'No such file or directory'))
outpost_service_connection_monitor(UUID('...'))
```

**Root cause — two-step chain:**

1. **v0.9.2-alpha** (CVE-2026-31431 hardening) deliberately removed `/var/run/docker.sock` from the Authentik worker container's compose volumes. infra-TAK uses a standalone `authentik-ldap-1` container managed by docker compose — not Docker-managed outposts — so the socket was never needed and was a security liability (full Docker daemon access from a compromised worker = host root).

2. That fix patched the compose file but never cleaned up the **Docker service connection** stored in Authentik's database. Authentik's upstream quickstart creates a "Local Docker" service connection by default. With the socket gone, the worker's `outpost_service_connection_monitor` task retries it every 30 seconds, fails, and dramatiq retries it again — burning CPU in a tight loop indefinitely.

This was present on every operator install since v0.9.2 but became clearly visible after the Authentik 2026.2.3 upgrade improved logging.

---

## What changed under the hood

New post-update migration step: `_auto_remove_stale_docker_service_connections()`.

Runs in `_run_post_update()` after `_authentik_tasklog_cleanup()`. Uses the Authentik API to:

1. `GET /api/v3/core/service_connections/docker/` — list all Docker service connections.
2. For each connection where `local: true` (meaning "connect to `/var/run/docker.sock` on the worker host"), issue `DELETE /api/v3/core/service_connections/docker/{pk}/`.
3. Log what was deleted, or "nothing to do" if no local connections were found.

**Idempotent** — if no local Docker service connections exist, it logs one line and exits. Safe to run on every Update Now; subsequent runs are no-ops.

**Non-fatal** — the entire function is wrapped in a top-level exception handler. If Authentik is unreachable at migration time (e.g. still starting up), it skips with a logged warning and does not block the rest of the update.

---

## Operator notes

- **Drop-in from v0.9.15.** Leave the update channel on `main` (green).
- **You will see this in the Update Now log:**
  ```
  Post-update: Authentik — deleted stale local Docker service connection 'Local Docker' (<uuid>)
  ```
  or, on installs where the connection was already cleaned up manually:
  ```
  Post-update: Authentik Docker SC cleanup — no local connections found, nothing to do
  ```
- **Verify the fix worked** — after Update Now, run:
  ```bash
  docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}' | grep authentik-worker
  ```
  Expected: `authentik-worker-1` at `<2%` (idle). The 30-second `DockerException` loop in the worker logs will also stop.
- **No impact on LDAP authentication.** The `authentik-ldap-1` outpost container connects back to Authentik over the network (`AUTHENTIK_HOST`) — it does not use the Docker socket and is completely unaffected.
- **No impact on any other Authentik functionality.** Docker service connections are only used to let Authentik manage outpost containers via the Docker daemon directly. infra-TAK does not use this feature.

---

---

## Caddy — Update button

The Caddy detail page now has an **Update** button in the controls bar, consistent with every other service page (TAK Portal, CloudTAK, Federation Hub, Guard Dog, MediaMTX).

### What it does

- **Version displayed in status banner** — the current installed Caddy version (e.g. `v2.9.1`) is shown inline in the "Running" / "Stopped" status line, alongside the domain and cert expiry.
- **Update available indicator** — when apt detects a newer Caddy package (`apt list --upgradable`), the status banner shows `· update available` in cyan and the Update button glows with a cyan border + dot — matching the console card badge that was already surfacing this.
- **Update button** — runs `apt-get update -qq && apt-get install --only-upgrade -y caddy` on the server, then issues `systemctl reload caddy` (falls back to restart if reload fails). Confirms before running. Shows a spinning indicator + status text while the upgrade is in progress; reloads the page on success.
- Works on both `apt` (Ubuntu/Debian) and `dnf` (Fedora/RHEL) installs — reads `pkg_mgr` from settings.

### Why it was missing

Caddy's update-available detection (`_get_caddy_version_info`) was already wired into the console card badge but the result was never passed into the Caddy detail page's template render call, and no backend update route existed. All other service pages had this — Caddy was the only one that didn't.

---

## Slack-able summary

> infra-TAK v0.9.16: two changes. (1) Hotfix: Authentik worker was burning ~26% CPU on every install since v0.9.2 due to a stale "Local Docker" service connection in Authentik's database. The v0.9.2 CVE hardening removed the Docker socket mount but didn't clean up the DB record — so the worker retried the dead socket every 30 seconds forever. Update Now auto-deletes it via the Authentik API. Drop-in, no operator action. (2) Caddy detail page now shows the installed version + update-available indicator in the status banner, and has an Update button in controls that upgrades via apt and reloads Caddy automatically.
