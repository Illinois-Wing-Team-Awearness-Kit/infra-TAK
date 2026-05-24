# Plan — v0.9.29-alpha

> **Status:** IMPLEMENTED (awaiting fleet validation gate)
> **Target:** v0.9.29-alpha
> **Scope:** (1) Security audit touch-up — response to the 2026-04 Oblivion Edge / Project Shakespear audit (CRIT-08 SSH host-key pinning + CRIT-07 eval cleanup + atakatak nag + CSP unsafe-eval drop + audit response doc). (2) Authentik MAX_REQUESTS cascade short-circuit — a startup migration that snaps MAX_REQUESTS straight to baseline=1000 when PgBouncer is installed and the system is quiet, replacing the v0.9.23 autotuner's 7-hour, 10-recreate climb with a single optional recreate. Field-discovered on tak-10 during v0.9.28-alpha soak (2026-05-18).
>
> **Origin:** Inserted ahead of the Node-RED multipart polygon work that previously held this slot. Node-RED multipart scope moved to [`PLAN-v0.9.30-alpha.md`](PLAN-v0.9.30-alpha.md). Full audit response + per-repo split lives in [`SECURITY-AUDIT-RESPONSE-2026-04.md`](SECURITY-AUDIT-RESPONSE-2026-04.md).

---

## Why v0.9.29 exists

A friend-of-the-operator audit by "Oblivion Edge Vulnerability Research LLC / Project Shakespear" (April 2026, 74 findings, 9 CRITICAL) was forwarded for review. Per-file breakdown showed:

- **6 of 9 CRITs are in `mediamtx-installer`, not `infra-TAK`.** Owned separately; forwarded.
- **3 CRITs target `infra-TAK`** — CRIT-06 (SSH probe injection), CRIT-07 (`eval` in guarddog), CRIT-08 (`StrictHostKeyChecking=no` in guarddog).
- **Both findings the auditor manually verified are in MediaMTX, not `infra-TAK`.** The other 7 CRITs are `Status: TBD` — inherited from a prior audit, not re-confirmed.
- **CRIT-06 cited stale line numbers** (`app.py:7932, 7962`) — current code is already mitigated by `_validate_ssh_target` + `shlex.quote` patterns. Verified, not changed.

What v0.9.29 actually changes:

1. **CRIT-08 fix (primary).** Replace `StrictHostKeyChecking=no` with `accept-new` + pinned `UserKnownHostsFile` across 9 guarddog bash scripts and 2 raw-SSH callsites in `app.py`.
2. **CRIT-07 cleanup.** Replace `eval` patterns in 3 guarddog scripts (`tak-db-repack.sh`, `tak-auto-vacuum.sh`, `tak-retention-guard.sh`).
3. **MED: `atakatak` default password nag.** Dismissable banner when TAK cert password is still the upstream default.
4. **LOW: CSP `unsafe-eval` removed.** Codebase has no `eval()` / `new Function()` / string-form `setTimeout`.
5. **Docs.** Audit response + per-repo split + already-addressed-defaults map in `SECURITY-AUDIT-RESPONSE-2026-04.md`.
6. **Stability: Authentik MAX_REQUESTS cascade short-circuit.** New startup migration `_patch_authentik_max_requests_snap_to_baseline_if_pgbouncer` (~223 lines + a `_estimate_cascade_recreates` helper). Replaces the v0.9.23 autotuner's 7-hour, 10-recreate climb-from-floor cascade with a single optional recreate. Idempotent: no-op on boxes already at baseline. Field-discovered on tak-10 (test12) during v0.9.28 soak.
7. **AI guardrail rule.** New `.cursor/rules/no-main-merge-no-tag-without-permission.mdc` codifies the boundary established during v0.9.28 shipping: no merge-to-main or tag-push without explicit operator green-light.
8. **TAK LDAP CoreConfig verifier — namespace-prefix tolerance.** Discovered 2026-05-18 on a fresh Azure deploy `tak-test-4` (v0.9.28 main). TAK Server 5.7-RELEASE-32-HEAD canonicalizes `CoreConfig.xml` with `<ns0:auth>` namespace prefix; `_coreconfig_has_ldap()` was substring-matching `<auth` and falsely reporting `coreconfig=FAIL`. Fixed verifier + legacy text-fallback patcher `_apply_coreconfig_ldap_auth_text()` (same regex bug class). Unit-tested across 5 XML shapes before deploy.
9. **Cloud public-IP detection on fresh install.** Same `tak-test-4` repro: `start.sh` used `hostname -I` which returns the Azure private IP, written into `settings.server_ip` and the SSL cert SAN. New `detect_server_ip()` helper queries Azure IMDS → AWS IMDS → `api.ipify.org` → `ifconfig.me` → `hostname -I`, validates IPv4 dotted-quad. Used at both the settings.json write and openssl SAN sites. Existing installs are not auto-rewritten (some operators legitimately deploy private-only).

