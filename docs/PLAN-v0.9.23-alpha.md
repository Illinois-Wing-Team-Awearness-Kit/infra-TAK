# Plan — v0.9.23-alpha

> **Status:** IN PROGRESS — created 2026-05-14 evening after live tak-10 LDAP SA bind drift diagnosis. Expanded 2026-05-15 morning to also cover the upstream Authentik 2026.2.x PG connection leak surfaced by Tom Andersen's anchortak forensic capture (see Item 4 below).
> **Target:** v0.9.23-alpha.
> **Theme:** Promote intermittent post-update / boot-only self-heal logic into **continuous** self-heal in `_startup_migrations` + periodic watchdog daemons. Same architectural pattern as v0.9.21 Theme C ("wiring-gap defense + drift self-healing"), extended to the three drift classes that bit operators between v0.9.20 and v0.9.22. Item 4 additionally mitigates the upstream Authentik #20714 PG connection leak by re-enabling gunicorn worker recycling — root-caused by Tom Andersen's anchortak forensic suite (vendored at `ops/diagnostics/anchortak/`, analysis pinned at `docs/UPSTREAM-AUTHENTIK-PG-LEAK-20714.md`).
>
> **Sources of work:**
> - Live diagnosis on tak-10 (2026-05-14, 22:00 PT) traced 8446 webadmin → WebTAK redirect + ATAK channel update outage to a single underlying cause: **LDAP service account (`adm_ldapservice`) bind drift** masked by `bind_mode: cached` (the exact failure mode `docs/HANDOFF-LDAP-AUTHENTIK.md` line 254 warns about).
> - Operator-reported recurrence of `webadmin` admin-role drift after Update Now: 8446 lands in WebTAK because `webadmin` admin-role linkage to `tak_ROLE_ADMIN` / `authentik Admins` / `adminGroup="ROLE_ADMIN"` is verified post-update but **not continuously**.
> - Operator-reported recurrence of TAK Portal admin-account guardrail drift (`USERS_HIDDEN_PREFIXES` / `USERS_ACTIONS_HIDDEN_PREFIXES` for `akadmin` / `webadmin`): `_auto_harden_takportal_settings` is currently called in post-update only, not in the always-run `_startup_migrations` block.

---

## TL;DR

Three items, one architectural fix: **continuous self-heal instead of one-shot heal**.

- **Item 1 — LDAP SA bind periodic watchdog.** `_startup_migrations` already detects + heals SA bind drift (verified live on tak-10: `Startup migration: LDAP service account bind FAILED — auto-resyncing` → `resync OK` → `LDAP healed — restarting takserver`). But it only runs at console startup. Drift recurs ~hourly mid-session and stays broken until operator intervention. **Promote the existing one-shot check into a 5-minute daemon loop** (pattern from `_authentik_channels_pool_watchdog_loop`, v0.9.21).
- **Item 2 — `webadmin` admin-role drift guardrail.** Add explicit invariant checks to both `_startup_migrations` and a 5-minute watchdog: `webadmin ∈ tak_ROLE_ADMIN`, `webadmin ∈ authentik Admins`, `adminGroup="ROLE_ADMIN"` in `CoreConfig.xml`. On any miss, auto-repair via existing `_ensure_authentik_webadmin` + `_apply_ldap_to_coreconfig`. Surface failure in console "LDAP ready" state instead of false-green.
- **Item 3 — TAK Portal admin hardening promotion.** Move `_auto_harden_takportal_settings` call site from post-update hook into `_startup_migrations` so `USERS_HIDDEN_PREFIXES` / `USERS_ACTIONS_HIDDEN_PREFIXES` for `akadmin` / `webadmin` get re-asserted on every console boot — closing the drift window between updates.

Goal: zero operator interventions for any of these three drift classes over a 48-hour soak on tak-10.

---

## Field evidence (tak-10, 2026-05-14 22:00 PT)

Forensic capture during active outage. **All three signals agreed on the SA password value, but Authentik's stored hash had drifted away from it.** Classic `bind_mode: cached` masking pattern from `HANDOFF-LDAP-AUTHENTIK.md`:

```
Container env (authentik-server-1 printenv): GRebGBeuKxcraFLkel3awHGqLLewMM1A   (matches .env)
~/authentik/.env (mtime 2026-05-15 00:42:53 UTC):                ...qLLewMM1A
/opt/tak/CoreConfig.xml (serviceAccountCredential):              ...qLLewMM1A

Fresh ldapsearch with that exact password:                       ldap_bind: Invalid credentials (49)
Outpost log (concurrent):                                        adm_ldapservice "authenticated from session" × hundreds/min
```

Authentik DB `password_change_date`:

```
2026-05-15T02:07:49.244035Z  ← last write
```

journalctl confirms that write came from **our own startup migration**:

```
May 15 02:07:33  Startup migration: LDAP service account bind FAILED — auto-resyncing
May 15 02:08:08  Startup migration: LDAP service account resync OK (LDAP bind verified (attempt 1))
May 15 02:08:08  Startup migration: LDAP healed — restarting takserver to flush cached state
```

**The self-heal works correctly.** It detected the drift, repaired it, verified with `ldapsearch`, and restarted takserver. Three hours later, drift recurred. No further self-heal fired because the migration only runs at console startup.

This is exactly the warning from `HANDOFF-LDAP-AUTHENTIK.md` line 247:

