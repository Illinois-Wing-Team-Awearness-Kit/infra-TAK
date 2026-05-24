# v0.9.22-alpha — Authentik hotfix: remove idle-session regression + make proxy host canonicalization converge on Update Now

**Date:** 2026-05-14  
**Type:** Hotfix release — drop-in update via Update Now.

---

## TL;DR

`v0.9.22-alpha` is a production hotfix for two regressions introduced in `v0.9.21-alpha`:

1. **Authn CPU/connectivity regression from `idle_session_timeout=30s`**  
   `v0.9.21-alpha` reintroduced `idle_session_timeout` (at 30s) as a server-side cleanup lever for idle connection accumulation. Field validation on tak-10 proved this is unsafe for Authentik 2026.2.3: Postgres treats `django_channels_postgres` LISTEN sockets as idle and kills them, causing continuous reconnect storms (`Postgres connection is not healthy`, `IdleSessionTimeout`) and large CPU spikes on server/worker/postgres.  
   **Fix in v0.9.22:** remove `idle_session_timeout` again from all compose target commands/patchers and enforce `SHOW idle_session_timeout = 0` as the runtime target.

2. **Proxy URL regression (`nodered.<fqdn>` / `takportal.<fqdn>` returning Authentik 404)**  
   Some boxes still retained non-canonical Authentik proxy provider hosts (`external_host=https://taktical.<fqdn>`), so requests to canonical service hosts landed in Authentik but failed host match routing.  
   **Fix in v0.9.22:** strengthen canonicalization path:
   - retry-aware API canonicalization in startup/post-update
   - re-run canonicalization after Authentik reconfigure and API-ready check
   - **new `ak shell` ORM fallback** when API PATCH cannot converge
   - persist result in settings for support audit.

Validated on tak-10 via Update Now flow (no manual field patching required):  
- `SHOW idle_session_timeout` => `0`  
- `Node-RED Proxy` => `https://nodered.test12.taktical.net/`  
- `TAK Portal Proxy` => `https://takportal.test12.taktical.net/`  
- runtime stable (no restart churn; active=1, idle=32; low steady CPU).

---

## Regression 1 — Why `idle_session_timeout=30s` failed

### What changed in v0.9.21

`idle_session_timeout=30s` was added to Authentik Postgres command line to cap idle session accumulation.

### What field logs showed

On tak-10, after update:

- repeated `django_channels_postgres.layer` warnings: `Postgres connection is not healthy`
- repeated ASGI warnings: `IdleSessionTimeout('terminating connection due to idle-session timeout')`
- bursts every ~30s across multiple worker pids
- high CPU spikes across Authentik server/worker/postgres

This proved `idle_session_timeout` was terminating healthy LISTEN sockets, not just abandoned web/worker connections.

### v0.9.22 fix

Removed `idle_session_timeout` from all relevant paths:

- fresh compose template command line
- legacy text patcher `pg_cmd`
- YAML parse-and-mutate `_pg_target_cmd`
- drift detection now treats **presence** of `idle_session_timeout=*` as drift and removes it

Runtime verification added to migration:

- after Postgres recreate + server/worker restart, migration checks:
  `SHOW idle_session_timeout`
- logs explicit pass when value is `0`
- records `last_runtime_idle_session_timeout` and structured outcome in `settings.authentik_pg_idle_timeout_fix`

---

## Regression 2 — Why canonical service URLs returned Authentik 404

### Symptom

`https://nodered.<fqdn>` and `https://takportal.<fqdn>` rendered Authentik "Not Found" pages.

### Root cause

Proxy provider records remained:

- `Node-RED Proxy external_host = https://taktical.<fqdn>`
- `TAK Portal Proxy external_host = https://taktical.<fqdn>`

Host-based provider matching failed for canonical service hosts.

### v0.9.22 convergence hardening

`_ensure_authentik_proxy_external_hosts_canonical(...)` now:

1. supports retry attempts + delay (`max_attempts`, `retry_delay_s`) to handle Authentik API warm-up races
2. persists outcome in settings (`authentik_proxy_external_hosts_canonicalization`)
3. when API path fails/partially fails, executes **`ak shell` ORM fallback** inside `authentik-server-1` to patch provider `external_host` and `cookie_domain`
4. reports fallback outcome (`fixed-via-ak-shell`) in audit state

Call sites updated:

- startup migration uses retries (`12 x 5s`)
- post-update auto-authentik path re-runs canonicalization after Authentik reconfigure and `_wait_for_authentik_api(...)` readiness.

---

## What stayed the same

- `max_connections=500` retained
- `statement_timeout=120s` retained
- `idle_in_transaction_session_timeout=300s` retained
- `AUTHENTIK_POSTGRESQL__CONN_MAX_AGE=10` retained
- channels watchdog retained as secondary safety net

---

## Verify on a box after Update Now

```bash
# 1) Runtime DB knobs
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA -c "SHOW idle_session_timeout"
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA -c "SHOW statement_timeout"
docker exec authentik-server-1 printenv AUTHENTIK_POSTGRESQL__CONN_MAX_AGE
# expected: 0, 2min, 10

# 2) Proxy hosts canonical
docker exec authentik-server-1 ak shell -c "
from authentik.providers.proxy.models import ProxyProvider
for p in ProxyProvider.objects.all():
    print(p.name, p.external_host, p.cookie_domain)
"
# expected:
# Node-RED Proxy https://nodered.<fqdn>/
# TAK Portal Proxy https://takportal.<fqdn>/

# 3) No restart churn
docker inspect -f 'server={{.RestartCount}} worker={{.RestartCount}} pg={{.RestartCount}}' \
  authentik-server-1 authentik-worker-1 authentik-postgresql-1
# expected: stable / unchanged
```

---

## Notes

- This hotfix intentionally prioritizes **Update Now convergence** over “clean abstraction”: fallbacks are in place so field operators are not forced into manual shell fixes.
- Long-term, upstream Authentik behavior around async connection lifecycle and proxy API consistency should still be tracked.
- Reference upstream issue context for connection pressure patterns: [goauthentik/authentik#20714](https://github.com/goauthentik/authentik/issues/20714).

