# v0.8.7-alpha Release Notes

## Headline: fix the v0.8.2 silent-ignore env var bug + apply official Authentik tunings

> **The bug we've been carrying for five releases:** Since v0.8.2 (late April 2026), every box in the fleet has been running with **half the gunicorn workers we thought**, because we wrote `AUTHENTIK_WEB_WORKERS=4` (single underscore) to `~/authentik/.env` and Authentik 2026.x silently ignored it. The correct name is `AUTHENTIK_WEB__WORKERS` (double underscore) per the [official Authentik docs](https://docs.goauthentik.io/install-config/configuration/) — *"the double-underscores are intentional."* On Apr 30 2026, `docker top authentik-server-1 | grep -c 'gunicorn: worker'` reported `2` on tak-10 despite our config saying `4`. `ak dump_config` confirmed cache and `log_level` were also at defaults — never tuned. v0.8.7 fixes all of this and adds a runtime-config verifier so we can never have this silent-default scenario again.

The Apr 30 2026 investigation started chasing CPU drift on `tak-10` (server p50 140%+ vs sibling box at p50 1.9%, identical hardware and config). We tried postgres `VACUUM`, autovacuum tuning, worker bumps, worker reverts, ASGI WebSocket loop fixes, runtime state drift theories — band-aid after band-aid. The breakthrough came when the operator pushed back: *"is there any info on the internet about optimizing authentik? I feel like we are doing all this half-ass shit."* That sent us to the official docs for the first time, where the very first sentence explained why every config we'd been writing was being ignored.

**Result on tak-10 with real 351-bind workload during a 3-min soak:** server CPU p50 dropped from ~99% to **2.1%** (~47x reduction), Postgres CPU p50 dropped from ~94% to **0.0%**, all with the LDAP outpost untouched.

---

## Changes

### 1. Fix the v0.8.2 silent-ignore bug + apply official tunings

**The bug:** Since v0.8.2, the post-update migration in `_post_update_auto_deploy` wrote `AUTHENTIK_WEB_WORKERS=4` (single underscore) to `~/authentik/.env`. Authentik 2026.x reads `AUTHENTIK_WEB__WORKERS` (double underscore). The single-underscore form was silently ignored on every box for five releases.

**The proof (tak-10, Apr 30 2026, before fix):**

```bash
$ grep AUTHENTIK_WEB ~/authentik/.env
AUTHENTIK_WEB_WORKERS=4

$ docker top authentik-server-1 | grep gunicorn
gunicorn: master [authentik.root.asgi:application]
gunicorn: worker [authentik.root.asgi:application]   ← worker 1
gunicorn: worker [authentik.root.asgi:application]   ← worker 2
                                                     ← TWO workers, not four

$ docker exec authentik-worker-1 ak dump_config | python3 -c \
  "import json,sys; o=sys.stdin.read(); cfg=json.loads(o[o.find('{'):]); \
   print(cfg.get('cache'), cfg.get('log_level'))"
{'timeout': 300, 'timeout_flows': 300, 'timeout_policies': 300} info
```

Every cache setting at default. `log_level=info` (default). Worker count 2 (default). Every official optimization, never applied.

**New function: `_authentik_apply_official_tunings(plog)`** in `app.py`. Idempotent. Edits `~/authentik/.env`:

- **Removes** `AUTHENTIK_WEB_WORKERS=N` lines (the single-underscore wrong name; was being ignored).
- **Adds** `AUTHENTIK_WEB__WORKERS=4` (correct double-underscore name; finally honored — doubles capacity from default 2).
- **Adds** `AUTHENTIK_CACHE__TIMEOUT_FLOWS=600` (2x default 300s — flows rarely change; reduces DB pressure).
- **Adds** `AUTHENTIK_CACHE__TIMEOUT_POLICIES=600` (2x default 300s — policies rarely change; reduces DB pressure).
- **Adds** `AUTHENTIK_LOG_LEVEL=warning` (down from default `info` — reduces log overhead and Postgres write pressure on busy boxes).

Only adds keys that are **missing**. Never overwrites operator-set values. Records actions taken to `settings.authentik_official_tunings`.

Hooked into both:

- **`_startup_migrations`** — runs on every console startup. Function self-gates (returns False on subsequent runs when no changes needed), so the recreate fires only once per box.
- **`_post_update_auto_deploy`** — replaced the v0.8.2 migration block. Fresh deploys get the correct config from the first Update Now.

After applying changes, `_recreate_authentik_server_worker(reason='official-tunings-migration')` fires automatically (10-15s blip; `ldap` outpost untouched).

### 2. Runtime config verifier (closes the audit loop)

**New function: `_authentik_verify_runtime_config(plog)`** in `app.py`. The lesson from the silent-ignore bug: **never trust `.env` to mean Authentik is using those settings**. Verify what's actually loaded at runtime.

Two probes:

1. **`docker exec authentik-worker-1 ak dump_config`** — parses JSON output, asserts:
   - `cache.timeout_flows == 600`
   - `cache.timeout_policies == 600`
   - `log_level == "warning"`
2. **`docker top authentik-server-1`** — counts processes whose CMD contains `gunicorn: worker`. Asserts count `== 4`. (`web.workers` is consumed by Authentik's launcher script, not visible in `dump_config`, so we have to count processes.)

Persists pass/fail per check to `settings.authentik_runtime_config_check`. Runs on every console startup AND after every recreate. If any check fails, the operator gets a clear log line in journalctl.

### 3. Single source of truth for the recreate operation

**Helper: `_recreate_authentik_server_worker(plog, reason)`** in `app.py`.

Used by callers that just changed env vars (gunicorn re-reads `.env` only at process startup, so editing `.env` alone is a no-op until containers are recreated). Runs:

```bash
cd ~/authentik && docker compose up -d --force-recreate --no-deps server worker
```

Note `--no-deps` — recreate ONLY `server` and `worker`. `ldap`, `postgresql`, `redis` (if present) stay up. The LDAP outpost's bind cache is preserved; no thundering herd on dependent TAK clients. **This `--no-deps` flag is non-negotiable.**

Records outcome to `settings.authentik_last_recreate` for operator visibility.

---

## What was explicitly NOT shipped (or built then deleted before ship)

- ❌ **Console UI / button.** Operator was explicit: *"I just want an update to work for now. No UI changes."*
- ❌ **Daily 04:00 periodic restart of `server`+`worker`.** *Built then deleted.* Was a band-aid built on the wrong "state drift" theory. Once the real fix is in, it's unnecessary noise.
- ❌ **Reactive ASGI WebSocket loop detector + auto-recreate.** *Built then deleted.* The ASGI loop we observed on tak-10 was a downstream symptom of worker starvation; with 4 workers + 600s cache it doesn't recur.
- ❌ **Admin API safety gate** (deferring scheduled restart while admins are mid-form). *Built then deleted.* Existed only because of the periodic restart; with no scheduled restart, no gate needed.
- ❌ **Permanent autovacuum tuning ALTER TABLE.** Today's manual tuning was a red herring; default autovacuum is fine once CPU isn't starved.

**Net deletion: ~200 lines of code that were treating symptoms.** v0.8.7 ships with only the changes that address the root cause.

---

## Cursor rule shipped with this release

`.cursor/rules/consult-upstream-docs.mdc` (alwaysApply) institutionalizes the lesson:

> **Read official docs before chasing symptoms. Never trust a `.env` value to mean the runtime is using it. Always verify with the project's introspection command** (`ak dump_config`, `caddy adapt`, `psql -c "SHOW ALL"`, `docker top`, etc.).

This is what would have prevented the v0.8.2 silent-ignore bug from surviving five releases. Future maintainers (and Cursor agents) see this guidance every session.

---

## Validation (tak-10, Apr 30 2026)

| Metric | v0.8.6 baseline | v0.8.7 (env var fix) |
|---|---|---|
| `authentik-server-1` CPU p50 | ~99% | **2.1%** |
| `authentik-server-1` CPU p95 | ~140% | 62.3% (transient flow execution bursts) |
| `authentik-server-1` CPU avg | ~150% | 9.9% |
| `authentik-postgresql-1` CPU p50 | ~94% | **0.0%** |
| `authentik-postgresql-1` CPU p95 | ~120% | 8.5% |
| Gunicorn workers (`docker top`) | 2 | **4** |
| `ak dump_config` cache.timeout_flows | 300 (default) | **600** |
| `ak dump_config` cache.timeout_policies | 300 (default) | **600** |
| `ak dump_config` log_level | info (default) | **warning** |
| LDAP outpost `StartedAt` | — | unchanged (recreate didn't touch it) |
| Bind volume during 3-min soak | — | 351 binds (real workload) |

~47x reduction at p50 with identical workload. Postgres effectively idle.

---

## Operator acceptance checklist

After Update Now or `git pull origin dev` + console restart:

- [ ] `grep AUTHENTIK_ ~/authentik/.env` shows the four correct double-underscore lines and **no** legacy `AUTHENTIK_WEB_WORKERS=` line.
- [ ] `docker top authentik-server-1 2>/dev/null | grep -c 'gunicorn: worker'` returns `4`.
- [ ] `docker exec authentik-worker-1 ak dump_config | python3 -c "import json,sys; o=sys.stdin.read(); c=json.loads(o[o.find('{'):]); print(c.get('cache'), c.get('log_level'))"` shows `timeout_flows: 600`, `timeout_policies: 600`, `warning`.
- [ ] `cat ~/.takwerx/settings.json | python3 -m json.tool | grep -A 10 authentik_runtime_config_check` shows `last_outcome: "pass"` and all four checks passing.
- [ ] LDAP outpost `docker inspect authentik-ldap-1 --format '{{.State.StartedAt}}'` is unchanged from before the upgrade (recreate touched only `server`+`worker`).

---

## Diagnostic commands

```bash
# What does the runtime audit say?
cat ~/.takwerx/settings.json | python3 -c \
  "import json,sys; print(json.dumps(json.load(sys.stdin).get('authentik_runtime_config_check', {}), indent=2))"

# What tunings were applied (and when)?
cat ~/.takwerx/settings.json | python3 -c \
  "import json,sys; print(json.dumps(json.load(sys.stdin).get('authentik_official_tunings', {}), indent=2))"

# When was the last recreate, what was the reason?
cat ~/.takwerx/settings.json | python3 -c \
  "import json,sys; print(json.dumps(json.load(sys.stdin).get('authentik_last_recreate', {}), indent=2))"

# Re-run the verifier manually (no recreate; just audits running config):
docker exec authentik-worker-1 ak dump_config 2>/dev/null | python3 -c \
  "import json,sys; o=sys.stdin.read(); c=json.loads(o[o.find('{'):]); \
   print('cache:', c.get('cache')); print('log_level:', c.get('log_level'))"
docker top authentik-server-1 2>/dev/null | grep -c 'gunicorn: worker'

# Force the migration to re-run if you're testing:
rm -f /tmp/takwerx-spiral-monitor.lock
sudo systemctl restart takwerx-console
journalctl -u takwerx-console --since "1 min ago" | grep -E "Startup migration|authentik (recreate|config verify)"
```

---

## What's preserved from prior releases

- **`_authentik_spiral_monitor`** (v0.8.5) — the 10-min reactive LDAP routing repair monitor still runs unchanged. The ASGI WebSocket loop pass that was added in an earlier draft of v0.8.7 was removed before ship.
- **Proactive FQDN routing migration** (v0.8.5) — unchanged.
- **Gunicorn worker timeout `--timeout=120`** (v0.8.5) — unchanged.
- **LDAP SA bind verifier** (v0.8.6) — unchanged.
- **`idle_in_transaction_session_timeout=30s`** (v0.8.3) — unchanged.

---

## Known limitations

- **One-time migration window.** On the upgrade, the console restart triggers `_authentik_apply_official_tunings`, which then triggers `_recreate_authentik_server_worker`. The `server`+`worker` containers cycle for ~10-15s. The LDAP outpost is unaffected (preserves bind cache). Subsequent console restarts are no-ops because the migration self-gates.
- **`AUTHENTIK_WEB__WORKERS=4` is fleet-wide.** Operators on small/light boxes who want fewer workers can edit `~/authentik/.env` manually; the migration only adds keys that are missing, so a manually-set value is preserved across upgrades.
