# v0.8.5-alpha Release Notes

## Headline: proactive LDAP routing migration + gunicorn timeout bump + verifier hardening

v0.8.4 introduced a reactive, one-shot post-update migration that reversed the v0.8.0 internal LDAP routing on boxes already spiraling. Field testing on `tak-10` and `ssdnodes` confirmed it works for actively-spiraling boxes. Field testing on `responder` (April 2026) exposed two **separate** problems v0.8.4 didn't solve:

1. **The reactive trigger doesn't fire on latent misroutes.** Responder's LDAP outpost was on internal direct routing (`http://authentik-server-1:9000`) but the cached `adm_ldapservice` session masked all the failures — there was no spiral signature in the outpost log because the only client successfully binding was the cached SA. The bug was invisible until `webadmin` (no cache) attempted a fresh bind, which immediately recursed and threw "exceeded stage recursion depth". Reactive detection was correctly NOT triggering — there was no spiral yet — but the box was one fresh-bind away from one.
2. **The verifier's destructive recovery destroyed user records on inconclusive probes.** When `_test_ldap_bind_dn` couldn't determine bind state (ldapsearch missing on host AND outpost showing `exceeded stage recursion depth` instead of credential markers), it returned `False` — which `_ensure_authentik_webadmin` interpreted as confirmed-failure and triggered DELETE+POST recreate of `webadmin`. The DELETE silently failed (race / async), and the POST returned `400 username must be unique`. This is what the user saw repeatedly during the responder incident.

Field testing on `ssdnodes` exposed a third weakness from v0.8.4 itself:

3. **The reactive detection signal can be hidden by high-volume normal binds.** On busy boxes the outpost log fills with thousands of benign `Bind request` lines per minute, pushing spiral markers off the `--tail 200` sample window. `bash grep` over the full log showed 14 spiral markers, but the migration sampled 200 lines and saw 0.

v0.8.5 ships fixes for all three without changing the v0.8.4 safety invariants.

## Changes

### 1. Proactive routing migration (`_ensure_authentik_ldap_outpost_on_fqdn`)

This is the headline fix for the responder-class bug. A new function migrates the LDAP outpost from internal direct routing (`http://authentik-server-1:9000`) to FQDN routing (`https://<fqdn>`) **before** a spiral has manifested, gated only on whether the box's load profile justifies it.

Preconditions (ALL must hold):

1. `~/authentik/docker-compose.yml` and `.env` exist
2. Outpost `AUTHENTIK_HOST` is currently `http://authentik-server-1:9000`
3. `.env` has `AUTHENTIK_HOST=https://<fqdn>` (FQDN configured)
4. `https://<fqdn>/-/health/live/` is reachable from inside the LDAP container (Caddy serving)
5. **TAK Server is installed** at `/opt/tak` — the heavy LDAP load profile that exposes the bug. Light/console-only deployments stay on internal routing (no Caddy round-trip overhead).

When all 5 hold, the function:
- Backs up `docker-compose.yml` to `<path>.bak.proactive-routing.<timestamp>`
- Rewrites the LDAP service to `AUTHENTIK_HOST: https://<fqdn>` + `extra_hosts: <fqdn>:host-gateway`
- Force-recreates ONLY the LDAP container
- Validates websocket-connected, no TLS errors, no 502/503 — restores the backup if any check fails
- Persists outcome to `settings.json` under `authentik_proactive_routing_migration`

Idempotent. No-op on FQDN-routed boxes, on light-load boxes, on boxes without an FQDN, and on boxes where Caddy isn't ready yet (will retry on the next trigger).

**Triggers (so the user never has to think about this):**
- Fresh Authentik deploy / reconfigure completion
- TAK Server deploy completion (catches the Authentik-then-TAK install order)
- Post-update migration after every Update Now
- Periodic spiral monitor (every 10 minutes, runs the proactive pass before the reactive pass)

The reactive `_apply_authentik_ldap_routing_repair` still runs second as a fallback for boxes where the proactive preconditions weren't met (e.g. Caddy temporarily down) but the box has already spiraled.

### 2. Gunicorn worker timeout bump (30s → 120s) — kills the SIGABRT cascade on heavy-load boxes

Field testing during v0.8.5 development on `tak-10` (≈3.5 LDAP binds/sec sustained, 1000+ binds in 5 min) surfaced a separate failure mode that the routing migration alone does not address. With FQDN routing already correct, Caddy logs still showed periodic upstream `EOF` (translated to 502) at 70-90 ms request lifetimes, and outpost logs still showed brief `exceeded stage recursion depth` bursts. Authentik server logs explained why:

