# v0.8.7-alpha — Work Plan

**Headline (and ONLY) feature: Authentik stability — periodic auto-restart + ASGI loop self-heal.**

Make Authentik *quiet* so the operator doesn't have to think about it. v0.8.5 fixed the acute crashes. v0.8.6 fixed the Azure deploy bugs. **v0.8.7 fixes the chronic CPU-pinning that makes the box feel broken even when nothing is technically wrong.**

> **Scope discipline note:** Earlier drafts had rollback as the v0.8.7 headline. **Rollback has been moved to v0.8.8** — operator stability comes first. Authentik must be silent, predictable, and self-healing before we add new operator features. v0.8.7 is Authentik. v0.8.8 is rollback. No mixing.
>
> **No UI changes in v0.8.7.** Operator was explicit: "I just want an update to work for now. No UI changes." Settings live in `settings.json` and are operator-editable; defaults are correct for everyone.

---

## 1. The Apr 30 2026 tak-10 investigation (root cause)

A multi-hour live debugging session on `tak-10` (Azure D8as_v5, 12 vCPU) produced a definitive root cause:

**Identical hardware + identical config + identical workload → wildly different CPU profiles between sibling boxes after several days of uptime.** The differentiator is **runtime state drift** in the Authentik server process — not capacity, not config, not workload.

### Theories explored and ruled out

| Theory | Test | Verdict |
|---|---|---|
| Postgres dead-tuple bloat | Manual `VACUUM ANALYZE` on 10 hot tables | Helped briefly (159% → 2%), but climbed back within minutes. **Symptom, not cause.** |
| Aggressive autovacuum tuning needed | `ALTER TABLE ... autovacuum_vacuum_scale_factor=0.0, threshold=5` | Settings applied. CPU stayed pinned at p50 ~97%. **Did not fix.** |
| `AUTHENTIK_WEB_WORKERS=4` undersized | Bumped to 12, threads to 8 | **Made it worse** (p50 155%, idle-in-trans 113). |
| `AUTHENTIK_WEB_WORKERS=4` *over*sized | Removed both env vars (let Authentik auto-default) | CPU dropped from 140% → 3.3%. *But:* responder runs **with** `=4` set and is fine at 1.9%. **Env var is irrelevant.** |
| ASGI WebSocket reconnect loop | Server logs showed `Expected ASGI message` every ~3s | Real failure mode. `docker compose up -d` (full stack) fixed it. **Genuine bug worth detecting.** |
| Runtime state drift | Force-recreate `server` + `worker` only | **CPU dropped 140% → 3.3% durably.** Matches responder pattern. |

### Field evidence

Tak-10 before any change:

| Metric | tak-10 | Responder (sibling) |
|---|---|---|
| server CPU p50 | 140%+ | 1.9% |
| postgres CPU p50 | 130%+ | 1.9% |
| idle-in-transaction | 100+ | 0 |
| bind volume | ~114/min | ~94/min |
| ASGI errors / 60s | 0 (steady state) | 0 |
| `AUTHENTIK_WEB_WORKERS` in `.env` | (not set, removed during test) | `=4` (set since v0.8.2) |
| `AUTHENTIK_WORKER_THREADS` | not set | not set |
| Hardware | Azure D8as_v5 | Azure D8as_v5 |

After `docker compose up -d --force-recreate --no-deps server worker` on tak-10:

| Metric | Before | After (3-min soak, 36 samples) |
|---|---|---|
| server CPU p50 | ~140% | **3.3%** |
| server CPU avg | ~150% | 13.9% |
| postgres CPU p50 | ~130% | **2.3%** |
| idle-in-transaction | 100+ | **0** (1 active, 15 idle) |
| ASGI errors during soak | n/a | 0 |
| LDAP impact | — | **zero** (outpost untouched, bind cache preserved) |

**Conclusion: only the recreate moved the needle durably. Periodic recreate is the cure.**

---

## 2. Implementation (already built in this release)

Three pieces in `app.py`:

### 2a. `_detect_authentik_asgi_websocket_loop()`

