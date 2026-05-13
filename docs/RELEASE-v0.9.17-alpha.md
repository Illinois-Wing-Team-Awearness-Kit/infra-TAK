# v0.9.17-alpha — Authentik Redis Self-Heal + Caddy Update Fix

**Date:** 2026-05-13
**Type:** Hotfix. Drop-in update — no operator pre-flight, no migrations to run manually.

---

## What the operator does

**Click Update Now.** That's it.

After the update:
- Any Authentik install missing a Redis container will have it added automatically. The Authentik worker CPU will drop from ~40–65% to near-idle within seconds of Redis starting.
- The Caddy Update button no longer risks a timeout loop when Caddy is mid-ACME-challenge.

---

## Fix 1 — Authentik: Redis self-heal for installs missing Redis

### What was wrong

Installs deployed before Redis was added to infra-TAK's Authentik compose template are missing the `redis:` service entirely. Without it, Authentik's dramatiq task broker falls back to trying `localhost:6379` on every task cycle — there's nothing listening there, so the worker retries the dead connection repeatedly, burning ~40–65% CPU sustained.

The existing `_ensure_authentik_compose_patches()` function — which runs on every Update Now — patched PostgreSQL tuning and healthchecks but had no logic to detect or inject a missing Redis service. Fresh installs always got Redis (it's in the deploy template), but existing installs never had it added after the fact.

This affected the responder install diagnosed in the v0.9.16 follow-up: Authentik 2026.2.3 running at 64.93% worker CPU with no `authentik-redis-1` container, `docker-compose.yml` modified today (by the v0.9.16 update), still no Redis.

### What changed

`_ensure_authentik_compose_patches()` now has three additional idempotent patch steps (run after the existing PG and healthcheck patches, before writing the file):

1. **Redis service injection** — detects absence of the Redis service (checked via `redis-cli ping` in the file, which is unique to the Redis healthcheck block). If missing, inserts the full Redis service block immediately before the `server:` service.

2. **`AUTHENTIK_REDIS__HOST: redis`** — if not present anywhere in the compose, inserts it into every environment block that contains `AUTHENTIK_POSTGRESQL__HOST:` (i.e., both `server` and `worker`).

3. **Top-level `redis:` volume** — if the Redis service is now present but `redis:` is not in the top-level `volumes:` section, inserts it.

All three steps are idempotent — on installs that already have Redis, all three checks pass immediately and the function returns without writing anything.

After the compose is patched, the existing `docker compose up -d` in `_auto_authentik()` starts the new Redis container and recreates the server/worker containers (since their environment changed). No operator action needed.

### Verify the fix worked

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E "redis|authentik-worker"
# Expected: authentik-redis-1 Up ... (healthy)

docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}' | grep authentik-worker
# Expected: authentik-worker-1   <2%
```

---

## Fix 2 — Caddy Update button: `reload` → `restart` (issue #25)

### What was wrong

The Caddy Update button (new in v0.9.16) ran `systemctl reload caddy` after the apt upgrade. On a Caddy instance mid-ACME-challenge (e.g. renewing `video.domain.com`), `reload` blocks waiting for the in-flight TLS operation to settle. If the ACME challenge never resolves, the reload never returns — systemd repeats it every ~90 seconds until timeout failures accumulate enough for systemd to SIGTERM and then SIGKILL the main Caddy process. Caddy's `Restart=` policy then brings it back up cleanly, but the repeated kill cycle causes ~1 hour of instability (issue #25, observed on tak.test6.takwerx.com).

Additionally, `reload` is semantically wrong after an apt binary upgrade: it only sends SIGHUP to re-read config in the existing process — it does not load the new binary. `restart` is required to get the upgraded Caddy version running.

### What changed

`caddy_update()` now runs `systemctl restart caddy` (timeout 90s) instead of `systemctl reload caddy || systemctl restart caddy`. No fallback chain needed — restart is unconditionally correct after a binary upgrade.

---

## Operator notes

- **Drop-in from v0.9.16.** Leave the update channel on `main` (green).
- **Installs that already have Redis** (all fresh installs since v0.8.x) — the Redis patches are no-ops. No compose change, no container recreation, no interruption.
- **Installs getting Redis for the first time** — `authentik-server-1` and `authentik-worker-1` will be recreated by `docker compose up -d` during Update Now (env var added). Expect ~30–60s of Authentik unavailability while the containers restart. LDAP auth via `authentik-ldap-1` is unaffected during the restart window (the LDAP outpost connects via network, not Docker socket).

---

## Slack-able summary

> infra-TAK v0.9.17: two hotfixes. (1) Authentik worker CPU — self-heal for installs missing Redis (deployed before Redis was part of the compose template). `_ensure_authentik_compose_patches()` now detects and injects the Redis service + env var on Update Now; idempotent no-op on installs that already have it. (2) Caddy Update button was issuing `systemctl reload` after the apt upgrade — wrong for a binary swap, and could hang indefinitely mid-ACME-challenge causing a systemd kill loop (issue #25). Changed to `systemctl restart`.