```
[CRITICAL] WORKER TIMEOUT (pid:95632)
[ERROR] Worker (pid:95632) was sent SIGABRT!
[INFO] Booting worker with pid: 96213
```

Authentik 2026.2.2's flow planner under heavy LDAP-bind load occasionally exceeds gunicorn's upstream default 30s worker timeout. Some flow plans we observed completed in 124-136s (status 200, just slow) — others in 211-218s. Gunicorn assumes a worker silent for >30s is hung and SIGABRTs it, which **drops every in-flight TCP connection in that worker** mid-response. Caddy sees connection-reset → returns 502 to the LDAP outpost → the outpost retries → the retry hits "exceeded stage recursion depth" inside the same flow.

The fix is the smallest possible change: a single env var appended to `~/authentik/.env`:

```
GUNICORN_CMD_ARGS=--timeout=120
```

Then a server-only `docker compose up -d --no-deps --force-recreate server` to apply it (worker / postgresql / redis / ldap untouched, ~10-30 s of API unavailability with cached LDAP service-account sessions surviving).

Why 120 s and not higher: 124-136 s slow plans complete with status 200 under 120 s (with margin); the 200 s+ outliers are queued requests waiting for an unblocked worker, not a single 200 s request — once any worker frees up, the queue drains. 120 s catches the slow-plan case without leaving workers hung indefinitely on a genuinely-stuck request.

Safety profile (why this is safe everywhere, not just slow boxes):

- **Healthy / fast boxes** (responder, ssdnodes, light-load consoles): timeout never fires (typical request 50-200 ms). No behavioral change.
- **Heavy-load / slow-flow boxes** (tak-10): absorbs slow plans without dropping connections. SIGABRT cascade goes silent.
- **Boxes that already set `GUNICORN_CMD_ARGS`** (operator override, future Authentik defaults, etc.): idempotent no-op — we never overwrite.
- **Operator-revertible**: delete the line, recreate server. Zero state coupling.

The function lives at `_ensure_authentik_gunicorn_timeout(plog, value=120)` in `app.py`, alongside `_ensure_authentik_ldap_outpost_on_fqdn`, and is hooked into the same trigger points (Authentik deploy completion, TAK Server deploy completion, post-update migration). It is **deliberately NOT** hooked into the periodic spiral monitor — this is one-shot config, not a hot fix to re-apply, and any operator override should stick.

Outcome is persisted to `settings.json` under `authentik_gunicorn_timeout_migration`.

### 3. Verifier hardening — tri-state bind probe, no destructive recovery on inconclusive

`_test_ldap_bind_dn` now wraps `_test_ldap_bind_dn_verdict`, which returns `'ok' | 'fail' | 'inconclusive'` instead of `True | False`.

A verdict of `'inconclusive'` is returned when:
- `ldapsearch` is unavailable AND the outpost log shows `exceeded stage recursion depth`, `nil pointer dereference`, EOFs, or generic flow errors with no credential markers
- No credential markers found AND no `ldapsearch` available

`_ensure_authentik_webadmin` now:
- On `'ok'`: returns success (unchanged)
- On `'inconclusive'`: returns success WITHOUT destructive recovery, but kicks off `_ensure_authentik_ldap_outpost_on_fqdn` since this verdict often indicates the responder spiral
- On `'fail'`: only then does the DELETE+POST recreate path. Even then, before POSTing the new record we re-query Authentik to confirm the DELETE actually removed the previous user — if it didn't, we skip the recreate and surface a clear error instead of triggering the `400 username must be unique` regression.

Also: the probe now installs `ldap-utils` (or `openldap-clients`) once at the top via `_ensure_ldapsearch()` so the inconclusive case is rare on Debian/RHEL hosts.

### 4. Dual-signal spiral detection — two-tier markers (Postgres + spiral-specific outpost log)

New helper `_detect_authentik_ldap_spiral()` returns spiral confirmation if **either**:

- LDAP outpost log shows ≥1 **spiral-specific** marker in the last **1000 lines** (was 200 in v0.8.4):
  - `result code 50` (Authentik flow refusing — LDAP "unwilling to perform")
  - `nil pointer` (Go runtime crash in outpost)
  - `exceeded stage recursion` (the responder signature)
  - `502 bad gateway` (upstream Authentik overloaded)
  - `503 service unavailable` (upstream Authentik refusing connections)
- OR Postgres has **≥30 connections in `idle in transaction`** state from `application_name LIKE '%authentik%'`

