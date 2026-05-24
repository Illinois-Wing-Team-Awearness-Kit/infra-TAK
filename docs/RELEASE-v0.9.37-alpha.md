# v0.9.37-alpha — Authentik version gating: vetted release control for fleet safety

**Date:** 2026-05-22
**Type:** Fleet-safety release — drop-in update via Update Now.
**Status:** RELEASED to `main` 2026-05-22. Validated on test6 (Authentik 2026.5.0) + test8 (Authentik 2026.2.3), SHA `ad130a1`, 75-min soak, zero watchdog fires, zero idle-in-transaction accumulation, all containers healthy. Operator-authorized 2-box gate (test12 held on main as baseline control).

---

## TL;DR

Authentik 2026.5.0 ships a **Rust-based worker orchestrator** (PR #21324) that runs a
persistent async event loop — CPU baseline shifts from ~0% to ~150% at idle.  After a
comprehensive performance review (test6 on 2026.5.0 vs. test8/test12 on 2026.2.3) the
decision is to **gate fleet-wide Authentik updates** until the new architecture is fully
validated.

Starting with v0.9.37:

- **Main-channel boxes** will never be offered an Authentik version above
  `AUTHENTIK_VETTED_RELEASE` (`2026.2.3`).  Customers stay on the last
  fleet-validated release.
- **Dev-channel boxes** (`update_channel = 'dev'`) see `AUTHENTIK_DEV_RELEASE`
  (`2026.5.0`) — the version under active validation.
- **Fresh installs** always use the channel-appropriate target (vetted for main,
  dev release for dev-channel boxes).
- The **Update button** only illuminates when the installed version is below the
  channel target.  Boxes already on `2026.5.0` will never see a "downgrade" badge.
- The **version status line** now shows `vetted ✓` (green) for main-channel
  installations and `dev: v2026.5.0` (amber) for dev-channel installations.

No config changes required.  No operator action on update.

---

## Background: Authentik 2026.5.0 — what changed

Authentik merged a new Rust-based worker manager (PR #21324, shipped 2026.5).
Instead of a `python-worker` process managed directly by Python, the `worker` container
now runs a **Rust orchestrator** that spawns Python sub-workers:

```
Before (2026.2.x):  authentik worker (Python, Dramatiq)
                    CPU idle: ~0%,  RAM: ~450 MB

After  (2026.5.0):  authentik Rust orchestrator + Python sub-workers
                    CPU idle: ~150%, RAM: ~300 MB  (expected steady state)
```

The Rust event loop is always running regardless of task load — this is architectural,
not a bug.  On a 4-vCPU box the `150%` maps to 1.5 out of 4 cores committed to
infrastructure overhead.  The console's CPU display will show this elevated baseline,
which at first glance looks alarming.

**Decision:** keep customers on `2026.2.3` until the new Rust worker architecture has
been soaked for ≥60 min across all three dev boxes simultaneously with zero watchdog
fires.  When that T&E passes, promote `AUTHENTIK_DEV_RELEASE` → `AUTHENTIK_VETTED_RELEASE`
in a new infra-TAK release.

---

## How to promote Authentik to a new vetted release in the future

When you're ready to fleet-release a new Authentik version:

1. Soak the candidate on dev boxes (full T&E per `TEST-AND-EVALUATION-PROCEDURE.md`).
2. In `app.py` update two constants:
   ```python
   AUTHENTIK_VETTED_RELEASE = "<new-version>"   # was the dev release
   AUTHENTIK_DEV_RELEASE    = "<next-candidate>"
   ```
3. Bump `VERSION` to the next infra-TAK release.
4. Push to `dev`, run T&E, then ship to `main`.

---

## Changes

### `app.py`

- `VERSION` bumped from `0.9.36-alpha` → `0.9.37-alpha`.
- `AUTHENTIK_VETTED_RELEASE = "2026.2.3"` — fleet-validated release.
- `AUTHENTIK_DEV_RELEASE = "2026.5.0"` — under validation on dev channel.
- **`_get_authentik_target_release(settings=None)`** — new helper that returns the
  channel-appropriate Authentik version:
  - `dev` channel → `AUTHENTIK_DEV_RELEASE`
  - `main` channel → `AUTHENTIK_VETTED_RELEASE`
- **`_get_authentik_version_info()`** — now calls `_get_authentik_target_release()` instead
  of the raw GitHub API.  Returns additional fields `vetted_release`, `dev_release`,
  `channel` for the template.  Version comparison uses a tuple comparison to prevent
  a "downgrade available" badge on boxes already above the vetted ceiling.
- **`authentik_control()` (local + remote update paths)** — Update action uses
  `_get_authentik_target_release()` instead of GitHub latest.
- **`run_authentik_deploy()`** — AUTHENTIK_TAG pinning during deploy uses target release.
  Added direction check: only pins if target > current (never silently downgrades an
  operator-upgraded installation).
- **Fresh install compose generation** — uses `_get_authentik_target_release()`.
- **`AUTHENTIK_TEMPLATE`** — status detail line now shows:
  - `vetted ✓` (green) — main channel, up to date.
  - `dev: v2026.5.0` (amber) — dev channel.
  - `v{latest} available` (cyan) — update available (existing behavior, target is now
    the vetted/dev ceiling rather than GitHub latest).
  - Update button tooltip clarifies `(fleet-vetted)` vs `(dev channel)`.

---

## Backward compatibility

- **Boxes on 2026.2.3** — no change to their behavior.  `update_available = False`.
  Status line shows `vetted ✓`.
- **Boxes on 2026.5.0** — version tuple check means they won't see a "downgrade" badge.
  If on main channel: `update_available = False` (2026.5.0 > 2026.2.3, no downgrade
  offered).  If on dev channel: `update_available = False` (already at dev target).
- **Boxes below 2026.2.3** — `update_available = True`, button glows cyan, target is
  `2026.2.3`.
- `_get_authentik_latest_release_tag()` is preserved (still used internally for caching
  raw GitHub data) but is no longer the source of truth for what version to offer.
