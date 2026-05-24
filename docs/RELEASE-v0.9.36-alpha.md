# v0.9.36-alpha — Authentik watchdog blind-spot fix: idle-in-transaction storm detection + server-health auto-recovery

**Date:** 2026-05-21
**Type:** Hotfix release — drop-in update via Update Now.
**Status:** RELEASED to `main` 2026-05-21. Validated on test6 / test8 / test12 (SHA `2cc833e`, ~26 min soak, operator-authorized early gate). Live watchdog proof on test12: fired first tick, cleared 80 idle-in-tx to 0.

---

## TL;DR

Two gaps in the Authentik PG watchdog exposed by a live incident on tak-10 (2026-05-21 02:30 UTC):

1. **Watchdog SQL monitored `state='idle'` only** — the `state='idle in transaction'` class (asgiref `CancelledError` mid-transaction storm) was completely invisible. 130 abandoned transactions were accumulating while the watchdog saw 49 idle connections against a threshold of 150. SAFETY NET never fired.

2. **No health-check watchdog** — when `authentik-server-1` went `(unhealthy)` (health probe timeout caused by the saturated thread pool), nothing auto-recovered it. The LDAP outpost got flooded with 502s, panicked with nil-pointer dereferences, and restarted in a loop.

No operator action required on update.

---

## What went wrong on tak-10 (2026-05-21)

**The failure chain:**
1. The LDAP outpost container restarted (Docker restart policy after a prior panic).
2. TAK Server reconnected and flooded bind requests into the recovering Authentik server.
3. `authentik/enterprise/middleware.py` hits `django_postgres_cache_cacheentry` on **every request** via the async-to-sync bridge (`asgiref`). Community Authentik never populates the enterprise license cache key — guaranteed DB miss.
4. Caddy's upstream timeout fired on slow requests (PG under load). `CancelledError` propagated through `asgiref`'s thread pool **before Django committed the open transaction** — leaving connections in `state='idle in transaction'`.
5. 130 idle-in-transaction connections accumulated. PgBouncer transaction-mode holds server connections until the transaction closes — so PgBouncer pool appeared healthy (`cl_waiting=0`) while PG itself was at 186% CPU.
6. The watchdog query (`WHERE state='idle'`) saw 49 connections (well under threshold 150) — **never fired**.
7. Server health check timed out (`/-/health/live/` probe) → `authentik-server-1` went `(unhealthy)`.
8. LDAP bind requests returned 502 → LDAP Go code nil-pointer panic → LDAP restart → TAK reconnects → more requests → cycle amplifies.

**Why the existing protections didn't catch it:**
- `idle_in_transaction_session_timeout=300s` — was draining connections after 5 min, but the faucet (new cancelled requests) was faster than the drain.
- PgBouncer transaction-mode — correctly limited `cl_waiting=0` (no queue), but can't reclaim connections that are in-flight inside an open transaction.
- MAX_REQUESTS=1000 — recycles workers after N requests, but doesn't help when threads are abandoned mid-request via `CancelledError`.
- Watchdog — was monitoring the wrong PG state (`idle` vs `idle in transaction`).

**Why `idle_in_transaction_session_timeout` was NOT lowered:**
The v0.8.8 release documented a real production crash-loop on SSDNodes (1795 IOPS): at 30s, Django startup migrations were killed mid-flight on slow storage, leaving stale advisory locks and causing the server to crash-loop on `waiting to acquire database lock`. 300s is the documented safe value. The watchdog restart is the correct response — not a tighter timeout.

---

## Fix 1 — Watchdog: add `idle in transaction` monitoring

**File:** `app.py` — `_authentik_channels_pool_watchdog_loop()`

**Before:** Single psql query: `WHERE state='idle'` → one count → one threshold. The `idle in transaction` class was invisible.

**After:** Single round-trip dual-count query:
```sql
SELECT
  COUNT(*) FILTER (WHERE state='idle'),
  COUNT(*) FILTER (WHERE state='idle in transaction')
FROM pg_stat_activity WHERE datname='authentik'
```

New constant: `_AUTHENTIK_IDLE_IN_TX_WATCHDOG_THRESHOLD = 60`

- Well above the healthy post-COMMIT transient peak (~46 observed in AnchorTAK forensic, May 2026)
- Far below the storm level (130+) that causes server health timeouts
- Configurable via `channels_pool_watchdog_idle_in_tx_threshold` in settings.json

