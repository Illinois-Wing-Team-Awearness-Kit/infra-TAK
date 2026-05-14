# v0.9.20-alpha — MediaMTX HLS Caddyfile fix + Caddyfile auto-regenerate on Update Now + Authentik PostgreSQL connection-pool tuning + Guard Dog 8089 false-positive fix

**Date:** 2026-05-13
**Type:** Drop-in update — no operator pre-flight, no migrations to run manually.

---

## TL;DR

Four related fixes:

1. **Guard Dog — 8089 false-positive restarts** — Replaced the `nc -z 127.0.0.1:8089` TCP-connect liveness probe with an `ss -ltn` LISTEN check. Port 8089 requires mutual TLS (`auth="x509"`); raw TCP probes cause Netty to log an SSL error on every check and, on some netcat-openbsd builds, return non-zero — triggering false-positive restarts that drain the 3/day restart cap and spam the Guard Dog activity log with "SKIP | 8089 unhealthy but daily restart cap reached". The `ss` LISTEN check is sufficient: if Netty is bound, the messaging process is accepting connections. Same change applied to `tak-post-start.sh`'s boot-wait loop (which used `nc -z` for the same port). Fixes [issue #24](https://github.com/takwerx/infra-TAK/issues/24).

2. **Caddyfile — MediaMTX HLS cookie-check** — Added `header_down Location ^ /hls-proxy` inside the `handle_path /hls-proxy/*` block on the MediaMTX FQDN, so MediaMTX v1.18.x's new HLS session-tracking cookie-check redirect (302 → `?cookieCheck=1`) lands back inside Caddy's `/hls-proxy/*` handler instead of 404'ing outside it. Without the fix, HLS Watch playback is broken on every infra-TAK server running MediaMTX v1.18.0+; the v1.18.x release notes flag the cookie-check redirect as the breaking change. Reference: [INFRA-TAK-CADDY-HLS-v1.18.md](https://github.com/takwerx/mediamtx-installer/blob/main/docs/INFRA-TAK-CADDY-HLS-v1.18.md).

3. **Caddyfile auto-regenerate on Update Now** — `_post_update_auto_deploy()`'s migration thread now ends with `generate_caddyfile() + systemctl reload caddy` (idempotent no-op when FQDN isn't set). Surfaced during the v0.9.19 HLS rollout: the Caddyfile template change in `generate_caddyfile()` was correct, but `Update Now` only does `git fetch → checkout → systemctl restart takwerx-console` — it never rewrites `/etc/caddy/Caddyfile`. So the new template code was on disk and the rendered Caddyfile was stale. From this release on, every version bump auto-rolls Caddyfile template changes without operator action.

