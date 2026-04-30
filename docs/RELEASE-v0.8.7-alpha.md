# v0.8.7-alpha Release Notes

## Headline: Authentik stability — fixing the v0.8.2 env-var-name silent-ignore bug + applying official tunings + periodic auto-restart

> **The bug we've been carrying for five releases:** Since v0.8.2 (late April 2026), every box in the fleet has been running with half the gunicorn workers we thought, because we wrote `AUTHENTIK_WEB_WORKERS=4` (single underscore) to `.env` and Authentik 2026.x silently ignored it. The correct name is `AUTHENTIK_WEB__WORKERS` (double underscore) per the [official Authentik docs](https://docs.goauthentik.io/install-config/configuration/) — "the double-underscores are intentional." Apr 30 2026 on tak-10: `docker top authentik-server-1` showed 2 gunicorn workers despite our config saying 4. `ak dump_config` confirmed cache and log_level were also at defaults, never tuned. v0.8.7 fixes all of this and adds a runtime-config verifier so we can never have this silent-default scenario again.

The Apr 30 2026 investigation started chasing CPU drift on tak-10 (server p50 140%+ vs sibling box at p50 1.9%, identical hardware and config). We tried postgres VACUUM, autovacuum tuning, worker bumps, worker reverts, ASGI WebSocket loop fixes, runtime state drift theories — band-aid after band-aid. The breakthrough came when an operator pushed back: "is there any info on the internet about optimizing authentik? I feel like we are doing all this half-ass shit." That sent us to the official docs for the first time, where the very first sentence explained why every config we'd been writing was being ignored.

v0.8.7 fixes the silent-ignore bug, applies the official optimization recommendations, ships a runtime-config verifier, and keeps the periodic auto-restart from the previous v0.8.7 design as belt-and-suspenders for the legitimate runtime-state-drift cases.

---

## Changes

### 1. Fix the v0.8.2 env-var-name silent-ignore bug + apply official tunings

**The bug:** Since v0.8.2, the post-update migration in `_post_update_auto_deploy` wrote `AUTHENTIK_WEB_WORKERS=4` (single underscore) to `~/authentik/.env`. Authentik 2026.x reads `AUTHENTIK_WEB__WORKERS` (double underscore). The single-underscore form was silently ignored on every box for five releases.

**The proof (tak-10, Apr 30 2026):**

```bash
$ grep AUTHENTIK_WEB ~/authentik/.env
AUTHENTIK_WEB_WORKERS=4

$ docker top authentik-server-1 -o pid,cmd | grep gunicorn
gunicorn: master [authentik.root.asgi:application]
gunicorn: worker [authentik.root.asgi:application]   ← worker 1
gunicorn: worker [authentik.root.asgi:application]   ← worker 2
                                                    ← TWO workers, not four

$ docker exec authentik-worker-1 ak dump_config | python3 -c "import json,sys; o=sys.stdin.read(); cfg=json.loads(o[o.find('{'):]); print(cfg.get('cache'),cfg.get('log_level'))"
{'timeout': 300, 'timeout_flows': 300, 'timeout_policies': 300} info
```

Every cache setting at defaults. `log_level=info` (default). Worker count 2 (default). Every official optimization, never applied.

**New function: `_authentik_apply_official_tunings(plog)`** in `app.py`. Idempotent. Edits `~/authentik/.env`:

- **Removes** `AUTHENTIK_WEB_WORKERS=N` lines (the single-underscore wrong name; was being ignored).
- **Adds** `AUTHENTIK_WEB__WORKERS=4` (correct double-underscore name; finally honored — doubles capacity from default 2).
- **Adds** `AUTHENTIK_CACHE__TIMEOUT_FLOWS=600` (2x default 300s — flows rarely change; reduces DB pressure).
- **Adds** `AUTHENTIK_CACHE__TIMEOUT_POLICIES=600` (2x default 300s — policies rarely change; reduces DB pressure).
- **Adds** `AUTHENTIK_LOG_LEVEL=warning` (down from default `info` — reduces log overhead on busy boxes).

Only adds keys that are **missing**. Never overwrites operator-set values. Records outcome to `settings.authentik_official_tunings`.

Hooked into both:
- **`_startup_migrations`** — runs on every console startup. Function self-gates (returns False on subsequent runs when no changes needed), so the recreate only fires once per box.
- **`_post_update_auto_deploy`** — superseded the v0.8.2 migration block. Fresh deploys get the correct config from the very first Update Now.

After applying changes, `_recreate_authentik_server_worker(reason='official-tunings-migration')` fires automatically (10-15s blip; `ldap` untouched).

### 2. Runtime config verifier (closes the audit loop)

**New function: `_authentik_verify_runtime_config(plog)`** in `app.py`. The lesson from the silent-ignore bug: never trust `.env` to mean Authentik is using those settings.

Two probes:

1. **`docker exec authentik-worker-1 ak dump_config`** — parses JSON, checks:
   - `cache.timeout_flows == 600`
   - `cache.timeout_policies == 600`
   - `log_level == "warning"`
