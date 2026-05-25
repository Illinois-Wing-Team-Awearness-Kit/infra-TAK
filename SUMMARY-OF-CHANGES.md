# Remote Authentik Deploy — Bug Fix Summary

**Branch:** `claude/admiring-planck-zXiPq`  
**Date:** 2026-05-25  
**Symptom:** Deploying Authentik to a remote host (separate VM from the InfraTAK console) failed every time, leaving the service unreachable.

---

## Five Root Causes Fixed

### 1. PostgreSQL crashed on small servers (commits `d0f5465`)

**What broke:** v0.9.28 hardcoded PostgreSQL memory settings sized for 48 GB enterprise hardware (`shared_buffers=12GB`, `max_connections=2000`). Any remote host with less than 48 GB of RAM had PostgreSQL crash immediately on startup, failing the entire deploy.

**Fix:** Added a RAM probe that SSHes to the remote host before writing the compose file and selects PostgreSQL settings appropriate to the actual hardware:

| Host RAM | shared_buffers | max_connections |
|----------|---------------|-----------------|
| < 4 GB / unknown | 256 MB | 200 |
| 4–7 GB | 1 GB | 300 |
| 8–15 GB | 2 GB | 500 |
| 16–47 GB | 4 GB | 1,000 |
| ≥ 48 GB | 12 GB (unchanged) | 2,000 |

The 48 GB enterprise tier is fully preserved for hardware that can support it.

---

### 2. Stale database volume caused password mismatch on retry (`a04fcf0`)

**What broke:** Each deploy generates a fresh random PostgreSQL password. If a previous failed deploy left a database volume on the remote host, PostgreSQL would start with the old password baked in and reject every login attempt from the application, producing a flood of `FATAL: password authentication failed` errors.

**Fix:** The deploy script now runs `docker compose down -v` (remove containers and volumes) before starting, so the database always initialises fresh against the current password. This happens automatically — no manual SSH cleanup required between retries.

---

### 3. Better error output when Docker Compose fails (`732b091`)

**What broke:** When `docker compose up` failed, the deploy log showed only `✗ Docker Compose failed` with no detail — making it impossible to diagnose the actual cause without SSHing to the remote host manually.

**Fix:** On failure, the deploy log now shows:
- The last 20 lines of `docker compose up` output
- The status of all Authentik containers (`docker ps -a`)
- The last 20 lines of the PostgreSQL container logs

The `docker compose up` timeout was also raised from 5 minutes to 10 minutes to accommodate the Authentik server's 600-second health-check start period.

---

### 4. LDAP token injection timed out waiting for the API (`f6ae3d9`)

**What broke:** After the containers started, the deploy script tried to call the Authentik REST API (`http://remote-ip:9090/api/...`) directly from the console server. The API port was bound to `127.0.0.1` on the remote host (loopback-only), so the console could never reach it. This caused Step 6b to sit waiting until it timed out without ever injecting the LDAP token.

**Fix:** All API calls in Step 6b now run as `curl` commands via SSH on the Authentik host itself, where `localhost:9090` is reachable. No network topology changes required for this step.

---

### 5. Caddy could not reach Authentik for login / SSO (`cdbedad`)

**What broke:** Caddy (the reverse proxy running on the InfraTAK console server) needs to reach the Authentik API at `remote-ip:9090` to perform `forward_auth` — the mechanism that protects the console behind the Authentik login page. The v0.9.12 security tightening bound port 9090 to `127.0.0.1` on the Authentik host under the assumption that Caddy was co-located there. It isn't — Caddy runs on the console server — so every request to `tak.prod.ilwg.us` failed with a connection error.

**Fix:**
- The remote compose file now binds port 9090 on `0.0.0.0` so the console server can reach it.
- A UFW firewall rule is added on the Authentik host that allows port 9090 **only from the console server's IP** and denies it from all other sources. This maintains the same security posture as the LDAP ports (389/636), which already used this source-scoping pattern.

---

## Net Result

A fresh remote Authentik deploy now completes end-to-end without manual intervention:

1. Detects remote RAM → tunes PostgreSQL accordingly
2. Clears any leftover state from previous attempts
3. Starts all containers and waits for health
4. Injects the LDAP outpost token via SSH
5. Configures the firewall (9090/389/636 locked to console IP)
6. Regenerates the Caddyfile and reloads Caddy
7. Marks the deployment as complete in settings

All changes are generic — no values are hardcoded for any specific server or environment.