**General markers** (`failed to execute flow`, `EOF`) are tracked for forensics but **do not trip alone** — they appear on every healthy box (user mistypes a password → `failed to execute flow`; LDAP client disconnects normally → `: EOF`). v0.8.5 dev testing on tak-10 caught this: 2 unique general markers from a transient container restart triggered the v0.8.4-style "≥2 unique" rule even though the box was perfectly healthy (`idle-in-trans=0`). The gate ("already on FQDN — skipping") caught it, but the threshold was wrong. v0.8.5 ships the tightened two-tier logic.

The Postgres signal is the durable one — it survives LDAP container recreates (which wipe the outpost log) and can't be drowned out by high bind volume. Healthy boxes sit at 0–3 idle-in-trans; a spiraling box sits at 50–200+. The 30 threshold is well above peak normal load and well below the spiral floor.

The outpost log signal is retained because it's faster to check and catches early-stage spirals before Postgres congestion sets in. With the spiral-specific tier, false positives from restart artifacts are eliminated while still catching the responder/`ssdnodes`/Jarrett class within seconds.

### 5. Periodic spiral monitor (background thread)

New `_authentik_spiral_monitor()` runs as a daemon thread inside the console. Every 10 minutes it calls the dual-signal detector, and if a spiral is confirmed it runs the same idempotent `_apply_authentik_ldap_routing_repair` the post-update migration runs.

Safeguards:
- **Single-instance lock** (`/tmp/takwerx-spiral-monitor.lock`, PID-checked). Gunicorn runs N workers; only one runs the monitor. Steals the lock if the holder PID is dead, so a worker restart always leaves a live monitor behind.
- **Repair rate limit**: max 1 repair attempt per 6 hours, recorded in `settings.json` under `authentik_spiral_last_repair`. Prevents thrashing on pathological boxes (e.g. spiral confirmed but Caddy unreachable — the repair would skip every 10 min anyway, but this caps the noise).
- **No-op gates**: still skips on healthy boxes, no-FQDN boxes, FQDN-routed boxes, and boxes without Authentik installed. Same gates as the migration — by design.

### 6. Granular gate logging in routing repair

Every early-return in `_apply_authentik_ldap_routing_repair` now logs **why** it skipped, with the same `routing repair: ...` prefix so operators can grep one stream. Examples:

```
routing repair: ~/authentik/docker-compose.yml or .env missing — skipping (Authentik not installed)
routing repair: no FQDN in .env (need AUTHENTIK_HOST=https://...) — skipping
routing repair: LDAP service already on FQDN routing in compose — skipping (already correct)
routing repair: no spiral evidence — leaving alone (outpost healthy or pre-spiral)
routing repair: cannot reach https://<fqdn> from LDAP container — skipping (Caddy not ready or DNS issue; box would end up worse)
routing repair: spiral CONFIRMED on http://authentik-server-1:9000 — proceeding to migrate to FQDN
spiral check: postgres signal: 47 idle-in-trans (≥30 threshold)
outpost markers (last 1000 lines): result code 50=14, nil pointer=3, exceeded stage recursion=2, eof=8
```

This was the diagnostic gap on `ssdnodes`: the v0.8.4 migration logged "0/2 markers — leaving alone" but didn't say which 0/2 it sampled or how big its window was. Now every gate decision is auditable from `journalctl -u takwerx-console`.

### 7. Spiral repair forensics persisted to settings.json

Every repair attempt (success, validation failure, recreate failure) writes:

```json
"authentik_spiral_last_repair": {
  "ts": 1730000000,
  "outcome": "success",
  "evidence": { "outpost_unique_markers": 4, "pg_idle_in_trans": 47, "pg_total_conns": 89, "reason": "..." },
  "outpost_markers": { "result code 50": 14, "nil pointer": 3, ... }
}
```

Used by the monitor for rate limiting; also useful when an operator reports "I think it spiraled and recovered last night" — the timestamp + evidence is right there.

## Who is affected