Cheap log scan (`docker logs authentik-server-1 --since 60s | grep -cE "Expected ASGI message|Unexpected ASGI message"`). Returns `(looping: bool, evidence: dict)` where `looping=True` when count >= 5 in last 60s.

### 2b. `_recreate_authentik_server_worker(plog, reason)`

Single source of truth for the recreate operation. Runs `cd ~/authentik && docker compose up -d --force-recreate --no-deps server worker`. **Never touches `ldap`** (preserves bind cache, zero thundering-herd risk). Records outcome to `settings.json` under `authentik_periodic_restart`:

```json
{
  "enabled": true,
  "hour_local": 4,
  "min_interval_hours": 12,
  "last_run_utc": "2026-04-30T11:00:00Z",
  "last_outcome": "ok",
  "last_duration_s": 9,
  "last_reason": "scheduled-24h"
}
```

### 2c. `_authentik_periodic_restart_monitor()` daemon thread

- Started at module load alongside `_authentik_spiral_monitor` (line ~30647 in `app.py`).
- Single-instance via PID-checked lockfile (`/tmp/takwerx-periodic-restart.lock`) — same pattern as the spiral monitor.
- Loops every **5 minutes** (cheap: a clock + a `settings.json` read).
- Fires the recreate when **all** are true:
  - `settings.authentik_periodic_restart.enabled != False` (default true)
  - `~/authentik/docker-compose.yml` exists
  - `datetime.now().hour == hour_local` (default 4 — 04:00 box-local time)
  - Time since `last_run_utc` >= `min_interval_hours` (default 12)
  - **Mission-critical safety gate:** `_authentik_admin_api_recently_active(60)` returns False (no `POST/PUT/PATCH/DELETE /api/v3/` in last 60s of server logs). If active, deferred and re-checked in 5 min.
- Idempotent: safe to call at module load on every gunicorn worker startup.

### 2c-bis. `_authentik_admin_api_recently_active(seconds=60)`

The safety gate. Cheap log scan: `docker logs authentik-server-1 --since 60s | grep -E "\"(POST|PUT|PATCH|DELETE) /api/v3/"`. Returns True on any match. Fail-opens (returns False) on error so a glitched `docker logs` can never permanently block the periodic restart. **Used only by the scheduled trigger** — the ASGI loop reactive trigger bypasses this gate intentionally because if the server is in an ASGI loop the box is already 502'ing every request.

### 2d. ASGI loop reactive trigger (in existing `_authentik_spiral_monitor`)

The 10-min spiral monitor already runs. It now adds a third pass *before* the existing reactive routing repair:

1. Proactive routing check (existing, v0.8.5).
2. **NEW: ASGI WebSocket loop check.** If `_detect_authentik_asgi_websocket_loop()` returns true and the 12h `min_interval_hours` floor allows, fire `_recreate_authentik_server_worker(reason='asgi-loop-N-errors-60s')` immediately.
3. Reactive spiral signature check (existing, v0.8.5, rate-limited per its own 6h cadence).

The 12h floor is shared between scheduled and reactive triggers — never recreate twice within 12h regardless of cause.

### Defaults justified