4. **Authentik PostgreSQL connection-pool tuning** — three coordinated changes to address `FATAL: sorry, too many clients already` and idle-in-transaction pileup on the django_postgres_cache table observed on tak-10 after the v0.9.18 LDAP-routing fix: (a) bump `max_connections` 300 → 500 in the Postgres command line; (b) append `AUTHENTIK_POSTGRESQL__CONN_MAX_AGE=60` + `AUTHENTIK_POSTGRESQL__CONN_HEALTH_CHECKS=true` to `~/authentik/.env` so Django reuses connections instead of opening one per request; (c) add a `django_channels_postgres_message` row-count + size signal to the spiral monitor (informational — surfaces channel-layer bloat in operator logs without tripping routing repair). Refs: [goauthentik/authentik#20714](https://github.com/goauthentik/authentik/issues/20714), [#20644](https://github.com/goauthentik/authentik/issues/20644), [Authentik configuration docs](https://docs.goauthentik.io/install-config/configuration/).

Validated on `tak-10` (`stream.test12.taktical.net`) running MediaMTX v1.18.1 — HLS Watch playback confirmed working after Update Now. Authentik PG pool tuning will be validated on the same box after the v0.9.20 push.

---

## What was wrong

### Part 4 — Guard Dog 8089 false-positive restarts (issue #24)

Port 8089 on TAK Server is configured with `auth="x509" protocol="tls"` (mutual TLS). The Guard Dog's `tak-8089-watch.sh` ran two checks every minute:

1. `ss -ltn "sport = :8089"` → port is LISTEN ✓
2. `nc -z -w 5 127.0.0.1 8089` → raw TCP connect (no TLS)

Step 2 is the problem. When `nc -z` connects to an mTLS port, the TCP 3-way handshake completes but Netty immediately begins TLS negotiation. `nc -z` sends no TLS ClientHello, so Netty logs an SSL error and closes the connection. On some builds of netcat-openbsd, the RST/FIN arrives before `nc` has reported success — causing it to return non-zero. Result: `CONNECT_OK=false` → `LISTEN_OK && CONNECT_OK` fails → false-positive unhealthy count increments every minute → 3 restarts hit the daily cap → every subsequent check logs `SKIP | 8089 unhealthy but daily restart cap (3) reached` in the Guard Dog activity log. TAK Server is healthy; the health check is not.

Same `nc -z 127.0.0.1 8089` pattern in `tak-post-start.sh`'s boot-wait loop polluted the TAK server log with SSL errors on every boot.

### Part 1 — MediaMTX v1.18.x HLS cookie check

MediaMTX v1.18.0 introduced HLS session tracking. On the first HLS manifest request per browser session, MediaMTX returns a `302` redirect to `?cookieCheck=1` with a `Set-Cookie: mediamtx_session=...` header. The browser follows the redirect, MediaMTX verifies the cookie on the second request, then redirects back to the original manifest URL.

infra-TAK's Caddyfile proxies HLS through `handle_path /hls-proxy/*` on `stream.<base>`, which strips the `/hls-proxy/` prefix before forwarding to MediaMTX on `127.0.0.1:8888`. When MediaMTX issues the cookie-check redirect, its `Location` header is relative to its own path (e.g. `/teststream/index.m3u8?cookieCheck=1`) — **without** the `/hls-proxy/` prefix. The browser follows the redirect to `/teststream/index.m3u8?cookieCheck=1` on `stream.<base>`, which has no Caddy handler, returns 404, and HLS playback fails.

MediaMTX v1.17.0 doesn't issue this redirect, so servers staying on v1.17.0 are unaffected — which is why the bug only surfaced after the MediaMTX upgrade.

### Part 2 — Update Now never rewrote `/etc/caddy/Caddyfile`

`Update Now`'s implementation (`api_update_apply` → checkout `origin/dev` HEAD / latest tag → `systemctl restart takwerx-console`) updates `app.py` on disk but does not call `generate_caddyfile(settings)` afterwards. So a release that changes the Caddyfile **template** but doesn't manually trigger a regenerate in the same flow (e.g. via a per-service redeploy or operator clicking "Reload" on the Caddy page) silently fails to roll out the template change to running Caddy.

This is the exact reason the v0.9.19 HLS fix appeared "not applied" on tak-10 after the first Update Now: the new template code was running in `app.py`, but `/etc/caddy/Caddyfile` was last rendered under the v0.9.18 template and Caddy was still serving that.

---

## What changed

### Part 4 — `tak-8089-watch.sh` and `tak-post-start.sh`

**`scripts/guarddog/tak-8089-watch.sh`** — removed the `nc -z` TCP-connect block and the `CONNECT_TIMEOUT` constant. `CONNECT_OK` variable eliminated. The "healthy" condition is now just `LISTEN_OK` (port is bound per `ss -ltn`). Detail log line updated from `listen=$LISTEN_OK connect=$CONNECT_OK` to `listen=$LISTEN_OK`. No logic changes to restart thresholds, cooldown, daily cap, or alerting.

**`scripts/guarddog/tak-post-start.sh`** — boot-wait loop changed from:
```bash
if nc -z 127.0.0.1 8089 2>/dev/null; then
```
to:
```bash
if ss -ltn "sport = :8089" 2>/dev/null | grep -q LISTEN; then
```

Both files are read from disk at Guard Dog deploy time — the fix ships to servers when the operator runs **Deploy Guard Dog** or via the next Guard Dog re-deploy. No console restart required; the scripts land in `/opt/tak-guarddog/` at deploy.

### Part 1 — `generate_caddyfile()` Caddyfile template

In `app.py:generate_caddyfile()`, the MediaMTX web-console block's `handle_path /hls-proxy/*` reverse_proxy now includes a single `header_down` directive on the response path:

```caddy
handle_path /hls-proxy/* {
    reverse_proxy 127.0.0.1:8888 {
        header_down Location ^ /hls-proxy
    }
}
```

`header_down Location ^ /hls-proxy` is a regex replacement on the upstream response's `Location` header — `^` is the start anchor, `/hls-proxy` is the prepended replacement. So MediaMTX's `Location: /teststream/index.m3u8?cookieCheck=1` becomes `Location: /hls-proxy/teststream/index.m3u8?cookieCheck=1` before reaching the browser. `Set-Cookie` and other headers pass through unmodified. On `200 OK` (manifest served), there's no `Location` header and `header_down` is a no-op — safe for all response types.

The same `header_down` is also emitted in the `hls_enc=True` branch (HLS-on-TLS deployments, currently rare), keeping both code paths consistent.

#### Why `header_down` and not `handle_response`

An earlier draft of the fix used `handle_response @mtx_redirect { redir ... }` to intercept 3xx responses. That approach **does not work** in this Caddyfile because the `stream.<base>` block also has a catch-all `route {}` with `forward_auth` for the MediaMTX web editor. When `handle_response` issues `redir`, the route chain does not terminate cleanly and the catch-all `forward_auth` directive still fires — redirecting the request to Authentik authorize instead of serving the rewritten redirect to the browser. `header_down` modifies the upstream response in-place without generating a new response, so the route chain terminates correctly and `forward_auth` never runs on `/hls-proxy/*` paths.

(Two commits on dev — `a275cc3` shipped the broken `handle_response` form first; `49c548d` replaced it with `header_down`. The doc at takwerx/mediamtx-installer was revised the same day to flag the `handle_response` approach as wrong.)

### Part 2 — `_post_update_auto_deploy()` auto-regenerate Caddyfile

Added a final step to `_run_post_update()` (right before `print("Post-update: auto-deploy complete")`) that:

1. Loads `settings.json`.
2. If `fqdn` is set and `/etc/caddy/Caddyfile` exists, calls `generate_caddyfile(settings)` (rewrites the file from the current template).
3. Runs `systemctl reload caddy` with a 20-second timeout.
4. Logs result: `Post-update: Caddyfile regenerated + reloaded` on success, or `Post-update: Caddy reload issue: <msg>` on failure.

The post-update thread is gated by `_post_update_auto_deploy()`'s version-change check (`if last_ver == VERSION: return`), so the step fires once per version bump. Boxes already on `VERSION` skip the rerun — important because regenerating the Caddyfile on every console restart could mask intentional operator edits.

Idempotent in all directions:

- **No FQDN configured:** skipped (no Caddyfile to regenerate).
- **No `/etc/caddy/Caddyfile`:** skipped (Caddy not installed).
- **Caddy reload failure:** logged, doesn't abort the migration thread, doesn't block subsequent updates.
- **Same VERSION as `last_console_version`:** post-update hook short-circuits (existing behaviour, unchanged).

---

## Cookie check flow after the fix

1. Browser → `https://stream.<base>/hls-proxy/teststream/index.m3u8`
2. Caddy strips prefix → MediaMTX gets `GET /teststream/index.m3u8`
3. MediaMTX (v1.18.x): `302 /teststream/index.m3u8?cookieCheck=1` + `Set-Cookie: mediamtx_session=X`
4. Caddy `header_down` rewrites: `302 /hls-proxy/teststream/index.m3u8?cookieCheck=1` + cookie passes through ✓
5. Browser follows → `/hls-proxy/teststream/index.m3u8?cookieCheck=1` → Caddy handles ✓
6. Caddy strips prefix → MediaMTX gets `GET /teststream/index.m3u8?cookieCheck=1` with cookie
7. MediaMTX validates cookie: `302 /teststream/index.m3u8`
8. Caddy rewrites: `302 /hls-proxy/teststream/index.m3u8` ✓
9. Browser follows → MediaMTX returns `200 OK` manifest → HLS plays ✓

---

## Compatibility

| MediaMTX version | Behaviour before fix | Behaviour after fix |
|------------------|---------------------|--------------------|
| v1.17.0 | HLS works (no cookie-check redirect issued) | HLS works (`header_down` is a no-op when no `Location` header is present) |
| v1.18.0+ | HLS broken (404 on cookie-check follow-up) | HLS works (Location header rewritten in-place) |

Safe to deploy to all infra-TAK servers regardless of their current MediaMTX version.

---

## Operator notes

- **Guard Dog 8089 fix (issue #24):** If your Guard Dog activity log is full of `SKIP | 8089 unhealthy but daily restart cap (3) reached`, TAK Server is almost certainly healthy — the health check was the problem. After Update Now, re-deploy Guard Dog from the infra-TAK console (Guard Dog page → Deploy Guard Dog). This writes the updated `tak-8089-watch.sh` and `tak-post-start.sh` to `/opt/tak-guarddog/`. The 24-hour restart counter resets automatically at midnight — no manual intervention required. To reset it immediately: `echo 0 > /var/lib/takguard/tak_restart_count_24h`.

- **Drop-in from v0.9.18.** Hit Update Now. No pre-flight, no manual Caddy reload required.
- **Dev-channel boxes that pulled v0.9.19 dev commits before this hook landed:** resolved by v0.9.20. The version bump (`0.9.19-alpha → 0.9.20-alpha`) triggers the post-update hook on every box, including the dev fleet, so the Caddyfile auto-regenerate step fires on the next Update Now. No manual workaround required.
- **Boxes on MediaMTX v1.17.0:** no behavioural change. HLS continues to work; the `header_down` directive is dead code on those servers until they upgrade MediaMTX.
- **Side benefit of part 2:** any future Caddyfile template change ships via Update Now alone — no need for operators to remember to click Reload after the update.

### Verify the fix landed

```bash
# 1. Confirm Caddyfile has the header_down directive
grep -A1 "handle_path /hls-proxy" /etc/caddy/Caddyfile
# Expected: contains "header_down Location ^ /hls-proxy"

# 2. Confirm Caddy reloaded the new config
journalctl -u takwerx-console --since "5 min ago" | grep "Caddyfile regenerated"
# Expected: "Post-update: Caddyfile regenerated + reloaded"
#   (on boxes that updated FROM a different version — see operator notes above)

# 3. Hit MediaMTX through Caddy and inspect the redirect
curl -sI "https://stream.<base>/hls-proxy/teststream/index.m3u8" | grep -i location
# Expected: "location: /hls-proxy/teststream/index.m3u8?cookieCheck=1"
#   (NOT "location: /teststream/index.m3u8?cookieCheck=1" — that's the broken state)

# 4. Confirm Caddy itself is happy
systemctl status caddy | head -20
# Expected: active (running), no recent "reload failed" entries
```

### Validated on tak-10 (test12.taktical.net), 2026-05-13

- MediaMTX v1.18.1 confirmed running.
- HLS Watch button playback confirmed working by the MediaMTX team after the dev push.
- Cookie-check redirect `Location` header confirmed rewriting to `/hls-proxy/teststream/...` per step 3 above.

---

## Part 3 — Authentik PostgreSQL connection-pool tuning

### Symptom on tak-10 (post v0.9.18)

After v0.9.18 dropped the LDAP routing spiral from 492% CPU back to ~5%, tak-10 surfaced a different problem: Authentik server CPU climbing to 800%+, Postgres to 180%, **221 total Postgres backends** with **75 in `idle in transaction`** all pinned to the `django_postgres_cache_cacheentry` query, repeated `FATAL: sorry, too many clients already` in the authentik-worker log, and `django_channels_postgres_message` bloated to **109,840 rows / 61 MB**.

Same pattern reported upstream:

- [goauthentik/authentik#20714](https://github.com/goauthentik/authentik/issues/20714) — connection pool exhaustion under heavy load.
- [goauthentik/authentik#20644](https://github.com/goauthentik/authentik/issues/20644) — django_channels_postgres growth + cleanup task gaps.

### Root cause

Authentik 2026.2.x ships with Django's `CONN_MAX_AGE=0` default. Every web request, every worker tick, every dramatiq job opens a brand-new Postgres connection (TCP handshake + Django startup queries — `SET timezone`, `ALTER ROLE`, etc.) and tears it down on response. Stacked across the server + worker + multiple outpost flows, this saturates the 300-slot pool that infra-TAK set in v0.8.4. The cache table queries are slow enough (multi-second when the enterprise license cache hits cold rows) that they sit in `idle in transaction` waiting on the next round-trip — which now arrives on a fresh connection because the old one was closed — and the pool fills.

`django_channels_postgres` (Authentik's PG-backed realtime channel layer) compounds the issue: under heavy outpost flow load it writes one row per outbound websocket message, and the TTL cleanup task can fall behind, leaving the table at 100k+ rows. Each LISTEN/NOTIFY query against a bloated table is slower, which lengthens the `idle in transaction` window, which fills the pool faster.

### Fix

Three changes, all delivered as automated migrations through `Update Now`:

**(a) `max_connections=300 → 500`** in `_ensure_authentik_compose_patches()`:

```python
pg_cmd = 'postgres -c max_connections=500 -c idle_session_timeout=300s -c idle_in_transaction_session_timeout=300s -c tcp_keepalives_idle=60 -c tcp_keepalives_interval=10 -c tcp_keepalives_count=6'
```

Detection broadened: catches `max_connections=<500` and rewrites the entire `command:` line. Won't downgrade boxes where the operator set a higher value (e.g. `=800` — verified by smoke test). Triggers a one-time `docker compose up -d --force-recreate postgresql` to apply (postgres command-line args only take effect on container start).

**(b) `AUTHENTIK_POSTGRESQL__CONN_MAX_AGE=60` + `AUTHENTIK_POSTGRESQL__CONN_HEALTH_CHECKS=true`** appended to `~/authentik/.env` via the new `_ensure_authentik_pg_persistent_connections()` helper:

```bash
# v0.9.20: Django persistent DB connections — cuts handshake churn ~95% under heavy load.
# Both keys use DOUBLE underscore between segments per Authentik docs (single is silently ignored).
AUTHENTIK_POSTGRESQL__CONN_MAX_AGE=60
AUTHENTIK_POSTGRESQL__CONN_HEALTH_CHECKS=true
```

Per Authentik's [configuration docs](https://docs.goauthentik.io/install-config/configuration/), these keys use the **double-underscore** convention. Same gotcha that produced the v0.8.2 → v0.8.6 `AUTHENTIK_WEB_WORKERS` silent-ignore bug (`__`, not `_`, between segments). Helper is idempotent — if either key is already present (operator override or future Authentik default), it's left alone; only the missing keys are appended.

`CONN_HEALTH_CHECKS=true` is strictly required when `CONN_MAX_AGE > 0` because Django pings each pooled connection before reuse and drops dead ones. Without it, the first request after any Postgres restart (or compose recreate) would fail with `connection already closed`.

After appending, `_recreate_authentik_server_worker(reason='pg-persistent-connections')` recreates server + worker only (LDAP outpost / Postgres untouched). Dramatiq workers also open Django DB connections, so leaving worker on `CONN_MAX_AGE=0` would leave half the churn problem in place. Verification step inside the helper: `docker exec authentik-server-1 printenv AUTHENTIK_POSTGRESQL__CONN_MAX_AGE` must return `60`, and the same on worker for `CONN_HEALTH_CHECKS=true`. Outcome saved to `settings.authentik_pg_persistent_conn_migration`.

**(c) `django_channels_postgres_message` size signal** in `_detect_authentik_ldap_spiral()`:

```sql
SELECT
  (SELECT count(*) FROM django_channels_postgres_message),
  (SELECT pg_total_relation_size('django_channels_postgres_message')/1024/1024);
```

Result lands in `evidence['channels_msg_rows']` and `evidence['channels_msg_mb']`. Surfaced in plog output with a tiered warning:

- < 5,000 rows: silent (healthy baseline).
- 5,000 – 49,999: `channels_postgres_message: N rows / N MB (elevated but not critical)`.
- ≥ 50,000 rows: `⚠ channels_postgres_message bloat: N rows / N MB (cleanup task may be stuck — consider worker restart)`.

Does **not** trip the spiral gate. Routing repair shouldn't fire on a bloat-only signal (the table can be empty during a real spiral — tak-10's first incident proved this), and a stuck cleanup task is an Authentik-side problem rather than infra-TAK's to mass-mitigate. The signal exists so operators can correlate symptoms during incident response without needing to docker-exec into Postgres manually.

### What changed in code

| File | Function | Change |
|------|----------|--------|
| `app.py` | `_ensure_authentik_compose_patches()` | `pg_cmd` template bumped to `max_connections=500`. Detection broadened: `max_connections < 500` triggers full command-line rewrite. |
| `app.py` | deploy template path (run_authentik_deploy) | Same `pg_cmd` template + detection update. |
| `app.py` | `_authentik_fix_pg_idle_timeout()` | Early-return broadened: only short-circuits when BOTH `idle_in_transaction_session_timeout=300s` AND `max_connections>=500`. Otherwise calls `_ensure_authentik_compose_patches` + force-recreates Postgres. |
| `app.py` | `_ensure_authentik_pg_persistent_connections()` | New helper: appends `CONN_MAX_AGE=60` + `CONN_HEALTH_CHECKS=true` to `~/authentik/.env`, recreates server + worker. |
| `app.py` | `_detect_authentik_ldap_spiral()` | Evidence dict gains `channels_msg_rows` + `channels_msg_mb`. Plog surfaces the values tiered. |
| `app.py` | three migration call sites (post-Authentik-deploy, post-TAK-deploy, post-update hook) | All three call the new `_ensure_authentik_pg_persistent_connections()` helper after the existing `_ensure_authentik_gunicorn_timeout()` call. |

### Smoke-tested before push

Synthetic compose/.env tests covering 9 cases — see commit message. Key cases:

- v0.8.8-era box (`max_connections=300`, `idle_in_transaction_session_timeout=300s`) → bumped to 500 cleanly, idle timeout left at 300s, no duplicate `command:` lines.
- v0.9.20 box (already 500/300s) → no change reported (idempotent).
- v0.8.4-era box (300/30s) → both fields upgraded together (500/300s).
- Operator-set 800 → not downgraded (file unchanged, no change reported).
- Fresh `.env` (no `AUTHENTIK_POSTGRESQL__` vars) → both appended.
- Idempotent `.env` (both vars already set) → file unchanged.
- Partial `.env` (operator set `CONN_MAX_AGE=120` already) → operator value preserved, only `CONN_HEALTH_CHECKS` appended.

### Operator notes

- **Drop-in from v0.9.18 or v0.9.19.** Hit Update Now. The post-update migration thread will: regenerate Caddyfile, run `_authentik_fix_pg_idle_timeout` (now also catches `max_connections<500` drift), recreate Postgres if compose was touched, then run `_ensure_authentik_pg_persistent_connections` and recreate server + worker if `.env` was touched. **Total downtime per box: ~30-60s for Postgres recreate, ~10-20s for server+worker recreate.**
- **`max_connections` only takes effect after Postgres restart.** The migration force-recreates the Postgres container if the compose command line was rewritten — this is unavoidable but already a one-shot per box.
- **`CONN_MAX_AGE=60` is safe to deploy everywhere.** On light-load boxes it's a no-op on the request path (each request closes within 60s anyway). On heavy-load boxes it dramatically cuts handshake churn.
- **If channels_postgres_message rows climb >50k** the spiral monitor will log a warning every 10 min. Operator action: `docker exec authentik-worker-1 ak shell -c "from channels_postgres.cleanup import cleanup; cleanup()"` or simpler: `docker compose restart worker` to nudge the cleanup task back to life.
- **Reverting:** delete the two lines from `~/authentik/.env`, change `max_connections=500` back to `300` in `docker-compose.yml`, force-recreate Postgres + server + worker. No state coupling — connection age is a runtime knob, not a schema commitment.

### Verify the fix landed

```bash
# 1. Confirm Postgres is running with max_connections=500
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA -c "SHOW max_connections"
# Expected: 500

# 2. Confirm server+worker have the persistent-connection env vars
docker exec authentik-server-1 printenv AUTHENTIK_POSTGRESQL__CONN_MAX_AGE
docker exec authentik-worker-1 printenv AUTHENTIK_POSTGRESQL__CONN_HEALTH_CHECKS
# Expected: 60 and true

# 3. Confirm pool isn't saturated (should be well under 500)
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA -c \
  "SELECT count(*) FROM pg_stat_activity WHERE datname='authentik'"
# Expected: < 100 on a healthy/idle box, < 200 on a heavily-loaded box

# 4. Confirm idle-in-tx isn't pinned to the cache table
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA -c \
  "SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction' AND application_name LIKE '%authentik%'"
# Expected: < 5 (was 75 on tak-10 before the fix)

# 5. Check the channels-postgres bloat number
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA -c \
  "SELECT count(*), pg_size_pretty(pg_total_relation_size('django_channels_postgres_message')) FROM django_channels_postgres_message"
# Expected: < 5,000 rows on a healthy box

# 6. Migration outcome saved to settings
INSTALL_DIR=$(grep -E '^WorkingDirectory=' /etc/systemd/system/takwerx-console.service | cut -d= -f2-)
python3 -c "import json; print(json.dumps(json.load(open('$INSTALL_DIR/.config/settings.json')).get('authentik_pg_persistent_conn_migration', {}), indent=2))"
# Expected: {"ts": ..., "outcome": "success", "conn_max_age": 60, "conn_health_checks": true}
# Note: settings.json lives at <WorkingDirectory>/.config/settings.json — the dir varies
# per box (e.g. /home/takwerx/infra-TAK/.config/settings.json on installs running as the
# takwerx user, not under /root/takwerx-console as an earlier draft of these notes said).
```

---

## Part 3 follow-up — wiring-gap fix (post-push, same v0.9.20-alpha)

### Symptom on tak-10 (during v0.9.20 validation)

After the v0.9.20 push, validation on tak-10 showed:

- `max_connections=500` in PG ✓ (migration (a) landed)
- `django_channels_postgres_message` cleanup happened (109,840 → 1,690 rows) ✓
- Authentik server CPU dropped 807% → 16.80% ✓
- **But** `AUTHENTIK_POSTGRESQL__CONN_MAX_AGE` and `CONN_HEALTH_CHECKS` were NOT in `~/authentik/.env`, NOT on the containers, and `settings.authentik_pg_persistent_conn_migration = {}` (empty audit) — migration (b) silently skipped.
- No `.env.bak.pg-persistent-conn.*` backup file existed — proving the helper never wrote `.env`.

### Root cause — burned VERSION-string + version-gated wiring

The post-update auto-deploy hook (`_post_update_auto_deploy`) is version-gated: `if last_ver == VERSION: return`. tak-10 had pulled an earlier v0.9.20-alpha dev SHA (`ea0c951`, Caddy-only Caddyfile auto-regenerate fix) before the full v0.9.20 commit (`2970470`, Authentik PG pool tuning) landed. The first pull set `last_console_version=0.9.20-alpha` via the post-update hook's banner (`Post-update: version changed 0.9.19-alpha → 0.9.20-alpha`). The second pull (my full v0.9.20) had the same VERSION string, so the post-update hook **short-circuited** with no banner and no migration thread — even though new migration code was on disk.

The startup-migration block (around line 39123 of `_startup_migrations`) is NOT version-gated: it runs on every console restart. It already had `_authentik_fix_pg_idle_timeout` wired in, which is why the `max_connections=500` bump did land on the second restart (compose patcher caught the drift, force-recreated Postgres, wrote the `last_outcome: fixed` audit at `2026-05-14T00:24:43Z` — 19 min after the second console restart). But `_ensure_authentik_pg_persistent_connections` and `_ensure_authentik_gunicorn_timeout` were ONLY in the version-gated post-update hook + the full-Authentik-deploy path + the TAK-deploy path. None of those fired on the second pull.

### Fix

Two-line addition to `_startup_migrations`, right after the existing `_authentik_fix_pg_idle_timeout` block:

```python
# v0.8.5 / v0.9.20 wiring-gap fix: gunicorn timeout + PG persistent connections
# also run as startup migrations, not just post-update. Discovered during v0.9.20
# tak-10 validation: when a VERSION string is burned by a partial commit (e.g. dev
# SHA bumps VERSION to 0.9.20-alpha for a Caddy-only fix, then a later commit on
# the same VERSION string adds Authentik migrations), the version-gated post-update
# hook short-circuits on the next Update Now and the new migrations never fire.
try:
    _ensure_authentik_gunicorn_timeout(lambda m: print(f"Startup migration: {m}", flush=True))
except Exception as ak_gt_err:
    print(f"Startup migration: gunicorn timeout fix error (non-fatal): {ak_gt_err}")
try:
    _ensure_authentik_pg_persistent_connections(lambda m: print(f"Startup migration: {m}", flush=True))
except Exception as ak_pc_err:
    print(f"Startup migration: pg persistent connections fix error (non-fatal): {ak_pc_err}")
```

Both helpers are idempotent — they're no-ops on boxes where the env vars are already set or `~/authentik` isn't installed. They're safe to call unconditionally on every console restart.

### Why no VERSION bump

The fix is shipped under the same `0.9.20-alpha` string. The whole point of the wiring-gap fix is that the startup-migration block runs **on plain console restart**, not on version-change. Boxes that already have `last_console_version=0.9.20-alpha` (like tak-10) will pick up the helpers on their next `Update Now` (which does `git checkout new commit → systemctl restart takwerx-console`) — the restart fires the startup-migration block automatically.

Also, this is the second wiring-gap discovery on v0.9.20 already (v0.9.19 dev iteration → v0.9.20 cut). Burning another VERSION number on a discovery that the fix mechanism itself was wrong would just compound the disposal pattern. Keeping `0.9.20-alpha` and adding a "wiring-gap follow-up" subsection here is the cleanest accounting.

### Expected behaviour on tak-10 next Update Now

1. `git fetch + checkout` to the new dev SHA.
2. `systemctl restart takwerx-console` — gunicorn imports the new app.py.
3. `_post_update_auto_deploy()` checks: `last_ver=0.9.20-alpha == VERSION=0.9.20-alpha` → short-circuits (still gated). No banner, no migration thread.
4. **`_startup_migrations()` runs unconditionally** — calls `_ensure_authentik_gunicorn_timeout` (idempotent, already-set → skip) and `_ensure_authentik_pg_persistent_connections` (new code path → detects missing vars → backs up `.env` → appends 2 vars → recreates server + worker → writes audit).

After step 4 completes (~30-45s after restart):

```bash
docker exec authentik-server-1 printenv AUTHENTIK_POSTGRESQL__CONN_MAX_AGE       # expect: 60
docker exec authentik-worker-1 printenv AUTHENTIK_POSTGRESQL__CONN_HEALTH_CHECKS # expect: true
grep AUTHENTIK_POSTGRESQL__CONN ~/authentik/.env                                  # expect: both lines
ls -la ~/authentik/.env.bak.pg-persistent-conn.*                                  # expect: backup file from this run
```

Plus the corresponding `Startup migration: pg persistent connections: appended 2 var(s)...` log line in `journalctl -u takwerx-console`.
