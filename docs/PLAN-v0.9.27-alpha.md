# v0.9.27-alpha — PgBouncer pool autotune (kill fleet fracture)

**Type:** Architectural fix for v0.9.26's silent fleet fracture.
**Branch:** `dev` only until multi-box validation passes.
**Status:** Awaiting field validation on tak-10 + test8 + responder.

## Problem (root cause from v0.9.26 retrospective)

v0.9.26 codified `_AUTHENTIK_PGBOUNCER_DEFAULT_POOL_SIZE = 75` and ran the
`_ensure_authentik_pgbouncer_pool_size` migration with `max(cur, target)` to
"preserve operator overrides." That semantic — praised as a feature in the
v0.9.26 release notes — caused the fleet to fracture:

| Box | Pre-v0.9.26 pool | Post-v0.9.26 pool | Result |
|---|---|---|---|
| tak-10 | 250/50 (operator override) | **250/50** (preserved via `max()`) | stable |
| test8 | 35/5 (v0.9.24 default) | **75/15** (no override, codebase target applied) | 297 `query_wait_timeout` in 5 min, server-1 unhealthy |
| responder | unknown (likely 35/5) | likely **75/15** | (validation pending) |

I validated tak-10's stability and shipped to `main`. tak-10's 250/50 was an
operator override — **the codebase never ran 250/50 on any box**. test8 (with
no override) was the first box to actually exercise the codified default, and
it failed.

The bug is **architectural, not numeric**: `max(cur, target)` lets an
operator-typed number from a one-time fire silently outlive the incident and
drift away from every other box in the fleet forever. Bumping the constant
to 250 doesn't fix this — the next operator override creates the same drift.

## Architectural fix — deterministic autotune

Pool size becomes a **deterministic function of observed load on each box**,
computed identically everywhere. The fleet converges to whatever each box's
traffic produces — same logic, no operator-typed numbers persisted.

### Sizing formula

```
peak              = max(idle_count) over last 30 watchdog samples (~1 h)
target_ceiling    = max(FLOOR_CEILING=90, ceil(peak × 2.0))
target_ceiling    = min(target_ceiling, ceil(PG_MAX_CONN=500 × 0.6))
target_default    = ceil(target_ceiling × 5/6)
target_reserve    = target_ceiling - target_default
```

### Components

- **`_authentik_pool_autotune_sample()`** — appends `{t, idle, channels,
  dramatiq, cache, advisory_lock, other}` to `settings.pool_autotune.samples`
  (ring buffer, last 30 entries). Called every watchdog tick (2 min) from
  `_authentik_channels_pool_watchdog_loop`.
- **`_authentik_pool_autotune_compute(plog)`** — pure function: reads
  samples, computes target, returns `(default, reserve, evidence)`. Cold-start
  (samples < 3) falls back to a one-shot `pg_stat_activity` seed
  (`_authentik_pool_autotune_seed_from_live_pg`) so the FIRST v0.9.27
  migration on a box doesn't accidentally shrink from a high operator-set
  pool down to FLOOR.
- **`_ensure_authentik_pgbouncer_pool_size`** rewritten:
  - Calls autotune compute.
  - **Always writes target** to compose YAML. No `max(cur, target)`. No
    operator-override preservation. An operator-typed value will be
    reconciled on next migration tick.
  - Recreates pgbouncer only if `cur != target` (idempotent).
  - Reconciles watchdog threshold to `ceiling + 50` (kept from v0.9.26
    hotfix #4 amendment, but now bidirectional — threshold also lowers if
    pool shrinks).

### What the operator sees

On first v0.9.27 boot, `journalctl -u takwerx-console` will show one of:

```
Startup migration:   pool autotune: peak=87 over 30 samples → target 142/28 (ceiling=170)
Startup migration:   pgbouncer pool-size: shrunk docker-compose.yml DEFAULT_POOL_SIZE 250 → 142, RESERVE_POOL_SIZE 50 → 28 (ceiling=170, reason=autotune: 30 samples; peak=87 × safety=2.0 = 174; floor=90; cap=300 (not hit); split default:reserve = 142:28 (ceiling=170))
Startup migration: ✓ pgbouncer pool-size: pgbouncer recreated and healthy (DEFAULT_POOL_SIZE=142, RESERVE_POOL_SIZE=28, ceiling=170)
Startup migration: ✓ watchdog threshold: 350 → 220 (pool ceiling 170 + 50 margin)
```

Or, if cold-start seed kicks in:

```
Startup migration:   pool autotune: cold-start seed from live pg_stat_activity (idle=180) → target 250/50 (ceiling=300)
Startup migration:   pgbouncer pool-size: set docker-compose.yml DEFAULT_POOL_SIZE None → 250, RESERVE_POOL_SIZE None → 50 (ceiling=300, reason=cold-start with live pg_stat_activity seed (samples=0, ring buffer empty); peak=180 × safety=2.0 = 360; floor=90; cap=300 (hit); split default:reserve = 250:50 (ceiling=300))
```

