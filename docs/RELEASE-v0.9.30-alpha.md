# v0.9.30-alpha — fail2ban Marketplace install no longer requires a console restart on fresh deploys

**Date:** 2026-05-18
**Type:** Bugfix release — drop-in update via Update Now.
**Status:** Implemented on `dev`. Awaiting fleet validation gate (tak-10 / test8 / responder) before merge to `main`.

---

## TL;DR

On a brand-new v0.9.29 deploy, clicking **Install** on **fail2ban** in the Marketplace immediately after deploying Authentik fails with:

```
fail2ban migration: checking prerequisites
fail2ban migration: SKIPPED — v0.8.9 trusted-proxy CIDR fix not yet confirmed
  (last_outcome=''). Re-runs automatically once the prerequisite is met.
✗ Installation failed. Check logs above.
```

The error message lies: nothing re-runs automatically from the Marketplace install path. The prereq stamp (`settings.authentik_trusted_proxy_cidrs_fix.last_outcome`) is only written by `_authentik_fix_trusted_proxy_cidrs`, which is gated on `~/authentik/.env` existing AND is only invoked from `_startup_migrations` / `_post_update_auto_deploy`. On a single-session fresh deploy the .env doesn't exist when those hooks run at boot, so the stamp never lands.

Field repro: two Azure boxes deployed back-to-back on the same v0.9.29 build. The first happened to get a console restart between Authentik install and fail2ban install (the user was unaware) — that restart re-ran `_startup_migrations`, the migration applied, the prereq stamped, fail2ban installed cleanly. The second went straight from Authentik install to fail2ban install with no restart, hit the prereq guard, and failed. Same code, two outcomes — pure timing race.

## What v0.9.30 changes

Two minimal edits to `app.py`, both fleet-uniform and idempotent:

1. **Bake `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS=172.16.0.0/12,127.0.0.1/32,::1/128` into both Authentik `.env` templates** (local at line ~32499, remote at line ~25097). Fresh installs now converge to the exact same `.env` content that boxes upgraded through the v0.8.9 migration have — Caddy → Authentik logs the real client IP from byte one, not the Docker bridge gateway `172.18.0.1`. Closes the latent v0.8.9 hole on every fresh install (audit logs, Reputation policy, fail2ban filter regex all see correct client IPs immediately).
2. **Call `_authentik_fix_trusted_proxy_cidrs(plog)` at the end of both Authentik deploy flows** (local at the "Deploy complete" line, remote just before the 🎉 banner). With the CIDR line baked into the template, this call hits the `idempotent-noop` branch and stamps `settings.authentik_trusted_proxy_cidrs_fix.last_outcome='idempotent-noop'` in the same console session as the deploy. The fail2ban Marketplace install can now succeed immediately after the Authentik deploy without requiring a process restart.

## Net behavior on a fresh box (v0.9.30+)

1. Boot console — `~/authentik/.env` doesn't exist → trusted-proxy migration skipped, `last_outcome` unset (unchanged).
2. Deploy Authentik — `.env` is written *with* `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS`. At the end of the deploy, the migration function is invoked, detects the key, stamps `last_outcome='idempotent-noop'`.
3. Click **Install fail2ban** in the Marketplace — prereq guard sees `last_outcome='idempotent-noop'`, prereq satisfied, install proceeds.

No restart required. No "wait, why did it work on the other box?" moments.

## Net behavior on an existing box (any prior v0.9.x)

`_authentik_fix_trusted_proxy_cidrs` is unchanged. Boxes that already ran the migration via `_startup_migrations` or `_post_update_auto_deploy` continue to have `last_outcome='applied'` or `'idempotent-noop'` — no action required, no double-write, no settings drift. The v0.9.30 inline call at deploy-end on those boxes hits the same idempotent-noop branch and is a no-op.

## Fleet-uniform compliance

Per `.cursor/rules/fleet-uniform-config.mdc`:

- ✅ **Fleet constant, no operator-override preservation.** Every box writes `172.16.0.0/12,127.0.0.1/32,::1/128` to `.env` — same value, no `max(cur, target)`, no per-customer tier.
- ✅ **Convergence verified by an existing probe.** `_authentik_verify_runtime_config()` (v0.8.9) already polls `ak dump_config` and asserts `listen.trusted_proxy_cidrs` contains `172.16.0.0/12`. Nothing new to wire.
- ✅ **No silent ignore.** The CIDR line uses the double-underscore Authentik syntax (`AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS`) — verified at runtime via dump_config, not just `.env` presence.

## Validation gate before merge to `main`

This is a single-issue bugfix on top of v0.9.29-alpha; both code paths it touches are idempotent on top of existing healthy boxes. Validation plan:

- [ ] tak-10 — Update Now from v0.9.29-alpha → v0.9.30-alpha. Verify `_authentik_fix_trusted_proxy_cidrs` post-update log shows `idempotent-noop` (already applied historically). Verify fail2ban still installed and healthy (`systemctl is-active fail2ban`). 60-min soak, no `query_wait_timeout`, no watchdog ALERT.
- [ ] test8 — same as tak-10. Bonus: confirm `_authentik_verify_runtime_config` post-update line shows `listen.trusted_proxy_cidrs: 172.16.0.0/12 ✓`.
- [ ] infratak-vps / responder — same as tak-10.
- [ ] **Fresh deploy on a clean cloud VM** (Azure DS2 v2 baseline). Deploy Authentik, then immediately click Install fail2ban from the Marketplace **without restarting the console**. Verify install succeeds end-to-end and `fail2ban-client status authentik` returns the jail definition.

If the fresh-deploy validation passes, the timing race is closed.

## Files changed

- `app.py`:
  - Line 369: `VERSION = "0.9.30-alpha"`
  - Line ~25109: remote `.env` template — adds `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS=...`
  - Line ~25660 (before the deploy success banner): `_authentik_fix_trusted_proxy_cidrs(plog)` call
  - Line ~32514: local `.env` template — adds `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS=...`
  - Line ~33806 (before "Deploy complete"): `_authentik_fix_trusted_proxy_cidrs(plog)` call
- `docs/RELEASE-v0.9.30-alpha.md` (this file)

No schema changes. No new settings keys. No new dependencies. No new files installed on the host.