- `hour_local: 4` — 04:00 local is the lowest-activity hour for nearly all TAK fleets (US/EU TOC ops are off, dawn ATAK clients haven't booted yet).
- `min_interval_hours: 12` — Once per day is enough state hygiene for tak-10-class boxes (their drift takes 5-12h to develop). Reactive ASGI trigger can also fire, but only after the 12h gap from the last recreate.
- `enabled: true` — Every box benefits. Operators on small/light boxes can opt out by editing `settings.json`.

### What was explicitly NOT built

- ❌ Console UI / button (operator: "no UI changes")
- ❌ Per-restart CPU before/after measurement (extra complexity for marginal value; logs are enough)
- ❌ `AUTHENTIK_WEB_WORKERS` migration changes (today proved env var is irrelevant; v0.8.2 logic stays)
- ❌ Permanent autovacuum tuning ALTER TABLE (today's manual tuning was a red herring)
- ❌ Dashboard CPU per-core breakdown (out of scope; deferred)

---

## 3. Acceptance criteria

- [ ] On tak-10 (with current p50 ~3% baseline), set `hour_local` to "next hour" temporarily and confirm the periodic restart fires within ~5 min, completes in < 15s, leaves the LDAP outpost untouched, and writes `last_run_utc` + `last_outcome=ok` to `settings.json`.
- [ ] Inject a fake ASGI loop (`for i in {1..10}; do logger -t authentik-server-1 "Expected ASGI message test"; done` — or just hit the real one if it recurs) and confirm the spiral monitor catches it within 10 min and triggers a recreate with `reason=asgi-loop-N-errors-60s`.
- [ ] Set `enabled: false` in `settings.json` and confirm the periodic monitor logs the skip and does not fire.
- [ ] Confirm the lockfile prevents double-firing across multiple gunicorn workers (only one log line per cycle from `[periodic restart]`).
- [ ] 7-day soak on tak-10 + responder: CPU p50 stays under 10% on both, no LDAP incidents, periodic restart fires once per day at 04:00 local.

---

## 4. Smaller items considered but deferred

These are scoped tightly. They can ride along only if they don't slow down v0.8.7 ship; otherwise → v0.8.8 or later.

### 4a. Node-RED upstream-health-check (defer)

After `_authentik_periodic_restart_monitor` is in place, this should naturally improve — Authentik server CPU won't pin long enough for proxy sessions to go cold. Defer to a future release.

### 4b. TAK Server webadmin admin-role final verifier (defer)

Apr 30 2026: After "Resync LDAP webadmin", TAK WebUI redirected webadmin to WebTAK instead of Admin Console. Resolved by a second resync. Add a final verifier that confirms webadmin appears in `/opt/tak/UserAuthenticationFile.xml` with `role="ADMIN"` and auto-applies if missing. Small (~30 lines), safe to include — but only if it doesn't bloat the v0.8.7 diff. Otherwise → v0.8.8.

### 4c. Authentik deploy: wait for all containers healthy before API poll (defer)

Confirmed root cause of v0.8.6's Azure issue was the `elif` scope bug, not poll timing. Lower priority. → v0.8.9.

---

## 5. Out of scope for v0.8.7 (moved to v0.8.8 or later)

- **Rollback feature** (one-click revert from console). → **v0.8.8 headline.** See `docs/PLAN-v0.8.8.md`.
- **Dashboard CPU per-core breakdown.** → v0.8.9 or later.
- **Speed test: read MB/s display.** → v0.8.9 or later.
- **NSG ARM template advisory in start.sh.** → v0.8.9 or later.

---

## 6. Why ship v0.8.7 fast as Authentik-stability-only

1. **The chronic pain is the one driving the operator nuts.** v0.8.5 fixed the acute crashes, but tak-10 still went into "expensive and sluggish" mode after a few days. That's the felt-every-day problem. Fix it first.
2. **The mechanism is field-validated.** Apr 30 2026 on tak-10: a single force-recreate moved the needle from p50 140% → 3.3%, durably, with zero LDAP impact. Automating it is ~200 lines of code, no new dependencies, no UI surface.
3. **Rollback can wait one release.** If v0.8.7 has a rough edge, the worst case is the operator runs the existing manual `docker compose up -d --force-recreate --no-deps server worker` command from SSH. They already know how. Rollback would be nice-to-have, not blocking.

---

## 7. Notes from v0.8.6 post-release (kept for context)

- All four v0.8.6 fixes confirmed working on Azure tak-test-3 (D8as_v5, P10 64 GiB, ~145 MB/s).
- v0.8.5 production fleet (tak-10, ssdnodes, responder) is stable on acute health metrics (zero SIGABRT, zero recursion, zero idle-in-trans).
- Apr 30 2026: discovered the v0.8.5 fix protects against the *acute* failure but not the *chronic* state-drift symptom on heavy-load boxes. v0.8.7 closes that gap.