> Reactive detection is necessary but not sufficient — proactive precondition migrations come first. Some bugs are silently latent (responder's misroute hidden by the cached SA session) and never produce a reactive signal until the operator's first fresh bind, which is also the first failed bind they see.

And line 254:

> `bind_mode: cached` hides issues. Healthy boxes can sit on a stale cached SA bind for hours while every fresh bind fails.

The fix is the same pattern v0.9.21 used for `channels_postgres` connection leak: **a periodic daemon that re-checks and re-heals**.

---

## Items in scope

### Item 1 — LDAP SA bind periodic watchdog

**Closes:** 8446 webadmin → WebTAK and ATAK channel-update outages caused by SA bind drift, as observed on tak-10 (2026-05-14) and across multiple operator-reported drift events since v0.9.20.

**Why this is the right fix:**
- The detection + repair logic already exists and is proven working (live tak-10 journalctl shows successful auto-heal at 02:07:33–02:08:08).
- The only gap is **runner**: one-shot at boot vs continuous.
- Precedent: `_authentik_channels_pool_watchdog_loop` (v0.9.21) demonstrates the daemon-thread pattern is safe and idempotent.
- `bind_mode: cached` is correct architecture (see `HANDOFF-LDAP-AUTHENTIK.md` line 692 — `bind_mode: direct` would crush Authentik). We are NOT changing the cache mode. We are bounding the recovery time when the underlying DB hash drifts out from under the cache.

**Implementation plan:**

1. **Extract the heal into a standalone helper.**
   - File: `app.py`.
   - Pull the existing "LDAP service account bind FAILED — auto-resyncing" block out of `_startup_migrations` into a new function:
     ```python
     def _authentik_ldap_sa_bind_check_and_heal(plog=None) -> tuple[bool, str]:
         """Verify adm_ldapservice can bind fresh. If not, resync from .env.
         Returns (healed_or_already_ok, message). Idempotent."""
     ```
   - Body: read SA password from `.env`, call `_test_ldap_bind_dn_verdict('cn=adm_ldapservice,ou=users,dc=takldap', ldap_pass)`. On `'ok'` verdict, return `(True, 'already-ok')`. On `'fail'` verdict, call `_ensure_authentik_ldap_service_account()` (which already exists and does set_password + force-recreate ldap + verify).
   - `_startup_migrations` calls the new helper (preserves the boot-time auto-heal that we just confirmed works).

2. **Add the periodic daemon loop.**
   - Pattern: copy verbatim structure from `_authentik_channels_pool_watchdog_loop` in `app.py` (v0.9.21 precedent, already battle-tested).
   - New function:
     ```python
     def _authentik_ldap_sa_bind_watchdog_loop():
         """v0.9.23: Background daemon — every 300s, verify SA can bind fresh
         and auto-heal on drift. Bounds drift window to one watchdog interval."""
     ```
   - Interval: **300 seconds** (5 min). Rationale: long enough to avoid hammering Authentik's auth flow under steady-state, short enough that worst-case channel-update outage is bounded at 5 min instead of "until operator notices."
   - On `verdict == 'fail'`: log `LDAP SA bind watchdog: drift detected — auto-resyncing` (visible in `journalctl -u takwerx-console -f | grep "LDAP SA"`).
   - On `verdict == 'ok'`: log once every 10 iterations (~50 min) `LDAP SA bind watchdog: ok` for liveness without spam.
   - On heal success: log `LDAP SA bind watchdog: healed (took N attempts)`, plus record `authentik_ldap_sa_last_repair` timestamp + `authentik_ldap_sa_repair_count` (cumulative) in `settings.json` so dashboard can surface drift frequency.
   - On heal failure: log `LDAP SA bind watchdog: heal FAILED — {error}` at WARNING level so it shows up in normal log review.
   - Catch and log all exceptions inside the loop — the watchdog must never die from a transient error.

3. **Launch the daemon thread.**
   - Add to `if __name__ == '__main__':` block alongside existing watchdogs (`_authentik_channels_pool_watchdog_loop`, etc.).
   - `threading.Thread(target=_authentik_ldap_sa_bind_watchdog_loop, daemon=True).start()`
   - Add a 30-second delay before the first check so console finishes startup migrations first (no contention with the boot-time heal).

4. **Dashboard surface.**
   - Add to the Authentik card on the console dashboard:
     - Last drift timestamp (or "no drift detected" green).
     - Cumulative repair count.
   - Read from `settings.json` keys recorded by the watchdog.

5. **Diagnostics enrichment (open question — Item 1b).**
   - DB `password_change_date` on tak-10 did NOT update between the 02:07:49 boot-time heal and the operator's discovery of recurrence ~3h later. **Yet binds with the same password value started failing again.** This means either:
     - (a) The hash itself is not being mutated, but something else changes (user `is_active=False`, lockout flag, group membership change that breaks the flow, reputation policy decision drifting).
     - (b) Authentik's password verification path is hitting a transient backend failure that surfaces as `49`.
   - Capture pre-heal state every time the watchdog fires: `is_active`, `password_change_date`, `failed_logins`, group memberships for `adm_ldapservice`, last `password_change_date` delta. Log to a dedicated file: `~/.config/infra-tak/ldap-sa-watchdog.log`.
   - After fleet collects ~1 week of samples, identify the underlying mutator. **Do not block release on this** — Item 1 (the watchdog) provides operator-acceptable recovery regardless of which underlying field is drifting.

---

### Item 2 — `webadmin` admin-role drift guardrail

**Closes:** Operator-reported recurrence of "8446 lands in WebTAK instead of admin UI" after Update Now (originally captured in `WORKFLOW-8446-WEBADMIN.md`; user re-flagged on 2026-05-14 with "i went to log back in and it drifted again had to resync like that was only an hour ago").

**Why this is the right fix:**
- Current `connect-ldap` readiness check validates: CoreConfig has LDAP, `webadmin` exists, `webadmin` `is_superuser`, bind succeeds. It does **NOT** explicitly assert `webadmin ∈ tak_ROLE_ADMIN`.
- So drift can produce a "ready / green" state where bind works but TAK Server's admin-role mapping is empty → bouncing to WebTAK.
- Same architectural fix as Item 1: continuous re-assertion instead of one-shot.

**Implementation plan:**

1. **Tighten the readiness check.**
   - File: `app.py`, `_get_authentik_webadmin_status()` and related helpers.
   - Add to the existing "ready" gate: `webadmin ∈ tak_ROLE_ADMIN` (Authentik group), `webadmin ∈ authentik Admins` (Authentik group), `adminGroup="ROLE_ADMIN"` present in `/opt/tak/CoreConfig.xml`.
   - If any miss → "ready" returns false with explicit reason.

2. **New self-healing helper.**
   - Function: `_authentik_webadmin_admin_role_check_and_heal(plog=None) -> tuple[bool, str]`.
   - Body:
     - If `webadmin` not in `tak_ROLE_ADMIN` → call `_ensure_authentik_webadmin()` (which already does the group-add).
     - If `webadmin` not in `authentik Admins` → call `_ensure_authentik_webadmin()` (same function handles both groups).
     - If `adminGroup="ROLE_ADMIN"` missing from CoreConfig → call `_apply_ldap_to_coreconfig()` (which already verifies and restores this attribute).
   - Returns `(True, 'already-ok' | 'healed: <what>')` or `(False, error)`.

3. **Wire into `_startup_migrations`.**
   - Add a new line to the unconditional startup migrations block:
     ```python
     plog(f"Startup migration: webadmin admin-role guardrail: {msg}")
     ```
   - Runs every console boot. Catches drift accumulated between updates.

4. **Wire into the same watchdog daemon from Item 1.**
   - Same daemon loop (`_authentik_ldap_sa_bind_watchdog_loop`) also calls `_authentik_webadmin_admin_role_check_and_heal()` on each tick.
   - One thread, two checks. Reduces overhead and keeps related logic colocated.
   - Same `settings.json` recording pattern: `authentik_webadmin_role_last_repair`, `_repair_count`.

5. **Dashboard surface.**
   - Add to Authentik card: "webadmin admin role: ok / drifted (last heal at HH:MM)".

---

### Item 3 — TAK Portal admin hardening promotion

**Closes:** Drift of `USERS_HIDDEN_PREFIXES` / `USERS_ACTIONS_HIDDEN_PREFIXES` (the `akadmin` / `webadmin` lock/hide settings in TAK Portal). Currently `_auto_harden_takportal_settings()` is only called from the post-update hook (see `app.py` line 41545), not from `_startup_migrations`. Between updates, drift accumulates with no re-assertion.

**Why this is the right fix:**
- Identical to the v0.9.21 Theme C pattern: move idempotent helpers from version-gated post-update path into unconditional `_startup_migrations`.
- The function is already idempotent. The only change is **where** it is called from.

**Implementation plan:**

1. **Add `_auto_harden_takportal_settings()` call to `_startup_migrations`.**
   - File: `app.py`.
   - Add line after existing TAK Portal–related migrations:
     ```python
     try:
         _auto_harden_takportal_settings()
         plog("Startup migration: takportal admin hardening re-asserted (idempotent)")
     except Exception as e:
         plog(f"Startup migration: takportal admin hardening failed: {e}")
     ```

2. **Leave the post-update call site in place.**
   - Don't remove the existing post-update call at line 41545 — defense in depth. Running twice on update is harmless (idempotent function).

3. **Test that hardening survives:**
   - Manual edit of `USERS_HIDDEN_PREFIXES` to `[]` in TAK Portal `settings.json`.
   - Console restart.
   - Verify `USERS_HIDDEN_PREFIXES` is restored to `['akadmin', 'webadmin']` (or whatever the current expected set is — match the function's body).

---

## Planned files touched

- `app.py`
  - New: `_authentik_ldap_sa_bind_check_and_heal()` (extracted from `_startup_migrations`).
  - New: `_authentik_webadmin_admin_role_check_and_heal()`.
  - New: `_authentik_ldap_sa_bind_watchdog_loop()` (handles both Item 1 and Item 2 checks).
  - Modified: `_startup_migrations` calls the two new helpers, plus existing `_auto_harden_takportal_settings()`.
  - Modified: `_get_authentik_webadmin_status()` (or wherever "ready" gate lives) — tighten check.
  - Modified: `if __name__ == '__main__':` block — launch new daemon thread.
  - New `settings.json` keys: `authentik_ldap_sa_last_repair`, `authentik_ldap_sa_repair_count`, `authentik_webadmin_role_last_repair`, `authentik_webadmin_role_repair_count`.
- `docs/RELEASE-v0.9.23-alpha.md` (new, planned).
- `docs/HANDOFF-LDAP-AUTHENTIK.md` (append v0.9.23 entry to the running log; document the watchdog + the open Item 1b underlying-mutator question).
- Console dashboard (Authentik card): two new status lines, one new repair-count metric each.

---

## Acceptance criteria

### Item 1 — LDAP SA bind watchdog

- [ ] On a fresh tak-10 install: within 5 min of console start, `journalctl -u takwerx-console -f | grep "LDAP SA"` shows `LDAP SA bind watchdog: ok`.
- [ ] Inject drift (manually change `adm_ldapservice` password via Authentik UI to a known-bad value): watchdog detects within 5 min, heals, fresh `ldapsearch` succeeds again, log shows `drift detected → resyncing → healed`.
- [ ] Restart `authentik-server-1` while watchdog running: watchdog continues, heals any drift that surfaces. Does not panic on transient API unavailability during restart window.
- [ ] No false positives — `ok` state stays `ok` over a 24-hour soak with no manual changes. Repair count does not increment.
- [ ] tak-10 24-hour soak: zero operator interventions for 8446 webadmin or ATAK channel updates. Watchdog log shows zero or auto-recovered drift events only.

### Item 2 — webadmin admin-role guardrail

- [ ] Readiness gate hard-fails when `webadmin` is removed from `tak_ROLE_ADMIN` in Authentik.
- [ ] Readiness gate hard-fails when `adminGroup="ROLE_ADMIN"` is missing from CoreConfig.
- [ ] Console boot heals all three invariants (Authentik `tak_ROLE_ADMIN`, Authentik `authentik Admins`, CoreConfig `adminGroup`) if drifted.
- [ ] Periodic watchdog heals all three within 5 min when drift is injected mid-session.

### Item 3 — TAK Portal admin hardening promotion

- [ ] Restart console after manually clearing `USERS_HIDDEN_PREFIXES` in TAK Portal `settings.json`: setting is restored within startup migrations.
- [ ] Log line `Startup migration: takportal admin hardening re-asserted (idempotent)` present in every console boot's journalctl.

---

## Test plan

1. **Unit / dev-box:**
   - Run console locally with watchdog interval shortened to 30s for fast iteration.
   - Force `_test_ldap_bind_dn_verdict` to return `'fail'` via test hook → verify heal is invoked and `settings.json` records the timestamp.
   - Manually clear `tak_ROLE_ADMIN` membership in a test Authentik → verify `_authentik_webadmin_admin_role_check_and_heal` restores it.

2. **tak-10 dev-build soak (primary acceptance):**
   - Pull v0.9.23 onto tak-10 via Update Now.
   - Tail watchdog: `sudo journalctl -u takwerx-console -f | grep -E "LDAP SA|webadmin admin-role|takportal admin"`.
   - Run 24h with no manual intervention.
   - Verify: zero 8446 → WebTAK redirects, zero ATAK channel-update outages, watchdog log shows drift detected → healed cycles bounded under 5 min each.
   - Re-test ldapsearch hourly: `result: 0 Success` every time.

3. **Drift injection (controlled):**
   - Change SA password via Authentik UI → expect heal within 5 min.
   - Remove `webadmin` from `tak_ROLE_ADMIN` group via Authentik UI → expect heal within 5 min.
   - Clear `USERS_HIDDEN_PREFIXES` in TAK Portal `settings.json`, restart console → expect restore on boot.

4. **Negative test:**
   - Shut down `authentik-server-1` for 60s while watchdog is running. Verify watchdog logs API errors gracefully and resumes when server comes back. No daemon thread death.

---

## Item 4 — Upstream Authentik PG connection leak mitigation (added 2026-05-15)

**Closes:** `ak-pg-watchdog` firing every 5-10 min on tak-10 and AnchorTAK fleet boxes, dropping the LDAP outpost websocket each time and breaking TAK CoT channel state for connected operators in the field. Underlying issue is upstream Authentik bug [goauthentik/authentik#20714](https://github.com/goauthentik/authentik/issues/20714) — confirmed (`label bug/confirmed`, assigned `rissson`), still open at time of writing.

**Root-cause analysis (Tom Andersen, AnchorTAK ops, 2026-05-15):**
- 91-minute × 1056-sample anchortak forensic capture on anctakserver2 (Authentik 2026.2.3 + infra-TAK v0.9.22).
- 100% of the leak is in `authentik-server-1`. `authentik-worker-1` is stable at 7±2 idle connections.
- Dominant query class: `enterprise/license` cache lookup, averaging 61.3 idle conns (peak 146) — 64% of total idle at any moment.
- 100% of leaked connections have a blank `application_name` (i.e. they're from `django_postgres_cache` using raw psycopg connections, NOT the Django ORM pool).
- 79.6% of leaked connections aged >60s, 37.8% aged >5min — proves CONN_MAX_AGE=10 (our v0.9.21 setting) is NOT reaching this code path. The cache backend's pool has its own lifecycle and Django's `close_old_connections()` request-end signal doesn't touch it.
- Full writeup pinned at `docs/UPSTREAM-AUTHENTIK-PG-LEAK-20714.md`.

**Why our existing fixes weren't enough:**
- `CONN_MAX_AGE=10` only bounds the ORM pool — leak is in the cache pool.
- `idle_session_timeout=300s` (re-added in v0.9.23 phase 3) reaps from the PG side at a 5-min ceiling, but Tom's measured leak rate (0.17 conn/sec aggregate) × 300s = 51 conns just from new accumulations during the reap window. Steady-state still climbs above the watchdog threshold on busy boxes.
- `ak-pg-watchdog` catches at idle≥150 and full-restarts `authentik-server-1`, but that drops the LDAP outpost websocket for 10-30s every cycle. Operators see this as "channels dropping" every 5-10 min.

**The fix — `AUTHENTIK_WEB__MAX_REQUESTS=1000` + `MAX_REQUESTS_JITTER=50`:**
- Gunicorn's built-in worker recycling: a worker that has processed `MAX_REQUESTS` (±jitter) gracefully shuts down and is replaced. On shutdown, ALL of that worker's file descriptors close — including the cache pool's leaked PG connections.
- Per-worker, not whole-container. One worker recycles at a time. LDAP outpost websocket only drops if it happens to be attached to that specific worker at recycle time (≈1/N of cycles).
- Result on a typical box: LDAP drop frequency reduced from every 5-10 min (whole-container restart by watchdog) to every 15-30 min (single-worker graceful recycle), and outage per drop shrinks from 10-30s to 1-5s.
- Upstream-supported pattern: documented at https://docs.goauthentik.io/install-config/configuration/#authentik_web__max_requests

**Background on why we had it OFF:**
- v0.8.7 explicitly set `MAX_REQUESTS=0` and `MAX_REQUESTS_JITTER=0` with the comment "disable gunicorn worker recycling — eliminates periodic LDAP websocket drops." That decision was correct at the time: no leak, no watchdog, so worker recycling was pure cost (rare LDAP drops) with no benefit.
- Authentik 2026.2.x changed the equation. With the leak now present, watchdog restarts cause MORE frequent and LONGER LDAP drops than worker recycling would. Flipping `MAX_REQUESTS` back ON is strictly better.

**Implementation (landed in this commit):**

1. **`_authentik_apply_official_tunings` target_settings updated.**
   - New installs get `AUTHENTIK_WEB__MAX_REQUESTS=1000` and `MAX_REQUESTS_JITTER=50` by default.
   - Detailed inline comment in `app.py` references this plan and Tom's analysis.

2. **New migration `_patch_authentik_web_max_requests_to_1000(plog)`.**
   - Existing installs deployed under v0.8.7+ with `=0` get migrated to `=1000` and `=50`.
   - Only touches the legacy `=0` value — operator-set values (e.g. 500, 2000) are preserved.
   - Backs up `.env` before write, triggers `_recreate_authentik_server_worker` to apply.
   - Records outcome to `settings.authentik_max_requests_to_1000_migration`.

3. **Wired into `_startup_migrations`** alongside the existing `_patch_authentik_conn_max_age_60_to_10` call.

4. **`ALTER SYSTEM RESET ALL` bug fix.**
   - Bug introduced in v0.9.23 phase 3: combining `ALTER SYSTEM RESET ALL` and `SELECT pg_reload_conf()` in a single `psql -c` flag wraps them in an implicit transaction, which `ALTER SYSTEM` refuses. Split into two `-c` flags so each runs in autocommit.

5. **Watchdog docstring + alert message demoted to safety-net status.**
   - `_authentik_channels_pool_watchdog_loop` docstring rewritten to say MAX_REQUESTS is the primary mitigation and the watchdog is defense-in-depth.
   - Alert message now points operators to check `~/authentik/.env` for `MAX_REQUESTS=0` and re-run the v0.9.23 migration if they're seeing it fire often.

6. **`_patch_authentik_conn_max_age_60_to_10` docstring updated** with the historical note that CONN_MAX_AGE doesn't actually reach the leaking cache pool, but the migration is retained for ORM-pool hygiene.

7. **Tom's analysis pinned** as `docs/UPSTREAM-AUTHENTIK-PG-LEAK-20714.md`. Diagnostic scripts vendored at `ops/diagnostics/anchortak/` with README.

**Acceptance:**

- [ ] On tak-10 dev-build install: `grep MAX_REQUESTS ~/authentik/.env` shows `1000` and `50` after Update Now.
- [ ] `journalctl -u takwerx-console | grep "max_requests:"` shows the migration ran exactly once and recreated the server container.
- [ ] `pg_stat_activity` idle count from `authentik-server-1` stays under 150 for ≥24 hour soak.
- [ ] `ak-pg-watchdog` alerts disappear from `journalctl -u takwerx-console -f`.
- [ ] ATAK / iTAK clients stop reporting channel drops during the soak.
- [ ] `ALTER SYSTEM RESET ALL` no longer errors with "cannot run inside a transaction block" in startup logs.

---

## Item 6 — PgBouncer architectural fix (added 2026-05-15, afternoon)

**Closes:** the residual failure mode that Phases 4 and 5 could not fully close on busy boxes — Authentik 2026.2.x's cache-pool connection leak (#20714) causing `ak-pg-watchdog` to still fire ~4x/hour on tak-10 even after MAX_REQUESTS auto-tuned to its floor of 100. Each fire creates a ~30-60s window where TAK Server user lookups fail, accumulating zombie subscriptions in TAK Server's `DistributedSubscriptionManager` that TAK Server cannot self-heal (51 zombies observed on Tom's v0.9.22 box, 2026-05-15).

**The root problem with the v0.9.23 Phase 4+5 mitigations:**
- MAX_REQUESTS=1000 + autotune bounded the leak inside long-lived gunicorn workers (good), but the leak rate × 100 requests floor still produced enough PG idle accumulation to trip the watchdog every ~15 min on busy boxes.
- Each watchdog fire = whole-container restart = ~30-60s LDAP outpost disconnect = ~10+ TAK Server user-lookup failures.
- TAK Server has no self-heal path for the resulting null-user subscriptions — they accumulate as phantom `tls:N` entries in TAK Portal/Marti until JVM restart.

**The architectural fix:**
Insert PgBouncer (transaction-pool mode) between Authentik and Postgres. PgBouncer maintains a small pool of real PG server connections (DEFAULT_POOL_SIZE=25 + RESERVE_POOL_SIZE=5 = ~30 real connections) regardless of how leaky Authentik's client-side psycopg pools become. Authentik workers can "hold" thousands of idle client connections without ever exhausting real Postgres slots — PgBouncer only borrows a server connection for the duration of each transaction.

**Why this is THE fix and not just another mitigation:**
- It moves the constraint from "Authentik must not leak" (which we can't enforce — it's upstream code) to "PgBouncer must cap connections" (which is exactly what PgBouncer is designed to do).
- The watchdog drops to deep-defense-in-depth status; it should never fire on a v0.9.23 Phase 6+ box.
- The MAX_REQUESTS autotune also drops to a memory-bounding role (still useful — worker processes accumulate psycopg client-side objects = memory growth). The pgbouncer install resets MAX_REQUESTS to baseline=1000 since aggressive recycling is no longer load-bearing.
- Same architectural pattern Christian Elsen runs in production on TAK-NZ/auth-infra (PR #102, on managed RDS) and that Erick Pound's FastTAK #31 issue converged toward.

**Reference (consulted before implementing — per `.cursor/rules/consult-upstream-docs.mdc`):**
- https://docs.goauthentik.io/install-config/configuration/#using-a-postgresql-connection-pooler
- `goauthentik/authentik#14148` + PR `#14149` (April 2025 doc fix: `DISABLE_SERVER_SIDE_CURSORS=true` is REQUIRED with transaction-pool mode; the previous docs said `false` and caused `query_wait_timeout`).
- `goauthentik/authentik` deprecated `AUTHENTIK_POSTGRESQL__USE_PGBOUNCER` — we do NOT set this, we use the modern individual env vars instead.
- `edoburu/docker-pgbouncer` v1.25.1 — pinned image (15MB Alpine, auto-generates userlist from `DB_USER`/`DB_PASSWORD`, supports scram-sha-256).

**Implementation (landed in this commit):**

1. **New helper `_ensure_authentik_pgbouncer(plog)` in `app.py`.**
   - Pre-checks: ~/authentik present, compose has `postgresql` service, not already installed, `AUTHENTIK_POSTGRESQL__HOST` not operator-customized.
   - Backs up `docker-compose.yml` and `.env` to `.bak.before-pgbouncer.<ts>` before any mutation.
   - Patches compose: adds `pgbouncer` service (edoburu/pgbouncer:v1.25.1-p0, scram-sha-256, transaction pool mode, healthcheck) and updates `server` + `worker` depends_on.
   - Patches `.env`: sets `AUTHENTIK_POSTGRESQL__HOST=pgbouncer`, `__PORT=5432`, `__DISABLE_SERVER_SIDE_CURSORS=true`, `__CONN_HEALTH_CHECKS=true`, `__CONN_MAX_AGE=0`. Adds a documentation header explaining the change.
   - Brings up `pgbouncer` container, waits for healthcheck (max 120s).
   - Force-recreates `server` + `worker` via existing `_recreate_authentik_server_worker` helper (LDAP outpost preserved as always).
   - Post-install probe: `pg_stat_activity` count of connections from `application_name='pgbouncer'` vs direct.
   - Resets MAX_REQUESTS back to baseline=1000 (autotune floor of 100 is no longer load-bearing with PgBouncer in place).
   - Records full outcome to `settings.authentik_pgbouncer` for operator audit.
   - On any failure: restores compose + .env backups, removes `pgbouncer` service via `docker compose rm -sf pgbouncer`.

2. **Wired into `_startup_migrations`** right after `_patch_authentik_web_max_requests_to_1000`. Runs at every console boot — idempotent no-op when pgbouncer is already in compose+env. Update Now triggers it via the post-update service restart.

3. **Watchdog docstring + alert message updated** to reflect PgBouncer's primary-fix role:
   - `_authentik_channels_pool_watchdog_loop` docstring now says PgBouncer is THE fix and watchdog is deep-defense-in-depth.
   - Alert message detects `settings.authentik_pgbouncer.installed` and gives operator a different action plan when PgBouncer is installed (check PgBouncer container + `SHOW POOLS`) vs not installed (run the v0.9.23 migration).

4. **New endpoint `/api/authentik/pgbouncer`** returning install status, container state, live `SHOW POOLS` / `SHOW STATS` output, and `pg_stat_activity` split between via-pgbouncer vs direct. Designed to power a dashboard tile.

**Phase 6 hot-fix landed 2026-05-15 evening (post tak-10 first install):**

5. **`_authentik_pgbouncer_pg_activity_breakdown(timeout_s)` helper** — replaces the brittle `application_name='pgbouncer'` filter in the post-install probe and the `/api/authentik/pgbouncer` endpoint. PgBouncer does NOT propagate `application_name` for Authentik 2026.2.x (the field's empty), so the original probe counted zero "via PgBouncer" connections even when PgBouncer was working perfectly. Correct check: resolve the pgbouncer container's IP via `docker inspect`, then group `pg_stat_activity` by `client_addr`. Tak-10's first install fired a misleading "⚠ pg_stat_activity shows 0 connections from pgbouncer" warning that the helper now resolves correctly.

**Acceptance:**

- [ ] Fresh `~/authentik` install: pgbouncer service appears in `docker-compose.yml`, `.env` has all 5 PgBouncer settings, AUTHENTIK_POSTGRESQL__HOST=pgbouncer.
- [ ] Existing `~/authentik` install on dev-build: migration auto-fires on next console boot, backups land at `.bak.before-pgbouncer.<ts>`, AUTHENTIK_POSTGRESQL__HOST flips to pgbouncer, server+worker recreate succeeds, post-install probe shows pg_stat_activity now serving Authentik via PgBouncer.
- [ ] `pg_stat_activity` idle count from `authentik-server-1` stays below 50 for ≥24-hour soak (was >150 pre-PgBouncer).
- [ ] `ak-pg-watchdog` alert lines disappear from `journalctl -u takwerx-console`.
- [ ] `ak-mr-autotune.log` shows MAX_REQUESTS drifting back up to baseline=1000 over the 24-hour soak (no fires → tune-up at +6h, +6.5h, etc).
- [ ] Authentik UI login + LDAP outpost bind work normally (regression check).
- [ ] TAK Server log shows zero `User lookup failed` / `null subscription` errors over 24-hour soak.
- [ ] If `docker compose down && docker compose up -d`: pgbouncer comes up healthy before server+worker (depends_on chain works).

**tak-10 first-install evidence (2026-05-15, 16:14 UTC) — partially misread, see v2.2 below:**
- PgBouncer container healthy after ~30s. ✓
- Steady-state idle connections: 9 (was 72-167 pre-install). 7-18x reduction. ✓ (genuinely happened — Authentik client-side cache flush after the recreate)
- ~~`client_addr` breakdown: 172.19.0.4 = 6 idle (pgbouncer-server pool), 172.19.0.3 = 4 idle (pgbouncer-worker pool). All Authentik traffic flowing through PgBouncer.~~ — **WRONG**. Subsequent forensic on 2026-05-15 evening proved .3 and .4 are server-1 and worker-1 connecting DIRECTLY to postgresql-1, bypassing PgBouncer entirely. PgBouncer has had 0 server-side connections to the `authentik` DB since install.
- `ak-pg-watchdog` alert count: 1 in 1h50m before measurement, 0 since. ✓ — but this happens to coincide with low Authentik load, not with PgBouncer working.

### v2.2 — compose precedence bug (2026-05-15 ~18:20 PT, **the real fix**)

Forensic on tak-10 evening of 2026-05-15 (after multiple "everything looks fine" verifications) discovered the v1 install was silently incomplete:

```
.env:                                AUTHENTIK_POSTGRESQL__HOST=pgbouncer    ✓ correct
docker exec authentik-server-1 env:  AUTHENTIK_POSTGRESQL__HOST=postgresql   ✗ STALE
docker exec authentik-worker-1 env:  AUTHENTIK_POSTGRESQL__HOST=postgresql   ✗ STALE
pg_stat_activity:                    15 direct conns, 0 via PgBouncer
PgBouncer SHOW DATABASES:            authentik pool ready, 0 current_connections
```

**Root cause:** The Authentik upstream `docker-compose.yml` template hardcodes the variable in `services.{server,worker}.environment`:

```yaml
services:
  server:
    environment:
      AUTHENTIK_POSTGRESQL__HOST: postgresql      # ← hardcoded
      AUTHENTIK_POSTGRESQL__USER: ${PG_USER:-authentik}
      AUTHENTIK_POSTGRESQL__PASSWORD: ${PG_PASS}
    env_file:
      - .env
```

Per Docker Compose semantics, **`environment:` takes precedence over `env_file:`.** So the v1 install's `.env` rewrite was overridden every time the containers were (re)created. The post-install probe that "succeeded" was actually counting all 9 idle `172.19.0.3` and 6 idle `172.19.0.4` connections as "via PgBouncer" because of a separate IP-mapping confusion — both IPs are direct clients, neither is PgBouncer (which is at 172.19.0.7).

**The fix (v2.2 code change in `_ensure_authentik_pgbouncer`):**

1. Detect the partial-install state — `pgbouncer` in compose + `.env` says pgbouncer + but `services.server.environment.AUTHENTIK_POSTGRESQL__HOST` still says `postgresql` → emit a clear "PARTIAL INSTALL DETECTED" log and apply the fixup.
2. **Always rewrite `services.{server,worker}.environment.AUTHENTIK_POSTGRESQL__HOST`** from `postgresql` → `pgbouncer` during install. This is what makes the .env change actually take effect.
3. Post-install probe records `last_outcome='bypassed'` (not `'ok'`) if `via_pgbouncer==0 AND direct>0`, with the manual remediation command in the log. Previously this was a weak warning that nobody noticed.

**tak-10 v2.2 verification path:**

```bash
# Before Update Now:
docker exec authentik-server-1 env | grep AUTHENTIK_POSTGRESQL__HOST
# AUTHENTIK_POSTGRESQL__HOST=postgresql   ← bug state

# After Update Now (v2.2 fix applied):
docker exec authentik-server-1 env | grep AUTHENTIK_POSTGRESQL__HOST
# AUTHENTIK_POSTGRESQL__HOST=pgbouncer    ← fixed

# And pg_stat_activity now shows PgBouncer in the path:
docker exec authentik-postgresql-1 psql -U authentik -d authentik -c \
  "SELECT client_addr, count(*) FROM pg_stat_activity WHERE datname='authentik' GROUP BY 1;"
# 172.19.0.7 (pgbouncer) | ~5-25 conns    ← REAL pooled connections
# (no 172.19.0.3 / 0.4 direct conns)

# And PgBouncer's authentik pool now has activity:
PG_PW=$(sudo grep -E '^PG_PASS=' /home/takwerx/authentik/.env | head -1 | cut -d= -f2-)
docker exec -e PGPASSWORD="$PG_PW" authentik-pgbouncer-1 \
  psql -h 127.0.0.1 -p 5432 -U authentik pgbouncer -c 'SHOW POOLS;'
# database=authentik with cl_active > 0 and sv_active/sv_idle > 0
```

---

## Item 7 — TAK Server connection-state diagnostic (added 2026-05-15 evening, **corrected to v2 the same evening**)

### v1 (the misdiagnosis — kept here as a cautionary tale)

The Phase 6b code shipped first as a "TAK Server zombie subscription" diagnostic + sweep, on the working hypothesis that pre-PgBouncer Authentik LDAP-outpost outages were leaving orphaned subscriptions in TAK Server's `DistributedSubscriptionManager` that survived JVM restarts. This was based on Tom Andersen's anctakserver2 forensic that observed "199 subscriptions, 165 epoch-zero `lastEventTime`, ZERO actively reporting" as the trailing effect of ak-pg-watchdog restarts.

That mental model was wrong. The endpoint and sweep were built, but never delivered the value they implied.

### v2 (what's actually shipping — corrected 2026-05-15 ~17:30 PT)

Field forensic on tak-10 the same evening — after PgBouncer landed cleanly and the watchdog went quiet for 21+ hours — revealed the actual TAK Server data model:

**1. `client_endpoint` is an immortal audit log, not a runtime subscription pool.** Schema:

```
client_endpoint (47 rows on tak-10)        client_endpoint_event (1,801 rows on tak-10)
─────────────────────────────────          ────────────────────────────────────────────
id (PK)              ◄──── FK ─────────── client_endpoint_id  (ON DELETE RESTRICT)
callsign             (varchar 100)         connection_event_type_id  (1=Connected, 2=Disconnected)
uid                  (varchar 100)         created_ts  (timestamp(3) with tz, NOT NULL, indexed)
username             (text, nullable)      client_version, groups (bitfield)
team, role           (text, nullable)
```

Two event types only. The `ON DELETE RESTRICT` is intentional: TAK Server is explicitly preserving the audit trail. Rows persist across JVM restarts (proven by running `sudo systemctl restart takserver` on tak-10 and counting 27 client_endpoint rows pre-restart, 27 post-restart, same UIDs, just bumped timestamps from new events).

**2. Marti's `/api/clientEndPoints` field `lastEventTime: null` does NOT mean "zombie" or "no events."** It means **the client is currently in the Disconnected state.** Proven empirically on tak-10:

- Device `D8985041-C4C0-4830-9487-93EAFC71D187` ("AJ-iTAK") was reported as `lastEventTime: null` at 13:31 PT.
- One minute later (13:32 PT) it CONNECTED.
- Direct DB query showed this device had 7 connect/disconnect cycles that day plus daily activity for weeks prior.
- The `null` in the API just reflected "most recent event for this UID is a Disconnect" at the moment of the query.

**3. The v1 "27 zombies" count on tak-10 was purely audit-log accumulation.** 47 unique identities ever seen across 35 days of testing × 2-3 username variations per device (from operator switching LDAP users during testing) = ~30 rows in client_endpoint. None of these were operational problems. Both PgBouncer (architectural fix) and the existing watchdog (defense in depth) work correctly; nothing was "leaking."

**4. `sudo systemctl restart takserver` does NOT clean any of this** because the data is persisted in Postgres, not in JVM memory. The v1 sweep strategy was a no-op.

### What v2 ships

- **`_takserver_connection_state(timeout_s, sample_size)`** helper. Queries the local cot DB via `sudo -u postgres psql cot -tAF$'\t' -c <sql>`. Returns:
  - `currently_connected`, `currently_disconnected` (derived from each identity's most recent event row)
  - `total_identities` (count of `client_endpoint`)
  - `total_events`, `events_last_5min`, `events_last_1h`, `events_last_24h` (audit-log activity)
  - `earliest_event_utc`, `latest_event_utc`
  - `sample_connected` — top N currently-connected clients with callsign / uid / username / since-when
  - Context-aware advisory: `HEALTHY` / `IDLE` / `QUIET` / `DORMANT` / `INACTIVE`. Never `CRITICAL` from a stale-row count alone (that was the v1 bug).

### v2.1 advisory correction (2026-05-15 ~18:00 PT)

First v2 run on tak-10 returned advisory `ATTENTION: 5 client(s) currently connected BUT no events in last 5 min` while showing 5 healthy connections and recent events 17 min ago. False alarm caused by my own misread of the table semantics:

**`client_endpoint_event` records ONLY state transitions (Connect=1, Disconnect=2). It does NOT record CoT traffic, heartbeats, or per-message activity.** A stably-connected client generates ZERO audit rows for the entire duration of its session — potentially hours or days. So "no transitions in last 5 minutes" is normal steady state, not an impairment signal.

The v2.1 advisory drops the `events_last_5min == 0 → ATTENTION` branch. Final rules:

| State | Condition |
|---|---|
| `INACTIVE` | `total_events == 0` |
| `HEALTHY` | `currently_connected > 0` (no further checks — DB-derived count is authoritative) |
| `IDLE` | `currently_connected == 0` AND `events_last_1h > 0` |
| `QUIET` | `currently_connected == 0` AND `events_last_24h > 0` |
| `DORMANT` | `currently_connected == 0` AND `events_last_24h == 0` AND `total_events > 0` |

CoT-routing impairment, if it ever happens, surfaces in `takserver-messaging.log` — not in `client_endpoint_event`. We don't try to derive it from the audit tables.

- **`GET /api/takserver/zombies`** kept as alias. **`GET /api/takserver/connection-state`** is the canonical name. Both return the same v2 response shape.

- **`POST /api/takserver/zombies/sweep`** — returns **410 Gone** with explanation. There is nothing to sweep under the corrected model. The retired strategy is documented in the response body for any operator who reads it.

- **`_takserver_subscriptions_breakdown`** kept as a back-compat shim that now delegates to `_takserver_connection_state`. Removable in a future release once nothing references it.

- **`ops/diagnostics/anchortak/zombies.sh` + `zombies.py`** rewritten to query the cot DB directly via `sudo -u postgres psql` instead of curling Marti. Same v2 response shape, same advisory taxonomy. No cert passphrase required (the v1 script had to negotiate the operator's tak_cert_password); the DB path is simpler and more accurate.

### Tak-10 v2 evidence (when the corrected code lands)

Expected reading on the current state of tak-10 once `9eeac71`+followup is deployed:

- `currently_connected`: 0-5 (depending on what test devices are running)
- `currently_disconnected`: ~42-47 (the audit-log roster — many physical devices × LDAP user variations from April-May testing)
- `total_events`: 1,800+ across 35 days
- `events_last_1h`: 0-10 typical for active testing
- Advisory: `HEALTHY` whenever any client is connected; `IDLE` / `QUIET` / `DORMANT` when no clients connected and various event-activity windows are empty. **Never** `CRITICAL` or `ATTENTION` from these tables alone (v2.1).

Empirically verified on tak-10 2026-05-16 00:53 UTC: 5 connected / 42 disconnected / 1,803 events / advisory `HEALTHY` ✓.

### Acceptance

- [ ] `GET /api/takserver/zombies` returns the v2 response shape with `model: "v2"`.
- [ ] `GET /api/takserver/connection-state` returns the same shape (canonical alias).
- [ ] `POST /api/takserver/zombies/sweep` returns 410 with explanation; does NOT run systemctl restart.
- [ ] On a box with TAK Server installed + recently active clients: advisory is `HEALTHY`.
- [ ] On a box without TAK Server: `error` field is set to "TAK Server not installed", endpoint still returns 200 (graceful degradation).
- [ ] `zombies.sh` requires NO cert passphrase (queries DB, not Marti).

---

## Out of scope (deferred to v0.9.24)

- **Node-RED ArcGIS multipart polygon support** — moved to `PLAN-v0.9.24-alpha.md`. Original scope unchanged.
- **Upstream fix for Authentik #20714** — out of our control. Track upstream, drop the migration + watchdog when fixed.
- **Per-subscription Marti DELETE sweep strategy** — Marti API endpoint shape varies across TAK Server versions. JVM restart is sufficient for v0.9.23. Revisit if operators report "we need to clear zombies without disconnecting legitimate clients."
- **Auto-sweep policy** (e.g. nightly if >N zombies). Manual sweep + diagnostic ships in v0.9.23. Auto-sweep policy decisions wait for field evidence on operator preferences.

---

## Open questions (do not block release)

- **Item 1b — what is actually drifting?** DB `password_change_date` on tak-10 did not update between the boot-time heal and recurrence, yet fresh binds began failing again. The watchdog will fix the symptom regardless, but root cause warrants investigation post-release. Capture pre-heal state into `~/.config/infra-tak/ldap-sa-watchdog.log` so we can diagnose with fleet data.
- **Is `session_duration=120s` on the LDAP authentication login stage the right value?** Current value bounds the cached bind session to 2 min (intentional per `HANDOFF-LDAP-AUTHENTIK.md` line 702). With the watchdog now closing the recovery loop at 5 min, we may be able to increase this back to a longer duration to reduce flow-executor load. Test this after Item 1 is stable.

---

_Plan created 2026-05-14 evening after tak-10 forensic capture confirmed the existing boot-time self-heal works but only fires at console startup. Same architectural pattern (continuous re-assertion via `_startup_migrations` + periodic watchdog daemon) addresses all three drift classes operators are seeing._
