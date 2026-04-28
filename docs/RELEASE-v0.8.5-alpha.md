# v0.8.5-alpha Release Notes

## Stability hardening for the v0.8.4 LDAP routing repair

v0.8.4 introduced a one-shot, post-update migration that detects boxes spiraling on the v0.8.0 internal LDAP routing change and reverses them back to FQDN-via-Caddy. Field testing on `tak-10`, `responder`, and `ssdnodes` exposed two weaknesses:

1. **The detection signal can be hidden by high-volume normal binds.** On busy boxes (Mission API / DataSync / lots of CoT clients), the LDAP outpost log fills with thousands of benign `Bind request` lines per minute. The migration's `--tail 200` sample window can be 100% benign even while the box is actively spiraling — the spiral markers get pushed off the visible tail. On `ssdnodes` during testing, `bash grep` over the full log showed 14 spiral markers, but the migration sampled 200 lines and saw 0.
2. **The repair runs once, at update time.** A spiral that manifests 30 minutes after Update Now (after traffic ramps, after a clock-aligned Mission API poll, after a Caddy hiccup) gets no second chance. Operators have to notice the CPU spike and re-run Update Now manually — exactly the "no CLI" problem v0.8.4 was supposed to solve.

v0.8.5 fixes both without changing any of the safety invariants from v0.8.4.

## Changes

### 1. Dual-signal spiral detection (Postgres + outpost log)

New helper `_detect_authentik_ldap_spiral()` returns spiral confirmation if **either**:

- LDAP outpost log shows ≥2 unique spiral markers in the last **1000 lines** (was 200 in v0.8.4), OR
- Postgres has **≥30 connections in `idle in transaction`** state from `application_name LIKE '%authentik%'`

The Postgres signal is the durable one — it survives LDAP container recreates (which wipe the outpost log) and can't be drowned out by high bind volume. Healthy boxes sit at 0–3 idle-in-trans; a spiraling box sits at 50–200+. The 30 threshold is well above peak normal load and well below the spiral floor.

The outpost log signal is retained because it's faster to check and catches early-stage spirals before Postgres congestion sets in.

### 2. Periodic spiral monitor (background thread)

New `_authentik_spiral_monitor()` runs as a daemon thread inside the console. Every 10 minutes it calls the dual-signal detector, and if a spiral is confirmed it runs the same idempotent `_apply_authentik_ldap_routing_repair` the post-update migration runs.

Safeguards:
- **Single-instance lock** (`/tmp/takwerx-spiral-monitor.lock`, PID-checked). Gunicorn runs N workers; only one runs the monitor. Steals the lock if the holder PID is dead, so a worker restart always leaves a live monitor behind.
- **Repair rate limit**: max 1 repair attempt per 6 hours, recorded in `settings.json` under `authentik_spiral_last_repair`. Prevents thrashing on pathological boxes (e.g. spiral confirmed but Caddy unreachable — the repair would skip every 10 min anyway, but this caps the noise).
- **No-op gates**: still skips on healthy boxes, no-FQDN boxes, FQDN-routed boxes, and boxes without Authentik installed. Same gates as the migration — by design.

### 3. Granular gate logging in routing repair

Every early-return in `_apply_authentik_ldap_routing_repair` now logs **why** it skipped, with the same `routing repair: ...` prefix so operators can grep one stream. Examples:

```
routing repair: ~/authentik/docker-compose.yml or .env missing — skipping (Authentik not installed)
routing repair: no FQDN in .env (need AUTHENTIK_HOST=https://...) — skipping
routing repair: LDAP service already on FQDN routing in compose — skipping (already correct)
routing repair: no spiral evidence — leaving alone (outpost healthy or pre-spiral)
routing repair: cannot reach https://<fqdn> from LDAP container — skipping (Caddy not ready or DNS issue; box would end up worse)
routing repair: spiral CONFIRMED on http://authentik-server-1:9000 — proceeding to migrate to FQDN
spiral check: postgres signal: 47 idle-in-trans (≥30 threshold)
outpost markers (last 1000 lines): result code 50=14, nil pointer=3, eof=8
```

This was the diagnostic gap on `ssdnodes`: the v0.8.4 migration logged "0/2 markers — leaving alone" but didn't say which 0/2 it sampled or how big its window was. Now every gate decision is auditable from `journalctl -u takwerx-console`.

### 4. Spiral repair forensics persisted to settings.json

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

- **Boxes still spiraling after v0.8.4 update** (the `ssdnodes` case): the periodic monitor will detect via Postgres signal within 10 min of v0.8.5 starting and run the repair. No manual action needed.
- **Boxes that drift back into a spiral** later (Caddy bounce, clock-aligned Mission API hammer, etc.): same — within 10 min, automatic repair, rate-limited to 6h.
- **Healthy boxes**: monitor runs, sees no spiral, sleeps. One log line at startup, then silent.
- **Boxes without Authentik installed**: monitor sees no `~/authentik/docker-compose.yml` and skips. No errors.

## What v0.8.5 explicitly does NOT change

- **Authentik image tag** — still tracking latest 2026.2.x. The slow `policybindingmodel` flow regression is upstream; FQDN-via-Caddy is the workaround until they ship a fix.
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
| `app.py` | New helper `_detect_authentik_ldap_spiral()` — dual-signal (postgres + outpost log) detector |
| `app.py` | `_apply_authentik_ldap_routing_repair` uses the new detector; granular gate logging at every early-return; `--tail 1000` (was 200); spiral repair attempts recorded in `settings.json` |
| `app.py` | New `_authentik_spiral_monitor()` daemon thread — 10 min interval, 6h repair rate limit, single-instance PID-checked lock |
| `app.py` | Spiral monitor thread started at module load, alongside `_post_update_auto_deploy()` |
| `app.py` | VERSION bumped to `0.8.5-alpha` |
| `docs/HANDOFF-LDAP-AUTHENTIK.md` | New section "v0.8.5 — dual-signal detection + periodic monitor" with rationale and diagnostic queries |

## How to verify on a deployed box

After Update Now (or after pulling and restarting per `docs/PULL-AND-RESTART.md`):

```bash
# 1. Confirm version
curl -ks https://localhost:5001/api/system/version | jq -r .version
# → 0.8.5-alpha

# 2. Confirm monitor is alive (one line at console startup)
sudo journalctl -u takwerx-console --since "5 min ago" | grep "spiral monitor"
# → [spiral monitor] PID <N> acquired monitor lock — starting (10 min interval, 6h repair rate limit)

# 3. Check current spiral state on the box (sanity)
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tAc \
  "SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction' AND application_name LIKE '%authentik%';"
# → healthy boxes: 0-3 ; spiraling: 30+

# 4. After 10 min on a spiraling box, look for monitor action:
sudo journalctl -u takwerx-console --since "15 min ago" | grep -E "spiral monitor|routing repair"
```