When `idle_in_transaction` count exceeds threshold: same action as existing watchdog — `docker restart authentik-server-1`. This flushes gunicorn's thread pool, clearing all abandoned asgiref threads and their open transactions immediately.

The `idle` watchdog and its threshold are unchanged — it still guards against the original connection-leak class (upstream Authentik #20714).

All existing log messages now include `idle_in_tx=N` for operator visibility.

---

## Fix 2 — Watchdog: server-health auto-recovery

**File:** `app.py` — `_authentik_channels_pool_watchdog_loop()`

New `_consecutive_unhealthy` counter. Each tick: `docker inspect authentik-server-1 --format '{{.State.Health.Status}}'`.

- If `unhealthy` for **2 consecutive ticks** (~4 min of watchdog observation, on top of Docker's own retry cycle ≈ 90s) → `docker restart authentik-server-1`.
- Counter resets to 0 on any non-unhealthy result or after restart.
- Wrapped in `try/except` — never blocks the main `idle`/`idle_in_tx` safety nets.

The 2-tick requirement prevents restart on transient single-probe failures (e.g. a brief PG query spike). By the time the watchdog sees 2 consecutive `unhealthy` readings, the server has been failing health checks for ~6+ minutes — a restart is definitively warranted.

---

## What was NOT changed (and why)

| Setting | Value | Reason unchanged |
|---|---|---|
| `idle_in_transaction_session_timeout` | 300s | Lowering risks migration crash-loop on slow disks (v0.8.8 history). Watchdog restart is the correct mitigation. |
| `idle_session_timeout` | 300s | Kills LISTEN sockets — documented regression from v0.9.21. |
| PgBouncer pool size | 750/150 | Fleet constant. Not related to this failure class. |
| `AUTHENTIK_WEB__MAX_REQUESTS` | 1000 | Still correct. Recycles workers by request count; doesn't help mid-request CancelledError but remains defense-in-depth for memory. |

---

## Validation plan

- [ ] **All 3 boxes (test6, test8, test12):** `journalctl -u takwerx-console --since '5 min ago' | grep 'ak-pg-watchdog'` — confirm log lines include `idle_in_tx=N` (proves the new dual-count query is live).
- [ ] **All 3 boxes:** Console journal shows `VERSION = "0.9.36-alpha"` at startup.
- [ ] **All 3 boxes:** No migration tracebacks in journal at restart.
- [ ] **All 3 boxes:** Authentik `idle in transaction` PG connections ≤ 3 at T+60 min (the metric the watchdog now monitors).
- [ ] **All 3 boxes:** All Authentik containers `(healthy)` at T+60 min.
- [ ] **All 3 boxes:** Zero new watchdog ALERTs above baseline in T&E window.
- [ ] **Code-only (no operator action needed):** `idle_in_tx` threshold branch — cannot be operator-triggered in normal T&E; validated by the v0.9.36 threshold constant (`60`) and confirmed via journal grep showing `idle_in_tx=N` in the tick log.
- [ ] **Code-only:** Health-check watchdog — cannot be operator-triggered without inducing an unhealthy server state; reviewed in code and confirmed correct by inspection.

---

## Acceptance test

After `Update Now` on a test box:

```bash
# Confirm watchdog now counts idle_in_tx
journalctl -u takwerx-console | grep 'ak-pg-watchdog' | tail -5
# Should show: idle_in_tx=N in log lines

# Confirm server health fires watchdog (manual test — requires inducing unhealthy)
# Expected: after 2 watchdog ticks of (unhealthy), journal shows:
# [ak-pg-watchdog] ALERT: authentik-server-1 has been (unhealthy) for 2 consecutive ticks

# Confirm idle_in_tx threshold fires (manual test — set threshold low in settings.json)
# channels_pool_watchdog_idle_in_tx_threshold: 1
# Expected: [ak-pg-watchdog] ALERT: N idle-in-transaction PG connections ... SAFETY NET firing.
```

---

## References

- `docs/UPSTREAM-AUTHENTIK-PG-LEAK-20714.md` — AnchorTAK forensic (confirms `idle_session_timeout=30s` kills LISTEN sockets; validates 300s idle_in_tx timeout)
- `goauthentik/authentik#20714` — upstream confirmed bug (enterprise license cache miss on every request)
- tak-10 incident 2026-05-21 02:30 UTC — live observation of the failure chain
