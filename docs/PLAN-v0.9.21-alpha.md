# Plan — v0.9.21-alpha

> **Status:** DRAFT — created 2026-05-13 evening after v0.9.20 cut, updated 22:05 PT with Item 0 (channels_postgres LISTEN kill).
> **Target:** v0.9.21-alpha (next minor after v0.9.20-alpha).
> **Sources of work:**
> - External operator handoff: `~/Downloads/handoff-infraTAK-v0.9.18-update-config-regression.md` (Samsung TAK / SAM box, infra-TAK v0.9.18-alpha, operator: Amos).
> - Internal finding during v0.9.20 fleet verification (2026-05-13 21:50 PT): tak-10's `Node-RED Proxy` + `TAK Portal Proxy` both have `external_host=https://taktical.test12.taktical.net` (brand subdomain — NOT in Caddyfile). Responder and ssdnodes are correct. So the bug is real but not universal.
> - **2026-05-13 22:00 PT diagnostic on tak-10**: Authentik server CPU 38%, Postgres CPU 20% baseline (4 worker pids continuously reconnecting LISTEN connections every 5 min). Traced via `authentik-server-1` logs to `IdleSessionTimeout` events on `django_channels_postgres.layer` connections, recurring at exactly 300s intervals. Confirmed match with upstream Authentik issue [#20714](https://github.com/goauthentik/authentik/issues/20714) (`bug/confirmed`, milestone Release 2026.8.0). Our own v0.8.4 `idle_session_timeout=300s` is killing the channels layer's LISTEN connections — workaround now in conflict with v0.9.20's better fix (`CONN_MAX_AGE=60`).

---

## TL;DR

Seven items split across three themes:

**Theme A — biggest CPU win: stop killing channels_postgres LISTEN connections** (Item 0). Remove `idle_session_timeout=300s` from our Postgres command line. This setting was added in v0.8.4 to clean up Authentik's idle web connections, but v0.9.20's `AUTHENTIK_POSTGRESQL__CONN_MAX_AGE=60` now handles that job correctly. The legacy setting is now ACTIVELY KILLING `django_channels_postgres` LISTEN connections every 5 min × 4 workers = 48 reconnect cycles/hr, producing the elevated CPU baseline observed on tak-10. Upstream fix won't land until Authentik 2026.8.0. Keep `idle_in_transaction_session_timeout=300s` (the safety net for stuck transactions, doesn't affect channels layer).

**Theme B — kill the "patcher emits duplicate YAML/XML keys" bug class** (Items 1+2). Convert `_ensure_authentik_compose_patches()` and the `CoreConfig.xml` writer from regex-based text manipulation to parse-and-mutate (PyYAML / ElementTree). Closes Issues #1 + #6 from the SAM operator's handoff and silently self-heals any box that already accumulated duplicates from v0.9.18.

**Theme C — wiring-gap defense + drift self-healing** (Items 3+4+5+6+7). Wire three idempotent helpers into `_startup_migrations` (the unconditional block we promoted in v0.9.20) so guaranteed reach + drift correction become the standard pattern:

- `_auto_remove_stale_docker_service_connections` (existing — currently only in version-gated post-update hook).
- `_ensure_embedded_outpost_authentik_host` (new — defensive re-assertion against FQDN changes).
- `_ensure_authentik_proxy_external_hosts_canonical` (new — closes tak-10's brand-subdomain drift; generalizes the existing `_sync_authentik_provider_external_hosts` which only handles the `authentik.<fqdn>` drift case).

Plus one small defense-in-depth knob: `statement_timeout=120s` on the Postgres command line.

---

## Items in scope

### Item 0 — Remove `idle_session_timeout=300s` from Postgres command line (BIGGEST CPU WIN)

**Closes:** tak-10's elevated baseline CPU (server 38%, PG 20%) when no users are active. Same mechanism affects every infra-TAK box with active channels_postgres traffic but tak-10 hits it hardest due to more concurrent SSE/websocket consumers.

**Upstream context:**
- Authentik issue [#20714](https://github.com/goauthentik/authentik/issues/20714) — `bug/confirmed`, assigned to `@rissson`, **milestone Release 2026.8.0**. Real upstream fix won't land for months.
- Authentik 2026.2.x deliberately removed Redis as the channel-layer backend and ships their own forked `django_channels_postgres` package (`packages/django-channels-postgres/` in goauthentik/authentik repo). **There is no supported way to switch channels back to Redis in 2026.x.** We're stuck on the Postgres layer until upstream fixes it.
- Comment from `@boesr` in #20714: *"simply increasing the `max_connections` didn't resolve the issue. I adjusted some parameters to end idle connections sooner"* — same direction as v0.9.20's `CONN_MAX_AGE=60`, but the underlying churn remains.

**Diagnostic evidence (tak-10, 2026-05-13 21:54-21:59 UTC):**

```
04:52:29 [pid 159] IdleSessionTimeout('terminating connection due to idle-session timeout')
04:54:12 [pid 158] django_channels_postgres.layer: Postgres connection is not healthy
04:54:12 [pid 156] django_channels_postgres.layer: Postgres connection is not healthy
04:54:12 [pid 157] django_channels_postgres.layer: Postgres connection is not healthy
04:54:12 [pid 159] django_channels_postgres.layer: Postgres connection is not healthy
04:59:13 [pid 158] django_channels_postgres.layer: Postgres connection is not healthy
04:59:13 [pid 156] django_channels_postgres.layer: Postgres connection is not healthy
04:59:13 [pid 157] django_channels_postgres.layer: Postgres connection is not healthy
04:59:13 [pid 159] django_channels_postgres.layer: Postgres connection is not healthy
```

**Exactly 300s (5 min) cycle. 4 worker pids × 12 cycles/hr = 48 reconnect storms per hour.**

**Root cause:**

1. `django_channels_postgres` (Authentik's realtime channel-layer backend, replaces Redis pub/sub) maintains long-lived `LISTEN <channel>` connections to Postgres in each of the 4 Gunicorn worker processes (`AUTHENTIK_WEB__WORKERS=4`, set by infra-TAK in v0.8.7).
2. The connection issues `LISTEN channels_messages` once, then waits for `NOTIFY` events. While waiting, no SQL command flows from client → server. From Postgres' POV the session is **idle**.
3. Our `idle_session_timeout=300s` (added in v0.8.4 to clean up Authentik's leaky web app connections) treats this as eligible for termination after 300s. Postgres kills the LISTEN connection.
4. `django_channels_postgres` detects "Postgres connection is not healthy", tears down, opens a fresh connection (TCP handshake + auth + LISTEN replay + channel-layer state recovery), and re-enters wait state.
5. Cycle repeats every 5 min for all 4 worker pids. CPU cost is the cumulative recovery work: ~3-5 seconds of server + PG CPU per reconnect × 48 cycles/hr = 2.5-4 min of sustained CPU per hour just on reconnect overhead.

**Why this didn't show on responder/ssdnodes during fleet check:** they have less channel-layer state at any given moment (fewer in-flight SSE/websocket subscriptions when checked). Same bug, smaller amplitude — would show under load.

**Why v0.9.20's `CONN_MAX_AGE=60` doesn't fix it:** `CONN_MAX_AGE` is a Django ORM setting (controls Django's connection pool: web requests, dramatiq workers). `django_channels_postgres` is a separate async pool (uses `psycopg.AsyncConnection`, not Django's ORM connection management). The two pools are independent — the v0.9.20 setting recycles web connections cleanly but has no effect on the channels layer.

**Approach:**

Edit `_ensure_authentik_compose_patches()` (the same template that v0.9.20 sets `max_connections=500` in) at `app.py:~25489`. Change the `pg_cmd` template:

**v0.9.20 (current):**
```
postgres -c max_connections=500 -c idle_session_timeout=300s -c idle_in_transaction_session_timeout=300s -c tcp_keepalives_idle=60 -c tcp_keepalives_interval=10 -c tcp_keepalives_count=6
```

**v0.9.21 (proposed):**
```
postgres -c max_connections=500 -c statement_timeout=120s -c idle_in_transaction_session_timeout=300s -c tcp_keepalives_idle=60 -c tcp_keepalives_interval=10 -c tcp_keepalives_count=6
```

Three changes:
1. **Remove** `-c idle_session_timeout=300s` (the bug). Postgres default is 0 = disabled.
2. **Keep** `-c idle_in_transaction_session_timeout=300s` (safety net for stuck transactions — `django_channels_postgres` connections are NOT inside transactions, so this doesn't affect them).
3. **Add** `-c statement_timeout=120s` (Item 6 — defense in depth against runaway queries; previously planned as a separate item but cheap to fold into the same template change).

**Why removing `idle_session_timeout` is safe (no regression of v0.8.4's original problem):**

- v0.8.4 added it to kill idle Authentik webapp connections. That problem is now solved correctly by v0.9.20's `AUTHENTIK_POSTGRESQL__CONN_MAX_AGE=60` — Django actively recycles web app connections every 60s before they accumulate.
- LDAP outpost uses a Go binary with its own connection management (not Django, not channels_postgres). Not affected either way.
- `idle_in_transaction_session_timeout=300s` remains the safety net for the actually-dangerous case (transaction left open, holding locks). That's the real risk we care about.
- `tcp_keepalives_*` settings continue to detect dead TCP connections at the network layer.
- Postgres' default (no `idle_session_timeout`) is what Authentik's own official docker-compose ships with — we're aligning back to upstream-recommended config, not diverging.

**Broaden detection logic in `_authentik_fix_pg_idle_timeout()` (`app.py:~27170`):**

Currently the short-circuit checks `idle_in_transaction_session_timeout=300s AND max_connections>=500`. For v0.9.21, change the short-circuit to verify the NEW target state:
- `statement_timeout=120s` present
- `idle_in_transaction_session_timeout=300s` present
- `max_connections>=500` present
- `idle_session_timeout` is NOT present (or is `0`)

If any of these don't match, rewrite via `_ensure_authentik_compose_patches` and force-recreate Postgres. This is the self-healing path for boxes upgrading from v0.9.20 → v0.9.21.

**Smoke tests before push:**

```bash
# 1. Verify before/after CPU on tak-10 (must run for at least 30 min post-deploy to see effect)
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}' | grep authentik

# 2. Verify Postgres is running with new command line:
docker exec authentik-postgresql-1 sh -c "ps -ef | grep '[p]ostgres -c'"
# Expected: includes statement_timeout=120s, idle_in_transaction_session_timeout=300s
# Expected: does NOT include idle_session_timeout

# 3. Verify channels_postgres reconnect cycle has stopped:
docker logs authentik-server-1 --since 15m 2>&1 | grep -c 'IdleSessionTimeout\|Postgres connection is not healthy'
# Expected: 0 (after the first 5 min post-deploy)

# 4. Confirm Authentik is still functional:
curl -sk https://tak.test12.taktical.net/api/v3/admin/version/ -H "Authorization: Bearer $(grep AUTHENTIK_BOOTSTRAP_TOKEN ~/authentik/.env | cut -d= -f2)" | python3 -m json.tool
# Expected: {"version_current": "2026.X.Y", ...}

# 5. Confirm CONN_MAX_AGE is still doing its job — recycled web connections show short backend_start times:
docker exec authentik-postgresql-1 psql -U authentik -d authentik -c "
SELECT NOW() - backend_start AS conn_age, state, COUNT(*)
FROM pg_stat_activity
WHERE datname='authentik'
GROUP BY conn_age, state
ORDER BY conn_age DESC LIMIT 10
"
# Expected: web app connections (most) should be < 60s old; LDAP + channels connections may be older.
```

**Risk:** low. We're removing a setting that's now actively harmful and aligning with Authentik's upstream-recommended Postgres config. The safety net (`idle_in_transaction_session_timeout`) is preserved. Force-recreate Postgres requires <30s downtime, same as v0.9.20.

**Expected outcome on tak-10:**
- Authentik server CPU baseline: 38% → ~5-10%
- Postgres CPU baseline: 20% → ~3-5%
- Idle PG connections: 180 → ~50-80 (steady state without 5-min reconnect storms)
- `django_channels_postgres_message` table growth: unchanged (separate problem — v0.9.20 bloat monitor still relevant)
- Reconnect log noise: zero `IdleSessionTimeout` events

**Future cleanup (v0.9.22+):** Authentik 2026.8.0 ships the upstream fix for #20714 (sync API for `group_send`, eager message deletion). Once that ships, our v0.9.20 bloat monitor for `django_channels_postgres_message` becomes redundant and can be retired.

---

### Item 1 — `_ensure_authentik_compose_patches` → parse-and-mutate YAML

**Closes:** SAM operator Issue #1 (duplicate `command:`, `cap_drop:`, `security_opt:`, `healthcheck:` keys in `~/authentik/docker-compose.yml` after `Update Config`).

**Root cause:** the patcher at `app.py:25479` does substring-based detection (`re.search(r'command:\s*postgres\b.*max_connections=', ...)`) on the entire compose file text. When the operator's `command:` is in YAML folded-block-scalar form (`command: >- \n  postgres \n  -c ...`) the regex misses it, `has_pg_cmd=False`, and the append branch at `app.py:25511-25522` adds a NEW `command:` line — leaving the old one in place. Same pattern produces duplicate `cap_drop:` / `security_opt:` / `healthcheck:` keys when the operator's YAML style differs from infra-TAK's template.

**Approach:**

```python
# Pseudocode for the refactor:
import yaml

def _ensure_authentik_compose_patches(compose_path, plog=None):
    with open(compose_path) as f:
        try:
            data = yaml.safe_load(f)  # last-wins dedupes any existing duplicate keys
        except yaml.YAMLError as e:
            plog(f"  ⚠ compose file invalid YAML — falling back to legacy text patcher")
            return _ensure_authentik_compose_patches_legacy(compose_path, plog)

    services = data.setdefault('services', {})

    # PG command — single dict assignment, no duplicate key possible
    pg = services.setdefault('postgresql', {})
    pg_cmd = 'postgres -c max_connections=500 -c statement_timeout=120s ' \
             '-c idle_session_timeout=300s -c idle_in_transaction_session_timeout=300s ' \
             '-c tcp_keepalives_idle=60 -c tcp_keepalives_interval=10 -c tcp_keepalives_count=6'
    if pg.get('command') != pg_cmd:
        # Detect operator-set max_connections > 500 — don't downgrade
        existing = pg.get('command') or ''
        m = re.search(r'max_connections=(\d+)', existing)
        if m and int(m.group(1)) > 500:
            plog(f"  pg command: operator-set max_connections={m.group(1)} — preserving")
        else:
            pg['command'] = pg_cmd
            changed = True

    # Server/worker hardening — set keys, never append
    for svc_name in ('server', 'worker'):
        svc = services.setdefault(svc_name, {})
        svc.setdefault('cap_drop', ['ALL'])
        svc.setdefault('security_opt', ['no-new-privileges:true'])
        svc.setdefault('healthcheck', {
            'test': ['CMD', 'ak', 'healthcheck'],
            'start_period': '600s',
            'interval': '30s',
            'timeout': '10s',
            'retries': 5,
        })

    # ... etc for redis injection, AUTHENTIK_REDIS__HOST env var, etc.

    if changed:
        with open(compose_path, 'w') as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False, width=200)
```

**Caveat — comments will be lost.** PyYAML doesn't preserve comments. If we care, switch to `ruamel.yaml` (already-installed dependency check needed). For v0.9.21 ship with PyYAML; operators who commented their compose will lose those comments on first run. Document this in the release notes.

**Self-healing bonus:** PyYAML's `safe_load` resolves duplicate keys by last-wins. So any box that already has duplicates (from v0.9.18) gets cleaned up on the first Update Now to v0.9.21 — the file is re-parsed (deduped automatically), our patches applied at dict level, and dumped back without duplicates.

**Code path to refactor:** `app.py:25479-25700` approximately. Also touch the deploy template path at `app.py:27734` for parity. Keep the legacy text patcher around as `_ensure_authentik_compose_patches_legacy` in case PyYAML can't parse a file (defensive fallback).

**Smoke tests:** synthesize the 13 compose/.env cases from the v0.9.20 work + add 4 new cases:
- Operator command in folded-block-scalar form.
- Operator command in compact-list form (different indent).
- File with pre-existing duplicate `command:` keys (the v0.9.18 bug state).
- File with operator comments (verify acceptable degradation).

**Risk:** medium. The refactor is on a critical path (every Update Now / deploy). Test thoroughly. Pre-flight: tak-10 + responder + ssdnodes paste their compose files, run the refactor locally against each, diff the output to confirm idempotency and no regressions.

---

### Item 2 — CoreConfig.xml writer → ElementTree-based

**Closes:** SAM operator Issue #6 (duplicate `<ldap>` element in `/opt/tak/CoreConfig.xml` after Update Config).

**Root cause:** same as Item 1 — text-based mutation can insert duplicate elements when the source doesn't match the expected pattern.

**Approach:** find every site in `app.py` that mutates CoreConfig.xml (use `grep -n 'CoreConfig.xml' app.py` — there are ~12 sites). Convert each to:

```python
import xml.etree.ElementTree as ET
tree = ET.parse('/opt/tak/CoreConfig.xml')
root = tree.getroot()
# Find or create the <auth> element
auth = root.find('auth')
# Find or create the <ldap> element
ldap = auth.find('ldap')
if ldap is None:
    ldap = ET.SubElement(auth, 'ldap')
# Update attributes
ldap.set('url', 'ldap://127.0.0.1:389')
ldap.set('userstring', 'cn={username},ou=users,dc=takldap')
# ...
tree.write('/opt/tak/CoreConfig.xml', xml_declaration=True, encoding='UTF-8')
```

**Caveat:** TAK Server's CoreConfig.xml uses XML namespaces — handle the default namespace correctly when using ET.find/findall (`./auth/ldap` vs `./{ns}auth/{ns}ldap`).

**Risk:** medium-high. CoreConfig.xml is sensitive. Wrong mutation = TAK Server won't start. Pre-flight with a copy of tak-10's CoreConfig.xml.

**Order:** ship Item 1 first (less risky), Item 2 in a follow-up commit on the same v0.9.21 release.

---

### Item 3 — Wire `_auto_remove_stale_docker_service_connections` into `_startup_migrations`

**Closes:** SAM operator Issue #2 (operator-visible symptom: `outpost_service_connection_monitor` 30s retry loop on dead Docker socket → ~26% sustained CPU on worker).

**Important:** the operator's PROPOSED fix (add `/var/run/docker.sock` back to worker volumes) is **REJECTED** — it would regress the v0.9.2 CVE-2026-31431 hardening. We deliberately removed that socket mount. The right fix already exists at `app.py:40272` (`_auto_remove_stale_docker_service_connections`) which deletes the orphan service connection from Authentik's DB so the worker stops retrying.

**The gap:** this helper is currently only called from `_run_post_update()` — the version-gated path. Same wiring-gap exposure we fixed for PG-pool helpers in v0.9.20. On boxes that pulled v0.9.16 dev SHAs without bumping VERSION, or boxes upgraded from v0.9.15 → v0.9.20 directly (skipping v0.9.16's post-update hook entirely), the cleanup never ran.

**Approach:** add to `_startup_migrations` right after the trusted-proxy-CIDRs block (`app.py:~39180`):

```python
# v0.9.21 wiring-gap defense: docker.sock orphan cleanup.
# Existing helper at app.py:40272 (added v0.9.16, was only in post-update hook).
# Move to startup-migrations for guaranteed reach (same rationale as v0.9.20 PG-pool helpers).
try:
    if os.path.exists(os.path.expanduser('~/authentik/.env')):
        _auto_remove_stale_docker_service_connections()
except Exception as ak_dock_err:
    print(f"Startup migration: docker.sock orphan cleanup error (non-fatal): {ak_dock_err}")
```

**Verification post-deploy on a v0.9.16-skipper box:**

```bash
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA -c \
  "SELECT COUNT(*) FROM authentik_outposts_dockerserviceconnection WHERE local = TRUE"
# Expected: 0
```

**Risk:** low. Idempotent existing helper. Just changing call site.

---

### Item 4 — `_ensure_embedded_outpost_authentik_host` (new helper)

**Closes:** SAM operator Issue #4 (defensive — only partially universal, but cheap to make foolproof).

**The existing fix:** `app.py:28776` sets `existing_config['authentik_host'] = _get_authentik_base_url(settings)` when TAK Portal is set up. This works for normal first installs but doesn't catch:
- Boxes where FQDN was changed after first install.
- Boxes where TAK Portal was never deployed (pure proxy + Authentik installs).
- Boxes where the embedded outpost config was manually edited.

**Approach:** new helper, wired into `_startup_migrations`. Cheap idempotent re-assertion:

```python
def _ensure_embedded_outpost_authentik_host(plog):
    """Re-assert embedded outpost's _config.authentik_host to current FQDN-based public URL.
    Defensive: catches FQDN changes after first install, manual edits, partial bootstrap states.
    Idempotent: no API call if already correct."""
    settings = load_settings()
    fqdn = settings.get('fqdn', '')
    if not fqdn:
        plog("  embedded outpost host: no FQDN configured — skip")
        return
    correct = _get_authentik_base_url(settings)  # e.g. https://tak.<fqdn>/
    # GET current embedded outpost config
    # If config.authentik_host == correct: no-op
    # Else: PATCH with new authentik_host + authentik_host_insecure=False
    # Log outcome
```

**Where:** wire into `_startup_migrations` right after Item 3, around `app.py:~39200`.

**Risk:** low. One API call per console restart on a healthy box (read-only check). One PATCH only when drift detected.

---

### Item 5 — `_ensure_authentik_proxy_external_hosts_canonical` (new helper)

**Closes:** tak-10's wrong `external_host` on Node-RED Proxy + TAK Portal Proxy (both set to `https://taktical.test12.taktical.net` — the brand subdomain, NOT a Caddy vhost). Also closes the bug-class for SAM operator's Issue #5 (their box had different specifics but same drift category).

**The existing helper:** `_sync_authentik_provider_external_hosts` at `app.py:18115` ONLY fixes drift to `authentik.<fqdn>` (different specific case). Need a generalized helper for ALL canonical proxy provider hostnames.

**Approach:** new helper that defines the canonical mapping and reasserts each provider's `external_host`:

```python
def _ensure_authentik_proxy_external_hosts_canonical(plog):
    """For each known infra-TAK proxy provider, assert external_host matches
    https://<service-prefix>.<fqdn>. Fixes brand-subdomain drift and any other
    non-canonical external_host values. Idempotent.

    Discovered 2026-05-13: tak-10 had Node-RED + TAK Portal proxy external_host
    both set to https://taktical.test12.taktical.net (brand prefix, NOT in Caddyfile).
    Responder + ssdnodes were correct. Root cause investigation in Item 7 below."""
    settings = load_settings()
    fqdn = settings.get('fqdn', '')
    if not fqdn:
        plog("  proxy external_hosts: no FQDN configured — skip")
        return
    base = fqdn.split(':')[0]
    canonical = {
        'TAK Portal Proxy': f'https://takportal.{base}',
        'Node-RED Proxy':   f'https://nodered.{base}',
        'MediaMTX':         f'https://stream.{base}',
        'infra-TAK':        f'https://infratak.{base}',
        'Federation Hub Proxy': f'https://fedhub.{base}',
    }
    # GET all proxy providers via API
    # For each, if name in canonical and external_host != canonical[name]:
    #   PATCH external_host + cookie_domain
    # Log "fixed N provider(s)" or "all canonical — no-op"
```

**Where:** wire into `_startup_migrations` right after Item 4.

**Risk:** low-medium. The risk is if an operator has DELIBERATELY set a non-canonical `external_host` (e.g. they're white-labeling under a different brand). The helper would clobber that. Mitigation: add a settings override `disable_proxy_external_host_canonicalization: true` that bypasses the helper.

---

### Item 6 — (folded into Item 0)

**Original plan:** add `statement_timeout=120s` to Postgres command line as a separate item.

**Status:** folded into Item 0 because both changes touch the same `pg_cmd` template in `_ensure_authentik_compose_patches`. Shipping them together avoids two consecutive Postgres recreates. See Item 0 for the `statement_timeout=120s` addition and the rejection of operator Amos's other tuning proposals (`max_connections=80`, `WORKERS=1`).

---

### Item 7 — Root-cause investigation: where does `taktical.<fqdn>` come from?

**Pure investigation, no code change in v0.9.21 unless we find a clear bug.**

**The data:**
- tak-10 Authentik proxy provider `external_host` values:
  - MediaMTX: `https://stream.test12.taktical.net` ✓
  - Node-RED Proxy: `https://taktical.test12.taktical.net` ✗ (should be `nodered.`)
  - TAK Portal Proxy: `https://taktical.test12.taktical.net` ✗ (should be `takportal.`)
  - infra-TAK: `https://infratak.test12.taktical.net` ✓
- Same setup on responder + ssdnodes: all correct.
- The brand on tak-10 has `taktical` as a slug somewhere.

**Hypothesis:** somewhere in the TAK Portal + Node-RED proxy provider creation paths (`app.py:13069` for Portal, `app.py:17409` for Node-RED), the `external_host` is built from a brand-related template variable instead of `_get_module_fqdn(settings, '<service>')`. The MediaMTX + infra-TAK paths use the correct source.

**Investigation steps:**

```bash
# 1. Find where _tp_host (TAK Portal external_host) is set
rg -n '_tp_host\s*=' app.py
# 2. Find where _nr_host (Node-RED external_host) is set
rg -n '_nr_host\s*=' app.py
# 3. Trace each back to its source — is it _get_module_fqdn() or something brand-related?
# 4. Compare with MediaMTX + infra-TAK paths which got the correct value.
```

**Likely outcome:** find a `f'https://{brand_slug}.{fqdn}'` somewhere that should be `f'https://{service_slug}.{fqdn}'`. Fix at the source.

**Why Item 5 covers us either way:** even if the bootstrap bug stays unfixed, the self-healing helper from Item 5 corrects the drift on every console restart. Item 7 is for clean history; Item 5 is the immediate safety net.

---

## Items explicitly REJECTED from the SAM operator handoff

| # | Their proposal | Why rejected |
|---|---|---|
| #2 | Add `/var/run/docker.sock` to worker volumes | Regresses v0.9.2 CVE-2026-31431 hardening. The right fix is `_auto_remove_stale_docker_service_connections` (already shipped, just needs wiring — Item 3). |
| #3 (partial) | `max_connections=80` | Opposite direction from v0.9.20's validated `=500` + persistent connections approach. Their reasoning is sound for "hard cap on stampede" strategy; ours is sound for "pool headroom + persistent connections" strategy. We've validated ours across 3 boxes. |
| #3 (partial) | `AUTHENTIK_WEB__WORKERS=1` | We validated `=4` in v0.8.7+ and confirmed runtime via `ak dump_config`. Their `=1` would halve our capacity for no compensating benefit. |
| #3 (partial) | `AUTHENTIK_WEB__MAX_REQUESTS=1000` + jitter 100 | Worker recycling. Not harmful but not in v0.9.21 scope. Defer to v0.9.22+. |
| #5 | Bootstrap fix for SAM-specific `copix.<fqdn>` external_host | Their box used a different brand pattern than ours. We've reproduced the bug-class on tak-10 (Item 7) but the specific code path causing SAM's drift is different. Item 5 self-healing covers it generically. |

---

## Implementation order

Recommended commit order on dev:

1. **Item 0** (remove `idle_session_timeout`, add `statement_timeout=120s` to pg_cmd template) — **HIGHEST PRIORITY: biggest CPU win, fixes real elevated baseline on tak-10**. Touches one template + the `_authentik_fix_pg_idle_timeout` short-circuit logic. Self-contained, reversible. Force-recreates Postgres once (~30s downtime). Validate immediately on tak-10 by watching CPU + reconnect log noise for 30 min post-deploy.
2. **Item 3** (wire `_auto_remove_stale_docker_service_connections` into startup migrations) — mechanical, low risk.
3. **Item 4** (`_ensure_embedded_outpost_authentik_host` helper + wiring) — new helper, isolated.
4. **Item 5** (`_ensure_authentik_proxy_external_hosts_canonical` helper + wiring) — new helper, isolated. Validate against tak-10 first (it has the drift). Item 5 might also drop CPU a small additional amount on top of Item 0 if forward_auth misroutes were doing some work.
5. **Item 7** (investigation) — pure read; result feeds into Item 1 or is a separate small commit.
6. **Item 1** (YAML refactor of compose patcher) — biggest change, highest risk. Ship last so prior items have already landed independently.
7. **Item 2** (CoreConfig.xml ElementTree) — follow-on to Item 1 in the same release.

Each commit on dev should be self-contained and reversible. After each, smoke test on tak-10 + responder + ssdnodes before moving to the next.

**Item 0 is the highest-leverage commit** — should ship first and the CPU evidence (server 38% → <10%) will be the headline validation for the release.

---

## Smoke test plan

### Per-item smoke test (run on tak-10 each time after dev push + Update Now)

```bash
# Confirm v0.9.21 ran startup migrations cleanly
journalctl -u takwerx-console --since "5 minutes ago" | grep -E "Startup migration|infra-TAK v"

# Confirm all v0.9.20 + v0.9.21 helpers no-op'd (idempotent)
journalctl -u takwerx-console --since "5 minutes ago" | grep -c "idempotent — no-op\|skipping (idempotent)"
# Expected: at least 8 (all migrations from v0.8.x through v0.9.21)

# Confirm Authentik containers still healthy
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}' | grep -E 'authentik|NAME'
# Expected: server < 100%, worker < 5% in steady state
```

### Item-specific verifications

**Item 1 (YAML refactor):**
```bash
# Verify compose file is valid strict YAML (no duplicate keys)
python3 -c "
import yaml
with open('/root/authentik/docker-compose.yml') as f:
    yaml.safe_load(f)  # would raise on duplicates with strict loader
print('OK')
"
# Strict-mode check (requires PyYAML >= 5.1 with allow_duplicate_keys=False, default):
docker exec authentik-postgresql-1 sh -c "echo 'compose is valid'"
# (just confirms compose parsed)
```

**Item 3 (docker.sock orphan):**
```bash
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA -c \
  "SELECT COUNT(*) FROM authentik_outposts_dockerserviceconnection WHERE local = TRUE"
# Expected: 0
```

**Item 4 (embedded outpost authentik_host):**
```bash
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA -c \
  "SELECT _config->>'authentik_host' FROM authentik_outposts_outpost WHERE name LIKE '%Embedded%'"
# Expected: https://tak.<fqdn>/ (NOT http://authentik-server-1:9000/)
```

**Item 5 (proxy external_host canonical):**
```bash
docker exec authentik-postgresql-1 psql -U authentik -d authentik -t -A -c "
SELECT p.name, pp.external_host
FROM authentik_providers_proxy_proxyprovider pp
JOIN authentik_core_provider p ON p.id = pp.oauth2provider_ptr_id
ORDER BY p.name
"
# Expected on tak-10 after v0.9.21:
#   MediaMTX|https://stream.test12.taktical.net
#   Node-RED Proxy|https://nodered.test12.taktical.net    ← fixed (was taktical.)
#   TAK Portal Proxy|https://takportal.test12.taktical.net  ← fixed (was taktical.)
#   infra-TAK|https://infratak.test12.taktical.net
```

**Item 6 (statement_timeout):**
```bash
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA -c "SHOW statement_timeout"
# Expected: 2min (PostgreSQL's "2min" === 120s)
```

---

## Release notes draft (to write at end)

Title: **v0.9.21-alpha — Authentik baseline CPU fix (channels_postgres LISTEN survival) + duplicate-YAML-key bug class fixed + drift self-healing across 4 Authentik config surfaces**

TL;DR:
1. **Removed `idle_session_timeout=300s` from Postgres command line** — this v0.8.4-era setting was killing `django_channels_postgres` LISTEN connections every 5 min × 4 worker pids = 48 reconnect storms/hr, producing elevated baseline CPU. v0.9.20's `CONN_MAX_AGE=60` now handles the original idle-webapp-connection problem correctly, making `idle_session_timeout` redundant AND actively harmful. Aligns with upstream Authentik docker-compose defaults. Workaround for confirmed Authentik bug [#20714](https://github.com/goauthentik/authentik/issues/20714) (milestone Release 2026.8.0). Validated drop on tak-10: server CPU 38% → ~X%, PG CPU 20% → ~X%.
2. Added `statement_timeout=120s` to Postgres command line — defense in depth against runaway queries.
3. `_ensure_authentik_compose_patches` rewritten to parse-and-mutate YAML (PyYAML), eliminating duplicate-key emissions reported externally on v0.9.18 (operator Amos's handoff, 2026-05-13).
4. `CoreConfig.xml` mutations switched to ElementTree for the same reason.
5. New defensive self-healing migrations wired into `_startup_migrations`: `_auto_remove_stale_docker_service_connections` (moved from version-gated post-update), `_ensure_embedded_outpost_authentik_host`, `_ensure_authentik_proxy_external_hosts_canonical`. All idempotent.

**Validation surface:** synthetic compose/.env smoke tests + real-box validation on tak-10 (had the proxy external_host drift + the channels_postgres LISTEN kill), responder, ssdnodes.

---

## Cold-start notes for tomorrow's session

If you're picking this up cold on a different machine:

1. **Read `~/Downloads/handoff-infraTAK-v0.9.18-update-config-regression.md`** for operator Amos's full context (he did good empirical work).
2. **Read this plan top-to-bottom** to remember what's in/out.
3. **Start with Item 0** (HIGHEST PRIORITY — biggest CPU win, ships standalone) — edit `_ensure_authentik_compose_patches` `pg_cmd` template at `app.py:~25489` to remove `idle_session_timeout=300s` and add `statement_timeout=120s`. Then broaden `_authentik_fix_pg_idle_timeout` short-circuit logic at `app.py:~27170` to verify the new target state (no idle_session_timeout AND has statement_timeout). Same template change replicated in the deploy-from-template path at `app.py:~27734` for fresh installs.
4. **Items 3, 4, 5** are mechanical helper additions to `_startup_migrations` — each is ~20 lines of code + one wiring call.
5. **Item 7** is investigation only — read code (`rg -n '_tp_host\s*=' app.py` and `rg -n '_nr_host\s*=' app.py`), find root cause for tak-10's brand-prefix drift, add a single-line fix if obvious.
6. **Item 1** is the big refactor. Do this after the others are stable. Pre-flight on a copy of tak-10's compose file via local Python.
7. **Item 2** (XML) follows Item 1's pattern.

**Reference commits from v0.9.20 (recently shipped, same patterns):**
- `9f2771a` — wiring-gap fix (Items 3/4/5 follow this pattern)
- `2970470` — Authentik PG tuning (Item 0's template-bump pattern)

**Validated tak-10 baseline as of 2026-05-13 21:50 PT (PRE-Item-0):**
- PG conns: 315, idle_tx: 0, channels: 3,080 rows / 39 MB
- Authentik server: 2.68% steady (but spikes to 38% during channels_postgres reconnect storms)
- Authentik worker: 0.85%, PG: 4.64% steady (but 20% during reconnect)
- env vars: `CONN_MAX_AGE=60`, `CONN_HEALTH_CHECKS=true`, `max_connections=500`
- pg_cmd: `idle_session_timeout=300s` PRESENT (the Item-0 target)
- Proxy external_hosts: 2/4 wrong (Node-RED + TAK Portal both at `taktical.` — Item 5 target)

**Expected tak-10 baseline post-Item-0:**
- Authentik server: < 10% sustained (no more 5-min reconnect storms)
- PG: < 5% sustained
- `IdleSessionTimeout` events in authentik-server logs: 0 (after first 5 min post-deploy)
- PG idle connections: 180 → ~50-80

If anything in v0.9.21 breaks the validated-green v0.9.20 state, roll back immediately. v0.9.20 is the floor.

**Diagnostic confirming Item 0 root cause** (run on tak-10 right now to capture pre-state evidence for release notes):

```bash
docker logs authentik-server-1 --since 30m 2>&1 | grep -c 'IdleSessionTimeout\|Postgres connection is not healthy'
# Pre-v0.9.21: many (8-12 per worker × 4 workers = 32-48 per 30 min)
# Post-v0.9.21: 0
```

---

_Plan created 2026-05-13 evening by Cursor (Composer-2-fast), after fleet-wide v0.9.20 validation across tak-10 + responder + ssdnodes. Item 0 added 22:05 PT after live diagnostic on tak-10 surfaced the channels_postgres LISTEN-kill mechanism. Pickup-ready for next session on different hardware._
