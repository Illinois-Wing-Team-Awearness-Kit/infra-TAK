# v0.8.7-alpha — Work Plan

**Headline (and ONLY) feature: fix the v0.8.2 silent-ignore env var bug + apply official Authentik tunings.**

> **Scope discipline note:** Earlier drafts of this plan had two more pieces — a daily 04:00 auto-restart of `server`+`worker`, and a reactive recreate when an ASGI WebSocket reconnect loop was detected. Both were **deleted before ship.** They were band-aids built on the wrong theory (runtime state drift). Once we found the real cause (env var name typo), the band-aids became unnecessary and were removed.
>
> **No UI changes in v0.8.7.** Operator was explicit: "I just want an update to work for now. No UI changes." All settings live in `settings.json` and are operator-editable; defaults are correct for everyone.
>
> **Rollback feature → v0.8.8.** Operator stability comes first. See `docs/PLAN-v0.8.8.md`.

---

## 1. The Apr 30 2026 tak-10 investigation (root cause)

A multi-hour live debugging session on `tak-10` (Azure D8as_v5, 12 vCPU) produced a definitive root cause **only after consulting upstream Authentik docs**. Until that point we were chasing symptoms.

### The actual root cause

The v0.8.2 migration set:

```bash
AUTHENTIK_WEB_WORKERS=4   # WRONG NAME — single underscore
```

But Authentik 2026.x reads:

```bash
AUTHENTIK_WEB__WORKERS=4  # CORRECT — double underscore (per official docs)
```

Source: <https://docs.goauthentik.io/install-config/configuration/> — Authentik's config system uses double-underscore as the section/key separator. Single-underscore vars are **silently ignored**. Every box in the fleet has been running with `2` gunicorn workers (the default) instead of `4` since v0.8.2 shipped.

`docker top authentik-server-1 | grep -c 'gunicorn: worker'` confirms: 2 before fix, 4 after.

### Theories explored and ruled out

| Theory | Test | Verdict |
|---|---|---|
| Postgres dead-tuple bloat | Manual `VACUUM ANALYZE` on 10 hot tables | Helped briefly (159% → 2%), but climbed back within minutes. **Symptom of CPU starvation, not cause.** |
| Aggressive autovacuum tuning needed | `ALTER TABLE ... autovacuum_vacuum_scale_factor=0.0, threshold=5` | Settings applied. CPU stayed pinned at p50 ~97%. **Did not fix.** |
| `AUTHENTIK_WEB_WORKERS=4` undersized | Bumped to 12, threads to 8 | **Made it worse** (p50 155%, idle-in-trans 113). |
| Runtime state drift | Force-recreate `server` + `worker` only | CPU dropped, but climbed back. **Was actually clearing the wrong env vars and accidentally letting Authentik default to 2 workers, which on tak-10's load was *also* not enough — the recreate was a temporary reset, not a fix.** |
| **Silent-ignore env var bug** | Read upstream docs, switched to `AUTHENTIK_WEB__WORKERS` | **CPU dropped 99% → 2.1% durably under real load.** Verified via `docker top` (4 workers actually running) and `ak dump_config`. |

### Field evidence (after the real fix)

3-min soak on tak-10 with **351 real LDAP binds during the window**:

| Metric | v0.8.6 baseline | v0.8.7 (env var fix) |
|---|---|---|
| `authentik-server-1` CPU p50 | ~99% | **2.1%** |
| `authentik-server-1` CPU p95 | ~140% | 62.3% (transient flow execution bursts) |
| `authentik-server-1` CPU avg | ~150% | 9.9% |
| `authentik-postgresql-1` CPU p50 | ~94% | **0.0%** |
| `authentik-postgresql-1` CPU p95 | ~120% | 8.5% |
| Gunicorn workers (`docker top`) | 2 | **4** |
| LDAP impact | — | **zero** (outpost untouched) |

**~47x reduction at p50 with identical workload.** The recreates we'd been doing were "fixing" things only because they happened to clear the bad config and let Authentik default to 2 workers — which was sometimes enough on light boxes (responder) but never enough on heavy ones (tak-10).

---

## 2. Implementation (already built and validated in this release)

Three pieces in `app.py`:

### 2a. `_authentik_apply_official_tunings(plog)`

Idempotent migration that runs in two places:

- `_startup_migrations()` (every console boot)
- `_post_update_auto_deploy` (after Update Now)

It edits `~/authentik/.env` to:

1. **Remove** `AUTHENTIK_WEB_WORKERS=N` (the wrong name, ignored since v0.8.2).
2. **Add** `AUTHENTIK_WEB__WORKERS=4` (correct name; 2x default capacity).
3. **Add** `AUTHENTIK_CACHE__TIMEOUT_FLOWS=600` (2x default; flows rarely change → reduces DB pressure).
4. **Add** `AUTHENTIK_CACHE__TIMEOUT_POLICIES=600` (2x default; same rationale).
5. **Add** `AUTHENTIK_LOG_LEVEL=warning` (down from `info`; reduces log overhead and Postgres write pressure).

All values doc-grounded from <https://docs.goauthentik.io/install-config/configuration/>. Returns `True` if `.env` was modified (caller recreates the server). Records actions taken to `settings.authentik_official_tunings`.

### 2b. `_recreate_authentik_server_worker(plog, reason)`