2. **`docker top authentik-server-1`** — counts actual gunicorn worker processes (because `web.workers` is consumed by the launcher script, not visible in `dump_config`).

Persists pass/fail to `settings.authentik_runtime_config_check`. Runs on every console startup (after `_authentik_apply_official_tunings`). If any check fails, the operator gets a clear log line in journalctl.

### 3. Periodic Authentik server + worker auto-restart

**New function: `_authentik_periodic_restart_monitor()` daemon thread** (in `app.py`).

Started at module load alongside `_authentik_spiral_monitor`. Single-instance via PID-checked lockfile (`/tmp/takwerx-periodic-restart.lock`) so only one gunicorn worker runs the monitor.

**Loop cadence:** every 5 minutes (cheap — a clock check + a `settings.json` read).

**Fires the recreate when ALL are true:**
- `settings.authentik_periodic_restart.enabled != False` (default `true`)
- `~/authentik/docker-compose.yml` exists (Authentik installed)
- `datetime.now().hour == hour_local` (default `4` → 04:00 box-local time)
- Time since `last_run_utc` >= `min_interval_hours` (default `12`)

**Action when fired:**

```bash
cd ~/authentik && docker compose up -d --force-recreate --no-deps server worker
```

Note `--no-deps` — recreate ONLY `server` and `worker`. `ldap`, `postgresql`, `redis` (if present) stay up. The LDAP outpost's bind cache is preserved; no thundering herd on dependent TAK clients.

**Outcome persisted to `settings.authentik_periodic_restart`:**

```json
{
  "enabled": true,
  "hour_local": 4,
  "min_interval_hours": 12,
  "last_run_utc": "2026-05-01T11:00:00Z",
  "last_outcome": "ok",
  "last_duration_s": 9,
  "last_reason": "scheduled-24h"
}
```

**Mission-critical safety gate:** `_authentik_admin_api_recently_active(60)` — before firing, the periodic monitor scans the last 60s of `authentik-server-1` logs for any non-GET admin API request (`POST|PUT|PATCH|DELETE /api/v3/`). If found, the cycle is deferred and re-checked in 5 minutes. Means: if an operator is actively making users / editing providers when 04:00 hits, the restart waits until activity has been quiet for 60s. Worst case the 04:00 restart slips by 5-30 min on a genuinely busy night — acceptable. **The reactive ASGI loop trigger explicitly bypasses this gate** — if the server is in an ASGI loop, the box is already 502'ing every request, so deferring would only prolong the pain.

### 2. ASGI WebSocket reconnect loop reactive trigger

**New function: `_detect_authentik_asgi_websocket_loop()`**.

Cheap log scan: `docker logs authentik-server-1 --since 60s 2>&1 | grep -cE "Expected ASGI message|Unexpected ASGI message"`. Returns `(looping: bool, evidence: dict)` where `looping=True` when the count is `>= 5` in the last 60s.

**Hook:** runs as a third pass inside the existing 10-min `_authentik_spiral_monitor` (after the proactive routing migration, before the reactive spiral repair). When triggered, fires the same recreate as the periodic monitor, with `reason='asgi-loop-N-errors-60s'`.

**Why it matters:** during the Apr 30 investigation, one window of tak-10's logs showed the server stuck in an ASGI WebSocket reconnect loop with one or more outposts (`RuntimeError: Expected ASGI message 'websocket.send' or 'websocket.close', but got 'websocket.accept'` recurring every ~3 seconds). A full-stack `docker compose up -d` cleared it. The reactive trigger detects this signature and clears it automatically without waiting for the daily 04:00 restart.

### 3. Single source of truth for the recreate operation

**New function: `_recreate_authentik_server_worker(plog, reason)`**.

Both the periodic monitor and the ASGI reactive trigger call this function — same command, same logging, same outcome persistence, same rate limiting. The 12h `min_interval_hours` floor is shared between triggers — never recreate twice within 12h regardless of cause.

**Cardinal rule encoded in this function:** `--no-deps` is non-negotiable. The LDAP outpost is never touched. Removing this flag would cause thundering herd on every recreate; the comment in the code explicitly warns against it.

---

## What was explicitly NOT changed

- **No UI / button.** Operator was explicit: "I just want an update to work for now. No UI changes." Settings live in `settings.json` only; defaults are correct for every install.
- **No `AUTHENTIK_WEB_WORKERS` migration changes.** The Apr 30 evidence proved the env var is irrelevant on Authentik 2026.x — responder runs at `=4` fine, tak-10 ran at `=4` melting. The v0.8.2 logic stays as-is (harmless, but no longer the lever we thought it was).
- **No permanent autovacuum tuning ALTER TABLE.** Today's manual tuning was a red herring; the recreate is the cure.
- **No console UI dashboard for restart history.** Operator can `cat ~/.takwerx/settings.json | python3 -m json.tool | grep -A 7 authentik_periodic_restart`.

---

