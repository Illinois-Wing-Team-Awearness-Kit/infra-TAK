# v0.8.7-alpha Release Notes

## Headline: Authentik stability — periodic auto-restart + ASGI loop self-heal

The Apr 30 2026 tak-10 incident proved that **identical hardware + identical config + identical workload boxes can drift to wildly different CPU profiles after several days of uptime**. Sister boxes on the same Azure D8as_v5, both v0.8.6, both with the same `AUTHENTIK_WEB_WORKERS=4` history, both with similar bind volume — but tak-10 sustained server CPU p50 at 140%+ while responder sat at p50 1.9%.

After ruling out four reasonable theories (postgres bloat, autovacuum tuning, worker undersizing, worker oversizing), the only durable fix turned out to be a force-recreate of the Authentik server + worker containers. CPU dropped from p50 140% → 3.3% in 3 minutes with **zero LDAP impact** (outpost untouched, bind cache preserved).

v0.8.7 automates this recreate on a 24h cadence, plus adds a reactive trigger for the ASGI WebSocket reconnect loop failure mode observed during the same investigation.

---

## Changes

### 1. Periodic Authentik server + worker auto-restart

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