Single source of truth for the recreate. Used **only** by callers that just changed env vars (gunicorn re-reads `.env` only at process startup, so editing `.env` alone is a no-op). Runs `docker compose up -d --force-recreate --no-deps server worker`. **Never touches `ldap`** (preserves bind cache, zero thundering-herd risk). Records outcome to `settings.authentik_last_recreate` for operator visibility.

### 2c. `_authentik_verify_runtime_config(plog)`

The verifier that closes the loop. Without this, the silent-ignore bug could come back the next time a tuning gets added with a wrong name and we'd never know. Two probes:

1. `docker exec authentik-worker-1 ak dump_config` — JSON dump of Authentik's actual loaded config. Asserts `cache.timeout_flows == 600`, `cache.timeout_policies == 600`, `log_level == "warning"`.
2. `docker top authentik-server-1` — counts processes whose CMD contains `gunicorn: worker`. Asserts count == 4.

Records pass/fail per check to `settings.authentik_runtime_config_check` for operator audit. Runs after every recreate AND on every console startup (closes the loop even when no migration was needed).

### What was explicitly NOT built (or built then deleted)

- ❌ Console UI / button (operator: "no UI changes").
- ❌ Daily 04:00 periodic restart of server+worker. *Built then deleted.* Was a band-aid built on the wrong "state drift" theory. Once the real fix is in, it's unnecessary noise.
- ❌ Reactive ASGI WebSocket loop detector + auto-recreate. *Built then deleted.* The ASGI loop we observed on tak-10 was a downstream symptom of worker starvation; with 4 workers + 600s cache it doesn't recur.
- ❌ Admin API safety gate (deferring scheduled restart when admins are mid-form). *Built then deleted.* Existed only because of the periodic restart; with no scheduled restart, no gate needed.
- ❌ Permanent autovacuum tuning ALTER TABLE (the manual tuning during investigation was a red herring; default autovacuum is fine once CPU isn't starved).

**Net deletion: ~200 lines of code that were treating symptoms.**

---

## 3. Acceptance criteria

All four passed on tak-10 Apr 30 2026:

- [x] After `git pull origin dev` + `systemctl restart takwerx-console`, `~/authentik/.env` contains `AUTHENTIK_WEB__WORKERS=4`, `AUTHENTIK_CACHE__TIMEOUT_FLOWS=600`, `AUTHENTIK_CACHE__TIMEOUT_POLICIES=600`, `AUTHENTIK_LOG_LEVEL=warning`. The legacy `AUTHENTIK_WEB_WORKERS` line is gone.
- [x] `docker top authentik-server-1 2>/dev/null | grep -c 'gunicorn: worker'` returns `4` (was 2 before).
- [x] `docker exec authentik-worker-1 ak dump_config` shows `cache.timeout_flows: 600`, `cache.timeout_policies: 600`, `log_level: warning`.
- [x] `settings.json` shows `authentik_official_tunings.last_outcome: "applied"` and `authentik_runtime_config_check.last_outcome: "pass"` (after the verifier-bug followup commit).
- [x] 3-min CPU soak with real bind workload (351 binds): `authentik-server-1` p50 ≤ 5%. (Actual: 2.1%.)

---

## 4. Smaller items considered but deferred

### 4a. Node-RED upstream-health-check (defer)

After v0.8.7 lands, Authentik should be quiet enough that this isn't urgent. Defer to a future release.

### 4b. TAK Server webadmin admin-role final verifier (defer)

Apr 30 2026: After "Resync LDAP webadmin", TAK WebUI redirected webadmin to WebTAK instead of Admin Console. Resolved by a second resync. Add a final verifier that confirms webadmin appears in `/opt/tak/UserAuthenticationFile.xml` with `role="ADMIN"` and auto-applies if missing. → v0.8.8 or later.

### 4c. Authentik deploy: wait for all containers healthy before API poll (defer)

Confirmed root cause of v0.8.6's Azure issue was the `elif` scope bug, not poll timing. Lower priority. → v0.8.9.

---

## 5. Out of scope for v0.8.7 (moved to v0.8.8 or later)

- **Rollback feature** (one-click revert from console). Originally planned for v0.8.8; **parked to v0.9.0 or later** — the v0.8.x line is reserved for Authentik stabilization work until the fleet is provably stable across slow disks. See `docs/PLAN-v0.8.8.md`.
- **Dashboard CPU per-core breakdown.** → v0.8.9 or later.
- **NSG ARM template advisory in start.sh.** → v0.8.9 or later.

---

## 6. Lessons captured as Cursor rule

`.cursor/rules/consult-upstream-docs.mdc` (alwaysApply) institutionalizes the lesson: **read official docs before chasing symptoms; never trust `.env` to mean the runtime is using it; always verify with the project's introspection command (`ak dump_config`, etc.).** This is what would have prevented the v0.8.2 silent-ignore bug from surviving five releases.

---

## 7. Notes from v0.8.6 post-release (kept for context)

- All four v0.8.6 fixes confirmed working on Azure tak-test-3 (D8as_v5, P10 64 GiB, ~145 MB/s).
- v0.8.5 production fleet stability on acute health metrics (zero SIGABRT, zero recursion, zero idle-in-trans) holds.
- Apr 30 2026: investigation of *chronic* CPU on tak-10 led to the v0.8.2 silent-ignore env var discovery. v0.8.7 closes that gap with a real fix, not state-hygiene cron.