- **Boxes with TAK Server installed but LDAP outpost still on internal direct routing** (the `responder` class): proactive migration fires on the next Update Now, on the next deploy, or within 10 min via the periodic monitor — whichever comes first. No manual action needed; no waiting for a spiral.
- **Boxes still actively spiraling after v0.8.4 update** (the `ssdnodes` case): the periodic monitor's reactive pass detects via Postgres signal within 10 min and runs the repair. No manual action needed.
- **Boxes that drift back into a spiral** later (Caddy bounce, clock-aligned Mission API hammer, etc.): same — within 10 min, automatic repair, rate-limited to 6h.
- **Heavy-LDAP-load boxes hitting gunicorn `WORKER TIMEOUT` / SIGABRT** (the `tak-10` class — sustained 3+ binds/sec, slow Authentik flow plans): gunicorn timeout migration fires on the next Update Now (or next deploy), one-shot. Server container restarts with `--timeout=120`. SIGABRT cascade and the 502→outpost-retry→stage-recursion downstream go silent.
- **Console / light-load boxes** (no `/opt/tak`): proactive routing migration intentionally skips. Gunicorn timeout migration still applies (it's safe everywhere — timeout never fires on fast boxes anyway).
- **Healthy / FQDN-routed boxes**: routing trigger is a no-op (proactive function detects existing FQDN routing and exits in <1s). Gunicorn timeout migration applies once on first encounter, then idempotent no-op forever after.
- **Boxes without Authentik installed**: every function sees no `~/authentik/docker-compose.yml` and skips. No errors.
- **Operators who hit the verifier `400 username must be unique` regression on `responder`**: cannot recur — the destructive DELETE+POST recreate is now gated on a confirmed-fail verdict AND a re-query that confirms the DELETE actually completed.
- **Operators who already set `GUNICORN_CMD_ARGS`** in `~/authentik/.env` for any reason: untouched — the migration never overwrites an existing value.

## What v0.8.5 explicitly does NOT change

- **Authentik image tag** — still tracking latest 2026.2.x. The slow `policybindingmodel` flow regression is upstream; FQDN-via-Caddy + the 120s gunicorn timeout are the workarounds until they ship a fix.
- **The v0.8.4 routing repair function itself** — same compose rewrite, same Caddy probe, same 30s validation, same auto-rollback. Only the *trigger* and *re-run cadence* are improved.
- **The v0.8.0 LDAP HOST migration** — unchanged from the v0.8.4 patch (only fires on positive TLS-failure evidence, not absence of websocket connect).
- **`AUTHENTIK_WEB_WORKERS=4`** logic from v0.8.2 — preserved.
- **PG `idle_in_transaction_session_timeout=30s`** logic from v0.8.3/v0.8.4 — preserved.
- **Guard Dog** — no changes; existing Authentik health monitor and 3-restart-per-day cap unchanged.
- **No UI changes.** Pure backend. Behavior is identical except for self-healing.
- **Rollback to arbitrary GitHub release** — still planned, now targeting v0.8.6.
- **Guard Dog Postgres alert** — still planned, now targeting v0.8.6.

## Files changed

| File | Change |
|---|---|
| `app.py` | New `_ensure_authentik_ldap_outpost_on_fqdn()` — proactive routing migration, gated on TAK installed + FQDN configured + Caddy reachable |
| `app.py` | Proactive routing migration hooked into Authentik deploy completion, TAK Server deploy completion, post-update migration, and spiral monitor |
| `app.py` | New `_ensure_authentik_gunicorn_timeout(plog, value=120)` — appends `GUNICORN_CMD_ARGS=--timeout=120` to `~/authentik/.env` if missing, recreates the server container only, validates via `printenv` inside the container, persists outcome to `settings.json` under `authentik_gunicorn_timeout_migration` |
| `app.py` | Gunicorn timeout migration hooked into Authentik deploy completion, TAK Server deploy completion, and post-update migration (NOT spiral monitor — one-shot config, not a hot fix) |
| `app.py` | New `_test_ldap_bind_dn_verdict()` — tri-state probe (`'ok' / 'fail' / 'inconclusive'`); installs `ldap-utils` if missing; treats `exceeded stage recursion depth` and similar as inconclusive |
| `app.py` | `_test_ldap_bind_dn` now wraps the verdict function (returns True only on confirmed-ok; backward-compatible) |
| `app.py` | `_ensure_authentik_webadmin` no longer does destructive DELETE+POST recreate on inconclusive verdicts; on confirmed-fail it re-queries to confirm DELETE before POST (kills the `400 username must be unique` regression) |
| `app.py` | New helper `_detect_authentik_ldap_spiral()` — dual-signal (postgres + outpost log) detector |
| `app.py` | `_apply_authentik_ldap_routing_repair` uses the new detector; granular gate logging at every early-return; `--tail 1000` (was 200); spiral repair attempts recorded in `settings.json` |
| `app.py` | New `_authentik_spiral_monitor()` daemon thread — 10 min interval, 6h repair rate limit, single-instance PID-checked lock; runs proactive migration first, reactive repair second |
| `app.py` | Spiral monitor thread started at module load, alongside `_post_update_auto_deploy()` |
| `app.py` | VERSION bumped to `0.8.5-alpha` |
| `docs/HANDOFF-LDAP-AUTHENTIK.md` | New section "v0.8.5 — proactive routing migration + verifier hardening" with the responder finding, preconditions, and triggers |

## How to verify on a deployed box

After Update Now (or after pulling and restarting per `docs/PULL-AND-RESTART.md`):

```bash
# 1. Confirm version
curl -ks https://localhost:5001/api/system/version | jq -r .version
# → 0.8.5-alpha

# 2. Confirm monitor is alive (one line at console startup)
sudo journalctl -u takwerx-console --since "5 min ago" | grep "spiral monitor"
# → [spiral monitor] PID <N> acquired monitor lock — starting (10 min interval, 6h repair rate limit)

# 3. Confirm the LDAP outpost is on FQDN routing (the responder fix)
grep AUTHENTIK_HOST ~/authentik/docker-compose.yml | grep -A0 "ldap" -B0
# → On a TAK-installed box with Caddy: AUTHENTIK_HOST: https://<your-fqdn>
# → On a console-only box: AUTHENTIK_HOST: http://authentik-server-1:9000  (intentional)

# 4. Check the proactive routing migration outcome (if any was needed)
jq '.authentik_proactive_routing_migration' ~/.config/settings.json 2>/dev/null \
  || jq '.authentik_proactive_routing_migration' /home/takwerx/infra-TAK/.config/settings.json 2>/dev/null
# → null (no migration needed) OR { ts, outcome: "success", fqdn: "..." }

# 5. Confirm the gunicorn timeout bump applied (the tak-10 fix)
docker exec authentik-server-1 printenv GUNICORN_CMD_ARGS
# → --timeout=120
grep GUNICORN_CMD_ARGS ~/authentik/.env
# → GUNICORN_CMD_ARGS=--timeout=120
jq '.authentik_gunicorn_timeout_migration' ~/.config/settings.json 2>/dev/null \
  || jq '.authentik_gunicorn_timeout_migration' /home/takwerx/infra-TAK/.config/settings.json 2>/dev/null
# → { ts, outcome: "success", value: 120 }

# 6. After applying, confirm SIGABRT cascade has stopped on heavy-load boxes
docker logs authentik-server-1 --since 30m 2>&1 | grep -cE "WORKER TIMEOUT|SIGABRT"
# → 0 on a box that was hitting it before; 0 on a box that never hit it

# 7. Check current spiral state on the box (sanity)
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tAc \
  "SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction' AND application_name LIKE '%authentik%';"
# → healthy boxes: 0-3 ; spiraling: 30+

# 8. After 10 min on a misrouted-but-not-yet-spiraling box, look for proactive migration:
sudo journalctl -u takwerx-console --since "15 min ago" | grep -E "spiral monitor|proactive routing|routing repair|gunicorn timeout"
```

## Responder-class manual recovery (already-broken boxes)

If a box is already exhibiting `LDAP Result Code 50` / `exceeded stage recursion depth` for fresh binds (cached SA still works):

```bash
# 1. Update to v0.8.5
# 2. Wait up to 10 min for the spiral monitor's proactive pass to migrate routing
sudo journalctl -u takwerx-console --since "12 min ago" | grep "proactive routing"
# Expected: "proactive routing: rewrote LDAP service → AUTHENTIK_HOST=https://<fqdn> ..."

# If the proactive pass skipped because Caddy probe failed, fix Caddy first:
docker exec authentik-ldap-1 wget --spider -q https://<your-fqdn>/-/health/live/; echo $?
# → 0 means Caddy is reachable; non-zero means fix Caddy/DNS first
```

If the box was hit by the verifier `400 username must be unique` regression on v0.8.4 or earlier and `webadmin` ended up in a weird state:

```bash
# Check is_superuser on webadmin
TOKEN=$(grep AUTHENTIK_BOOTSTRAP_TOKEN ~/authentik/.env | cut -d= -f2)
curl -ks -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9090/api/v3/core/users/?search=webadmin | jq '.results[] | {pk, username, is_superuser, groups_obj}'
# → if is_superuser: false, add webadmin to "authentik Admins" group:
ADMINS_PK=$(curl -ks -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9090/api/v3/core/groups/?search=authentik+Admins" \
  | jq -r '.results[] | select(.name=="authentik Admins") | .pk')
WEBADMIN_PK=$(curl -ks -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9090/api/v3/core/users/?search=webadmin" \
  | jq -r '.results[] | select(.username=="webadmin") | .pk')
curl -ks -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"pk\":$WEBADMIN_PK}" \
  "http://127.0.0.1:9090/api/v3/core/groups/$ADMINS_PK/add_user/"
```