Settings.json gets the audit trail:

```json
{
  "pool_autotune": {
    "samples": [/* last 30 */],
    "last_decision": {
      "computed_at": "2026-05-17T20:30:00Z",
      "version": "0.9.27-alpha",
      "samples_seen": 30,
      "peak_observed": 87,
      "peak_at": "2026-05-17T20:14:00Z",
      "target_ceiling": 170,
      "target_default": 142,
      "target_reserve": 28,
      "capped_at_pg_max": false,
      "reason": "autotune: 30 samples; peak=87 × safety=2.0 = 174; ..."
    },
    "last_applied": {
      "default": 142, "reserve": 28,
      "applied_at": "2026-05-17T20:30:00Z",
      "noop": false, "version": "0.9.27-alpha",
      "from": {"default": 250, "reserve": 50}
    }
  }
}
```

## Multi-box validation gate (MANDATORY before main merge)

Per `.cursor/rules/fleet-uniform-config.mdc`. No exceptions on this release.

### Pre-flight (each box)

- [ ] Box's update channel set to `dev` (footer toggle, password-gated).
- [ ] Box's current state captured: `git rev-parse --short HEAD`, VERSION,
      compose pool size, settings.json `channels_pool_watchdog_threshold`,
      docker ps healthy/unhealthy.
- [ ] No operator overrides should remain in compose YAML or settings.json
      that are EXPECTED to persist across this validation — the test is
      "what does the autotuner produce on this box."

### Validation steps

| # | Action | Pass criteria |
|---|---|---|
| 1 | Push to dev | Tag NOT pushed; commit only on `dev` branch |
| 2 | test8: Update Now from dev | Migration log shows: autotune compute, pgbouncer pool set/shrunk, watchdog threshold reconciled; 6/6 containers healthy within 3 min |
| 3 | responder: Update Now from dev | Same criteria as test8 |
| 4 | tak-10: Update Now from dev | **CRITICAL:** Cold-start seed picks up live ~180 idle, lands at 250/50, watchdog threshold stays at 350. Operator override is no longer "preserved" — it's been replaced by an autotune decision that happens to converge to the same value because peak load justifies it |
| 5 | 60-min soak (all 3 boxes) | Zero `query_wait_timeout`, zero `context canceled`, zero `ak-pg-watchdog ALERT`, all 6 containers healthy, `settings.pool_autotune.samples` accumulates ≥ 30 entries |
| 6 | Verify autotune evidence | Each box's `settings.json.pool_autotune.last_decision.reason` reflects observed load on THAT box; no two boxes have a config that differs from what the autotuner produces for their respective load |
| 7 | Selective merge to main | `README.md`, `app.py`, `docs/RELEASE-v0.9.27-alpha.md`, `memory-bank/techContext.md`, `.cursor/rules/fleet-uniform-config.mdc` in a single squash on top of `v0.9.26-alpha`. Tag `v0.9.27-alpha` |

### Rollback plan

If validation fails on any box:

- `git checkout v0.9.26-alpha` on the affected box (and operator restores their
  manual pool override).
- File a forensic note in `docs/RELEASE-v0.9.27-alpha.md` describing the
  failure mode.
- Do NOT merge to `main` until the failure is understood and reproduced on
  a second box.

## Files modified

- `app.py`:
  - VERSION `0.9.26-alpha` → `0.9.27-alpha`
  - New constants: `_AUTHENTIK_POOL_AUTOTUNE_*` near existing pgbouncer constants
  - New: `_authentik_pool_autotune_seed_from_live_pg()`,
    `_authentik_pool_autotune_sample()`,
    `_authentik_pool_autotune_compute()`
  - Rewritten: `_ensure_authentik_pgbouncer_pool_size()` — no more
    `max(cur, target)`
  - Modified: `_authentik_channels_pool_watchdog_loop` — `_classify_idle_load()`
    runs on EVERY tick (was alert-paths only), output fed to autotune sampler
- `.cursor/rules/fleet-uniform-config.mdc` — new rule pinning the principle
- `docs/RELEASE-v0.9.27-alpha.md` — forensic + design (separate file)
- `docs/PLAN-v0.9.27-alpha.md` — this document
- `README.md` — latest-release pointer + changelog entry
- `memory-bank/techContext.md` — v0.9.27 roadmap entry, override-preservation
  as antipattern

## Lessons baked into this release

1. **Operator overrides are temporary, not architectural.** If a customer
   needs a different value, the codebase has to learn the new value across
   the whole fleet. No silent per-box drift.
2. **Validation must happen on a box where runtime config == codebase
   output.** Validating on a tuned box validates the tune, not the code.
3. **Multi-box dev validation before main.** Single-box validation
   per-release is forbidden for any change that ships fleet-wide
   configuration. See `.cursor/rules/fleet-uniform-config.mdc`.