---

## Item 1 — Fix CRIT-08 across guarddog watchdog scripts (PRIMARY)

Replace `StrictHostKeyChecking=no` with `accept-new` and pin a known_hosts file so first-contact pins and subsequent connections refuse on mismatch.

Affected files (9):

- `scripts/guarddog/tak-remotedb-watch.sh` (3 occurrences)
- `scripts/guarddog/tak-retention-guard.sh` (2)
- `scripts/guarddog/tak-remotedb-auth-watch.sh` (1)
- `scripts/guarddog/tak-fedhub-watch.sh` (1)
- `scripts/guarddog/tak-db-repack.sh` (2)
- `scripts/guarddog/tak-cotdb-watch.sh` (1)
- `scripts/guarddog/tak-auto-vacuum.sh` (2)

Mechanical change in each script: replace

```bash
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 ...
```

with

```bash
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile=/opt/tak-guarddog/known_hosts \
    -o ConnectTimeout=10 ...
```

Also migrate two raw `subprocess.run(['ssh', ...])` callsites in `app.py` (snapshot/rollback paths, ~lines 39310 and 39484) from `=no` to `=accept-new`.

**Provisioning:** `app.py` creates `/opt/tak-guarddog/known_hosts` (`0600`, root:root) in two places:

- the main Guard Dog deploy thread, right after the directory loop
- `_sync_guarddog_remote_db_from_settings`, right after `guarddog.conf` is written

Empty file on first install — scripts append on first successful connect.

**Operator escape hatch:** if a customer rotates the DB host key, `: > /opt/tak-guarddog/known_hosts` clears the pin and Guard Dog re-pins on the next cycle. Document in release notes.

Fleet-uniform per [`.cursor/rules/fleet-uniform-config.mdc`](../.cursor/rules/fleet-uniform-config.mdc): every box gets the same path, no per-box override.

---

## Item 2 — CRIT-07 eval cleanup in guarddog scripts

Three scripts use `eval "$(python3 ...)"` to load values from `/opt/tak-guarddog/guarddog.conf` into shell variables (`TWO_SERVER_MODE`, `REMOTE_DB_HOST`, etc.):

- `scripts/guarddog/tak-db-repack.sh`
- `scripts/guarddog/tak-auto-vacuum.sh`
- `scripts/guarddog/tak-retention-guard.sh`

The python emitted `export NAME=value` lines with `shlex.quote` on the host. Blast radius was root-only (config is root-owned), but `eval` is bad form.

**Refactor:** newline-delimited `read`. Python prints raw lines; bash reads them with `IFS= read -r`, so no shell parsing of values ever happens.

```bash
{
  IFS= read -r TWO_SERVER_MODE
  IFS= read -r REMOTE_DB_HOST
} < <(python3 - <<'PY'
...
print(two)
print(host)
PY
)
TWO_SERVER_MODE="${TWO_SERVER_MODE:-0}"
```

Also replace `eval "$cmd"` in the local branch of `remote_cmd()` in `tak-db-repack.sh:86` with `bash -c "$cmd"` (single-eval semantics, no `eval` keyword to flag).

---

## Item 3 — `atakatak` default-password nag (MED)

The audit's "Hardcoded certificate passwords — `atakatak` in multiple locations" is a real symptom but the upstream TAK default. `infra-TAK` falls back to `atakatak` when no override exists.

Add a dismissable banner across every console page when:

- TAK Server is installed (`/opt/tak/CoreConfig.xml` present), AND
- `tak_cert_password` is unset or equals `atakatak`, AND
- operator has not dismissed (`cert_pw_warning_dismissed`).

Implementation:

- `render_default_cert_password_warning(settings)` near `render_custom_banner` in `app.py`.
- Wired into existing `@app.context_processor` (`inject_cloudtak_icon`).
- POST endpoint `/api/security/dismiss-cert-pw-warning` (login-required, same-origin CSRF gate).
- Auto-clear `cert_pw_warning_dismissed` when a non-default password is saved (`takserver_set_cert_password`).

---

## Item 4 — CSP tightening (LOW)

