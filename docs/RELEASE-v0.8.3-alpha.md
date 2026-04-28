# v0.8.3-alpha Release Notes

## Bug Fixes

### Fix: reduce `idle_in_transaction_session_timeout` from 120s to 30s to prevent Postgres exhaustion

**Problem:** Authentik 2026.2.2 checks the enterprise license cache on every flow executor request, leaving database transactions open after the cache query instead of committing them cleanly. With the default PostgreSQL command-line setting of `idle_in_transaction_session_timeout=120s`, these leaked transactions pile up faster than they time out. On installs with 4+ Authentik workers this causes hundreds of `idle in transaction` connections to accumulate, exhausting the Postgres connection pool (max 300), spiking Postgres CPU to 500–800%, and consuming gigabytes of RAM — even on boxes with few or no active TAK clients.

**Symptoms:**
- Postgres CPU stuck at 500–800%+ with no client activity
- `pg_stat_activity` shows 150–228 connections all in `idle in transaction` state, all running `SELECT ... FROM django_postgres_cache_cacheentry WHERE cache_key IN ('public::1:goauthentik.io/enterprise/license')`
- Authentik server unresponsive or very slow despite no LDAP bind storms
- Killing idle-in-transaction connections manually only provides temporary relief — they rebuild within 2 minutes

**Fix:** The PostgreSQL `idle_in_transaction_session_timeout` command-line argument is reduced from `120s` to `30s`. This causes Postgres to auto-kill any connection that sits idle inside an open transaction for more than 30 seconds — 4× more aggressive than before, yet safe for Authentik's startup migration lifecycle (which holds a transaction open during module loading and can have idle gaps of 10–20s). With 4 workers this limits the license-check leak to ~30–60 concurrent idle connections instead of 228.

The `Update Config` / post-deploy flow now also force-recreates the PostgreSQL container when the command-line args change (required since command-line args cannot be applied with a config reload alone).

**Who is affected:**
- All installs on any version with `idle_in_transaction_session_timeout=120s` in `~/authentik/docker-compose.yml`
- The migration runs automatically on **Update Now** and **Update Config** — no operator action required
- PostgreSQL will briefly restart during the migration (~5 seconds). TAK clients will reconnect automatically.

**Recovery for operators currently stuck:**

Just run **Update Now** — the migration will update the compose file and recreate the PostgreSQL container automatically.

> If your console is unreachable, use the backdoor at `https://<server-IP>:5001`

| File | Change |
|------|--------|
| `app.py` | `idle_in_transaction_session_timeout` reduced from `120s` to `30s` in all compose templates |
| `app.py` | `needs_pg_update` migration condition now also triggers on installs with `120s` |
| `app.py` | `_apply_authentik_pg_tuning()` force-recreates postgresql container when compose args change |
| `app.py` | VERSION bumped to 0.8.3-alpha |