## Configuration (operator override)

Defaults are correct for every install. To customize, edit `~/.takwerx/settings.json`:

```json
{
  "authentik_periodic_restart": {
    "enabled": true,
    "hour_local": 4,
    "min_interval_hours": 12
  }
}
```

| Key | Default | Range | Notes |
|---|---|---|---|
| `enabled` | `true` | bool | Set `false` to disable scheduled recreates entirely. ASGI loop reactive trigger still fires (it's a different failure mode and shouldn't be off). |
| `hour_local` | `4` | 0-23 | Box-local hour to fire the daily restart. 04:00 local is quietest hour for nearly all TAK fleets. |
| `min_interval_hours` | `12` | int | Floor between recreates from any trigger (scheduled or reactive). 12h ensures at least 12h gap between back-to-back recreates. |

---

## Validation matrix

| Box | Status |
|---|---|
| tak-10 (Azure D8as_v5, heavy DataSync/Node-RED) | Drifted to p50 140%+ on v0.8.6. Manual recreate dropped it to p50 3.3%. v0.8.7 will automate this. |
| responder (Azure D8as_v5, medium-light) | Has not drifted on v0.8.6. v0.8.7 daily recreate is preventive insurance — runs once at 04:00, no client impact, keeps state fresh. |
| ssdnodes (medium streaming) | Stable on v0.8.6. v0.8.7 daily recreate provides the same preventive insurance. |
| Azure tak-test-3 | Confirmed clean v0.8.6 baseline. v0.8.7 inherits all v0.8.6 fixes; no regression risk. |

---

## Operator acceptance checklist

- [ ] Update Now to v0.8.7-alpha. Console restarts cleanly.
- [ ] `cat ~/.takwerx/settings.json | python3 -m json.tool | grep -A 7 authentik_periodic_restart` shows the new defaults (`enabled: true`, `hour_local: 4`, `min_interval_hours: 12`) within ~5 min of first 04:00 fire (or temporary `hour_local: <next_hour>` for instant test).
- [ ] After first scheduled fire: `last_outcome=ok`, `last_duration_s` < 15, `last_reason=scheduled-24h`. LDAP outpost `docker inspect authentik-ldap-1 --format '{{.State.StartedAt}}'` did NOT change (recreate touched only `server` + `worker`).
- [ ] Set `enabled: false` in `settings.json`; observe the next window-hour skip in journalctl: `journalctl -u takwerx-console --since "today" | grep "[periodic restart]"` shows the gate-reject log line and no recreate.
- [ ] No LDAP incidents during a 7-day soak across the fleet.

---

## Diagnostic commands

```bash
# Has the periodic restart fired recently?
cat ~/.takwerx/settings.json | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin).get('authentik_periodic_restart', {}), indent=2))"

# Is the periodic restart thread running?
ls -la /tmp/takwerx-periodic-restart.lock

# What does today's monitor log look like?
journalctl -u takwerx-console --since "today" | grep -E '\[periodic restart\]|\[spiral monitor\] ASGI'

# Force a test recreate manually (equivalent to what the daemon does):
cd ~/authentik && docker compose up -d --force-recreate --no-deps server worker

# Verify the recreate didn't touch ldap (StartedAt should NOT change):
docker inspect authentik-ldap-1 --format '{{.State.StartedAt}}'

# Check the ASGI loop detector evidence:
docker logs authentik-server-1 --since 60s 2>&1 | grep -cE "Expected ASGI message|Unexpected ASGI message"
# (>= 5 means the next spiral-monitor tick will fire a recreate)
```

---

## What's preserved from prior releases

- **`_authentik_spiral_monitor`** (v0.8.5) — the 10-min reactive routing repair monitor still runs unchanged; v0.8.7 adds a third pass to it, doesn't replace it.
- **Proactive FQDN routing migration** (v0.8.5) — unchanged.
- **Gunicorn worker timeout `--timeout=120`** (v0.8.5) — unchanged.
- **LDAP SA bind verifier** (v0.8.6) — unchanged.
- **`AUTHENTIK_WEB_WORKERS=4` migration** (v0.8.2) — unchanged. Apr 30 evidence proved it irrelevant on 2026.x; it stays only because it's harmless.
- **`idle_in_transaction_session_timeout=30s`** (v0.8.3) — unchanged.

---

## Known limitations

- **First-day baseline:** on a fresh upgrade to v0.8.7, the first scheduled restart will fire at the next 04:00 box-local. Boxes already in a drifted state (sustained > 100% server CPU) will not be auto-recovered until that first scheduled fire. Operators can run the manual recreate immediately to avoid waiting.
- **`min_interval_hours: 12` is a hard floor.** If both triggers want to fire within 12h (e.g. ASGI loop right after scheduled restart), the second is gated. This is intentional — back-to-back recreates within 12h indicate a deeper problem and should be investigated, not papered over.
- **No metric collection of CPU before/after.** This was deliberately cut to keep v0.8.7 small. Operators can do this manually with the diagnostic commands.