`apply_security_headers` in `app.py` (~line 340) drops `'unsafe-eval'` from `script-src`. Pre-flight grep confirmed zero `eval()` / `new Function()` / string-form `setTimeout` / string-form `setInterval` in the JS bundles, inline scripts, and static files.

`'unsafe-inline'` removal is deferred — requires nonce-injection across ~25 `render_template_string` callsites. Tracked in the v0.9.30+ docs.

---

## Item 5 — Verify-and-document CRIT-06 (no code change)

Created [`docs/SECURITY-AUDIT-RESPONSE-2026-04.md`](SECURITY-AUDIT-RESPONSE-2026-04.md) with:

1. Per-repo split of the 74 findings — `infra-TAK` / `mediamtx-installer` / `takwerx` / unknown.
2. CRIT-06 mitigation map: `_validate_ssh_target`, `_ssh_probe`, `shlex.quote` pattern in FedHub deploy.
3. Pointers to where CRIT-07 and CRIT-08 fixes land in this release.
4. Already-addressed defaults (CSRF, session flags, security headers, persistent secret_key, rate limiting) with file:line references.
5. Outstanding work for v0.9.30+: `shell=True` defense-in-depth sweep, CSP `unsafe-inline` removal, request re-baseline against current `dev` from the auditor, Authentik upstream header strip in Caddy.

---

## Item 6 — Authentik MAX_REQUESTS cascade short-circuit (POST v0.9.28 STABILITY)

### Problem (field-discovered on tak-10 / test12, 2026-05-18)

While soaking v0.9.28-alpha on the three-box dev fleet (`tak-10`, `test8`, `responder`),
the v0.9.23 MAX_REQUESTS autotuner was caught quietly recreating `authentik-server-1` +
`authentik-worker-1` every ~31 minutes for 10+ hours on tak-10. Investigation:

1. The day before, a real Authentik #20714 leak burst on tak-10 triggered the
   safety-net watchdog. Autotuner halved `MAX_REQUESTS` from 1000 → 500 → 250 → 125 → 100
   (floor) over ~10 minutes. Each halve = one server+worker recreate to make gunicorn
   pick up the new env.
2. Once PgBouncer was installed (v0.9.23 Phase 6) and v0.9.28's enterprise headroom
   landed, the leak pressure was gone. No more fires.
3. After 6h of quiet, the autotuner's TUNE-UP path activated: `+25%` every 30 min.
4. Climbing back from 100 → 1000 takes 10 steps (100 → 125 → 156 → 195 → 243 → 303 → 378 → 472 → 590 → 737 → 921 → 1000).
   **Each step is a server+worker recreate.** LDAP outpost websocket drops 1-5s on each.
5. Total cost: **~7 hours and 10 recreates** to get back to a known-safe value.

The v0.9.23 PgBouncer install function (`_ensure_authentik_pgbouncer`, line 27948)
*already promised* to fix this — its post-install path resets MAX_REQUESTS to
baseline ("autotune floor=100 is no longer load-bearing with PgBouncer in place",
line 27983-27986). But that reset only fires on FRESH PgBouncer installs. Boxes
that were already-stuck-at-floor when PgBouncer landed (because they hit the leak
pressure first, then got upgraded) never got the reset.

### Fix

New idempotent startup migration `_patch_authentik_max_requests_snap_to_baseline_if_pgbouncer`
added to `app.py` after `_patch_authentik_web_max_requests_to_1000` (~line 26847).
Wired into `_startup_migrations` after `_ensure_authentik_pgbouncer_pool_size`.

Gating (all must be true):

- `~/authentik/.env` exists
- PgBouncer is installed in compose AND wired in `.env`
- current MAX_REQUESTS < baseline (`authentik_max_requests_baseline`, default 1000)
- no SAFETY NET firing in last `_AUTHENTIK_MAX_REQUESTS_QUIET_WINDOW_S` (default 6h)
- no autotune apply in last `_AUTHENTIK_MAX_REQUESTS_TUNE_UP_COOLDOWN_S` (default 30 min)

On apply:

1. Write `AUTHENTIK_WEB__MAX_REQUESTS=baseline` and `AUTHENTIK_WEB__MAX_REQUESTS_JITTER=baseline//20` to `~/authentik/.env`
2. Single `_recreate_authentik_server_worker` to make gunicorn pick up the new env
3. Clear `authentik_max_requests_fire_history` (post-PgBouncer, old fires irrelevant)
4. Record the snap in `authentik_max_requests_tune_history` with `reason='snap-to-baseline-pgbouncer-installed-quiet'`
5. Set `authentik_max_requests_last_tune_ts = now` (so autotuner won't immediately re-fire)
6. Record `authentik_max_requests_snap_migration` audit blob with `from_max`, `to_max`, `saved_recreates_estimate`

Helper `_estimate_cascade_recreates(cur, baseline)` is a pure function that
simulates how many +25%/30min steps the autotuner would have taken from `cur`
to `baseline`. Used purely for log-line accounting:

```
  ✓ snap-to-baseline: MAX_REQUESTS 100→1000, JITTER 5→50 (short-circuited ~10 +25%/30min tune-UP recreates)
```

### Field state at release time

All three dev boxes (test12, test8, test6) are already at `MAX_REQUESTS=1000`
(baseline). test12 finished its natural cascade during soak before this fix
landed; test8 and test6 were never at floor (their PgBouncer absorbed leak
pressure before any halving happened). **The fix is therefore preventive** —
silent no-op on the current fleet, kicks in only if any future box (operator
install or recovering production) ever drops below baseline with PgBouncer
present.

### Acceptance criteria

- [ ] After Update Now on all three dev boxes: `grep AUTHENTIK_WEB__MAX_REQUESTS /root/authentik/.env` returns `1000` everywhere.
- [ ] In the 60-min soak: `journalctl -u takwerx-console | grep -c "authentik recreate: starting.*max-requests-autotune"` does not increase on any box.
- [ ] On a box artificially set below baseline (`MAX_REQUESTS=200` in `.env`) + console restart: migration fires once with an APPLY log line; subsequent console restarts return False silently.

---

## Files touched

- `scripts/guarddog/tak-remotedb-watch.sh`
- `scripts/guarddog/tak-retention-guard.sh`
- `scripts/guarddog/tak-remotedb-auth-watch.sh`
- `scripts/guarddog/tak-fedhub-watch.sh`
- `scripts/guarddog/tak-db-repack.sh`
- `scripts/guarddog/tak-cotdb-watch.sh`
- `scripts/guarddog/tak-auto-vacuum.sh`
- `app.py` (SSH option flags in snapshot/rollback paths, known_hosts provisioning, atakatak nag, CSP, dismiss endpoint, **`_patch_authentik_max_requests_snap_to_baseline_if_pgbouncer` + `_estimate_cascade_recreates` + startup-migration wire-up**, VERSION bump)
- `.cursor/rules/no-main-merge-no-tag-without-permission.mdc` (new)
- `docs/SECURITY-AUDIT-RESPONSE-2026-04.md` (new)
- `docs/RELEASE-v0.9.29-alpha.md` (new)
- `docs/PLAN-v0.9.30-alpha.md` (Node-RED scope moved out)

---

## Acceptance criteria

- [ ] `rg 'StrictHostKeyChecking=no' .` returns no matches anywhere in the tree.
- [ ] All three `eval "$(python3 ...)"` patterns in guarddog scripts replaced with newline-read pattern; `rg '\beval\b' scripts/guarddog/` returns only historical comments.
- [ ] On a TAK-installed box where `tak_cert_password` is `atakatak`, every console page renders the orange "atakatak" banner with a Dismiss button; banner disappears after dismiss; banner does not return on next page load.
- [ ] On a TAK-installed box where a non-default password is set, banner does not render.
- [ ] No CSP `unsafe-eval` violations in browser DevTools console while navigating every page of the console.
- [ ] `app.py` `VERSION = "0.9.29-alpha"`.
- [ ] After Update Now: all three dev boxes show `MAX_REQUESTS=1000` and no further `max-requests-autotune` recreates in 60 min soak (Item 6 acceptance — see above).

---

## Validation gate (per fleet-uniform-config.mdc)

Before merging `dev` → `main`:

- Three test boxes (`tak-10`, `test8`, `responder` / `infratak-vps`) pull v0.9.29 from `dev` with **no manual config edits**.
- On each box, force a Guard Dog cycle that triggers an SSH watchdog (e.g. `sudo systemctl start tak-remotedb-watch.service` or wait for the timer); confirm `/opt/tak-guarddog/known_hosts` is populated after first connect and reused on the next cycle.
- Soak ≥ 60 min on each box: zero `permission denied (publickey)`, zero `host key verification failed`, all guarddog alerts behave as before.
- Browser-check console pages with DevTools open: no CSP `unsafe-eval` violations.
- Visual check of dashboard: `atakatak` nag banner appears on a fresh install, hides on a custom password, stays hidden after dismiss.
- Item 6 acceptance: MAX_REQUESTS=1000 on every box post-Update-Now; no further `max-requests-autotune` recreates in the soak window.

Only merge `dev` → `main` after all three boxes pass.
