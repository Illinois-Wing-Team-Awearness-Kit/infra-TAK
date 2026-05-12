# v0.9.12-alpha — Cyber Security Hardening

**Date:** 2026-05-11
**Type:** Comprehensive security hardening (post-v0.9.11 follow-up). Not a fire drill — no live exploit observed.
**Reference:** [docs/PLAN-v0.9.12.md](PLAN-v0.9.12.md), [docs/PORT-EXPOSURE-POLICY.md](PORT-EXPOSURE-POLICY.md), [docs/RELEASE-v0.9.11-alpha.md](RELEASE-v0.9.11-alpha.md).

---

## Why this release exists

v0.9.11 patched ONE upstream-credential / port-exposure vulnerability (CloudTAK PostgreSQL → PG_MEM/PGMiner cryptominer infection). The audit immediately after found the same class of issue across the rest of the stack:

- Publicly-bound admin services (TAK Portal 3000, MediaMTX HLS/admin/webedit, remote Authentik 9000/9443, CloudTAK api/tiles).
- Unconditional UFW `allow` rules that silently overrode source-scoped rules (Server One Postgres, Guard Dog health agent).
- Post-auth code bugs in admin-side routes: snapshot path traversal, external-DB SQL injection, webadmin password shell injection, hardcoded LDAP service password fallback, missing SSH host/user validation.

None of these were live-exploited. v0.9.12 is the planned hardening pass that closes them all in a single release.

---

## Part A — Port hardening

Adopts the formal Tier classification from `docs/PORT-EXPOSURE-POLICY.md`. Tier 1 (public, ~7 ports), Tier 3 (Caddy-loopback, `127.0.0.1` + UFW deny), Tier 4 (Docker-internal, no host port), Tier 5 (source-scoped UFW).

### A1. CloudTAK — extend the v0.9.11 override

`_cloudtak_build_override_yml()` now also includes:

```yaml
services:
  api:
    ports: !reset
      - "127.0.0.1:5000:5000"
  tiles:
    ports: !reset
      - "127.0.0.1:5002:5002"
  events:
    ports: !reset []           # no host port — Docker-internal worker
  media:
    ports: !reset
      - "127.0.0.1:${MEDIA_PORT_API:-9997}:9997"
      - "${MEDIA_PORT_RTSP:-18554}:8554"
      - "${MEDIA_PORT_RTMP:-11935}:1935"
      - "127.0.0.1:${MEDIA_PORT_HLS:-18888}:8888"
      - "${MEDIA_PORT_SRT:-18890}:8890"
```

`_auto_harden_cloudtak()` extends its UFW deny list to cover the new loopback ports: `5000, 5002, 5003, 9997, 18888` (in addition to the v0.9.11 entries `5433, 9000, 9002`). Streaming ports `18554` (RTSP), `11935` (RTMP), `18890` (SRT) are kept public — those are legitimate external streaming endpoints (Tier 1).

### A2. TAK Portal — new `_auto_harden_takportal()`

`_write_takportal_override()` now adds:

```yaml
services:
  tak-portal:
    ports: !reset
      - "127.0.0.1:${WEB_UI_PORT:-3000}:${WEB_UI_PORT:-3000}"
```

New `_auto_harden_takportal()` post-update step: writes the override, applies UFW deny on `3000/tcp`, and force-recreates the container only if it's still on `0.0.0.0`. Mirrors the v0.9.11 CloudTAK hardening pattern. TAK Portal's authentication is delegated to Authentik via Caddy `forward_auth` — anyone hitting `3000` directly bypassed that boundary pre-v0.9.12.

### A3. MediaMTX — new `_auto_harden_mediamtx()`

Both compose-generated `mediamtx.yml` files (local and remote installs) now use:

```yaml
apiAddress: 127.0.0.1:9898      # admin API — Caddy-loopback
hlsAddress: 127.0.0.1:8888      # HLS — Caddy proxies via /hls-proxy/
```

The webedit Flask script (`mediamtx_config_editor.py`) is sed-patched to bind `host='127.0.0.1'` (was `'0.0.0.0'` upstream). UFW now allows only Tier 1 streaming ports (`8554, 8322, 8890, 8000, 8001`) and denies Tier 3 (`8888, 5080, 9898`). New `_auto_harden_mediamtx()` patches existing installs on every Update Now (regex sed-in-place on `mediamtx.yml`, string-replace on `mediamtx_config_editor.py`, systemd restart only if anything changed).

### A4. Remote Authentik — new `_auto_authentik_ports_remote()`

Remote Authentik's compose template now binds `server` (Authentik web) to `127.0.0.1:9000` / `127.0.0.1:9443`. Caddy on the remote proxies the public FQDN to loopback. The LDAP outpost (`389/636`) intentionally stays on `0.0.0.0` because TAK Server on the console host has to reach it across the network — but UFW source-scopes it to the console's IP only (`settings.server_ip`), with `deny 389/tcp` + `deny 636/tcp` as the catch-all.

`_auto_authentik_ports_remote()` SSHes into the remote on every Update Now, patches `~/authentik/docker-compose.yml`, and `docker compose up -d --force-recreate` if anything was on `0.0.0.0`. Falls back to public-allow if `Settings → Server IP` is empty (logged as a warning).

### A5 / A6. Server One — Postgres + Guard Dog health agent

Two-server install (`_setup_postgres_on_server_one`) now removes the unconditional `ufw allow {db_port}/tcp` that silently overrode the source-scope rule above it. UFW: `allow from {Server Two IP} to any port {db_port} proto tcp` then `deny {db_port}/tcp`. The standalone two-server step list shipped to operators (`server_one_steps`) gets the same treatment.

Guard Dog health agent install (both `_deploy_health_agent_to_server_one` and the inline copy in `run_guarddog_deploy`) source-scopes `8080/tcp` to the console source IP (`_fedhub_caddy_source_ip(settings)`) with `deny 8080/tcp` catch-all. Falls back to public-allow only when no source IP is configured.

---

## Part B — Route-level patches

### B1. Snapshot path traversal validator

New `_validate_snapshot_label(label)` defined after `SNAPSHOT_DIR`:

- Rejects empty / non-string labels, `.` / `..`, and anything that doesn't match `^[A-Za-z0-9._-]+$`.
- `os.path.realpath(os.path.join(SNAPSHOT_DIR, label))` and verifies the result lives strictly inside `os.path.realpath(SNAPSHOT_DIR) + os.sep`.

Wired into four sinks:

- `GET /api/takserver/snapshot/<label>/download`
- `DELETE /api/takserver/snapshot/<label>`
- `POST /api/takserver/rollback` (request body `label`)
- `_tak_rollback(label, plog)` helper (defence-in-depth)

Pre-v0.9.12 these used `os.path.basename(label)` only (download route) or no validation at all (delete/rollback). A request with `label=../../etc` would `shutil.rmtree('/etc')`.

### B2. External-DB SQL injection (`/api/takserver/external-db/provision`)

- All identifiers (`app_user`, `db_name`, `admin_user`) regex-validated against `^[A-Za-z_][A-Za-z0-9_]{0,62}$` (the Postgres identifier grammar) BEFORE any `psql` call.
- `db_host` validated via `_safe_migration_db_host` (IP or simple DNS name).
- `db_port` range-checked (1..65535).
- Passwords (`app_pass`) passed to psql via `-v pw=value` and referenced as `:'pw'` in SQL. Postgres does not support parameter substitution for identifiers, so the regex above is the only safe path for those.

### B3. External-DB test-connection RCE

`bash -c "</dev/tcp/{db_host}/{db_port}"` → `socket.create_connection((db_host, db_port), timeout=8)`. No shell, no injection. Same `_safe_migration_db_host` + port range check applied to `db_host` / `db_port` upfront.

### B4. Webadmin password shell injection

`/api/takserver/webadmin-password` POST now validates the password with `_validate_cert_password` (existing helper, rejects shell metacharacters) and invokes `UserManager.jar` with argv:

```python
subprocess.run(['java', '-jar', '/opt/tak/utils/UserManager.jar',
                'usermod', '-A', '-p', pw, 'webadmin'], ...)
```

instead of the pre-v0.9.12 f-string-inside-`bash -c` form. Secondary call site at TAK Server deploy uses `shlex.quote` for defence-in-depth.

### B5. Hardcoded LDAP service password fallback

The literal 32-char `B9wobRV8wlFJmnlEWB71gJjD3aoKOBBW` baked into `app.py` since v0.7.x is gone. When `AUTHENTIK_BOOTSTRAP_LDAPSERVICE_PASSWORD` is missing from `~/authentik/.env`, the deploy now generates a fresh `secrets.token_urlsafe(24)` and persists it back to `.env` so the next run reads the same value. No two installs ever share an `adm_ldapservice` credential anymore.

### B6. SSH host/user injection

New `_validate_ssh_target(host, user, port)` regex-checks `host` (via `_safe_migration_db_host`) and `user` (`^[a-z_][a-z0-9_-]{0,31}\$?$` — POSIX username). Called from `_ssh_probe` and `_scp_to_host` before they build the ssh argv. Defends against a settings file (or compromised API caller) that supplies `host = '-oProxyCommand=touch /tmp/owned'` — pre-v0.9.12 ssh would happily interpret that as an option.

### B7. `~/authentik` tilde expansion under gunicorn — HOME pinning

**Symptom surfaced during v0.9.12 test cycle on `tak-10`:** clicking **Sync webadmin to Authentik** in the console returned `/bin/sh: 1: cd: can't cd to ~/authentik` even though `/root/authentik` clearly existed. Same failure mode would intermittently break `_ensure_authentik_ldap_service_account` and other code paths that shell out to `cd ~/authentik && docker compose …`.

**Root cause:** `takwerx-console.service` has only ever pinned `Environment=PYTHONUNBUFFERED=1` and `Environment=CONFIG_DIR=…` — it never carried `Environment=HOME=…`. systemd does **not** inherit `HOME` from login env, so under gunicorn `os.environ.get('HOME')` returned `None` and `subprocess.run('cd ~/authentik …', shell=True)` invoked `/bin/sh` with no `HOME`, leaving `~` literal. **This is the same class of bug fixed for `takupdatesguard.service` in v0.2.7-alpha** (`Environment=HOME=…` added there) and the same root cause as the v0.9.2 `git safe.directory` / `git config --global` issue documented in [docs/RELEASE-v0.9.2-alpha.md](RELEASE-v0.9.2-alpha.md). It was never carried across to the console unit.

Three-layer fix, belt + suspenders + migration:

1. **Runtime guard (`app.py`, top of file, after stdlib imports):** if `HOME` is unset, derive it from `pwd.getpwuid(os.getuid()).pw_dir` (`/root` fallback) and write it into `os.environ`. All `subprocess.run` children inherit it. Fixes the **current** gunicorn process the moment v0.9.12 boots — no restart needed.
2. **`start.sh create_service()`:** new installs get `Environment=HOME=$SERVICE_HOME` baked into `takwerx-console.service` (derived from the running shell's `$HOME`, falls back to `/root`).
3. **`_startup_pin_console_service_home()` migration:** runs on every console startup. If `/etc/systemd/system/takwerx-console.service` is missing `Environment=HOME=`, the line is inserted after the existing `Environment=` block and `systemctl daemon-reload` is fired. Existing v0.9.11 installs converge automatically — operators don't have to edit unit files.

After this fix, `cd ~/authentik`, `cd ~/CloudTAK`, `cd ~/TAK-Portal`, etc. all work consistently from any `subprocess.run(..., shell=True)` call site. No call-site changes were needed — fixing the root cause once at the env layer was the documented, reusable pattern.

### B8. Authentik ReputationPolicy binding was inverted — `negate=True` required

**Symptom surfaced during v0.9.12 testing on `tak-10`:** every LDAP bind for `webadmin` (and `adm_ldapservice` until the cache rebuilt) returned `Invalid credentials (49)`, the Authentik server log filled with `FlowNonApplicableException` on `ldap-authentication-flow`, and Sync webadmin reported `webadmin exists but LDAP bind verification failed (DN/password)` — even with a freshly hashed password and the user in the correct groups (`authentik Admins`, `tak_ROLE_ADMIN`).

**Root cause — verified against the deployed upstream source.** Per [.cursor/rules/consult-upstream-docs.mdc](../.cursor/rules/consult-upstream-docs.mdc), the canonical truth is what the running container does. From the deployed `authentik-server-1` container (Authentik `2026.2.2`) on `tak-10`:

```python
# inspect.getsource(authentik.policies.reputation.models.ReputationPolicy.passes)
def passes(self, request: PolicyRequest) -> PolicyResult:
    remote_ip = ClientIPMiddleware.get_client_ip(request.http_request)
    query = Q()
    if self.check_ip:
        query |= Q(ip=remote_ip)
    if self.check_username:
        query |= Q(identifier=request.user.username)
    score = Reputation.objects.filter(query).aggregate(
        total_score=Sum("score"))["total_score"] or 0
    passing = score <= self.threshold
    return PolicyResult(bool(passing))
```

**`ReputationPolicy.passes()` returns `True` only when the score is `≤` threshold** — i.e. only when the user has accumulated enough bad reputation to *act on*. A normal user with `score = 0` or positive returns `False`. We shipped the binding with `negate=False`, so the binding result for every normal user was `False`. With `policy_engine_mode: any` on the flow and this as the **only** binding, every LDAP bind made the flow non-applicable. Authentik returned 49 regardless of whether the password was correct.

**Why this hid for ten releases (v0.9.2 → v0.9.12-rc):** the LDAP outpost runs in `bind_mode: cached`. Once `adm_ldapservice`'s first successful bind landed in the cache, all subsequent binds for that DN were served from cache and never re-consulted the flow. The misconfigured binding never had a chance to fail. `webadmin` was only re-evaluated when the operator clicked Sync webadmin (rare in practice). v0.9.12's `_startup_resync_ldap_service_account` force-recreates the LDAP outpost on every console boot, wiping the bind cache, exposing the long-standing misconfig.

**Fix — three layers:**

1. **`_authentik_setup_reputation_policy()`** — new bindings POST with `negate=True, failure_result=True`. The block now carries the upstream source citation in a multi-line comment so the next agent doesn't re-flip it.
2. **`_authentik_setup_reputation_policy()` retro-fix branch** — when the binding already exists, the drift detector triggers DELETE+POST recreate if **either** `failure_result != True` **or** `negate != True`. (PATCH on `policies/bindings/{pk}/` returns 405 on Authentik 2026.x — DELETE+POST is the documented workaround.)
3. **`_startup_fix_reputation_policy_drift()`** — same dual-field idempotent migration runs on every console startup. Existing v0.9.11 installs converge automatically on first Update Now; subsequent boots are no-ops.

**Boolean truth table after the fix:**

| user state | `score <= -5`? | policy returns | after `negate` | binding result | flow applicable | bind result |
|---|---|---|---|---|---|---|
| normal (`score=0..+N`) | False | `False` | `True` | passes | YES | password check runs |
| brute-force abuser (`score <= -5`) | True | `True` | `False` | denies | NO | bind rejected (intended gate) |

This is the **Authentik-documented usage** of ReputationPolicy as a brute-force gate, and is now what infra-TAK actually ships.

**Cross-references:**
- `docs/HANDOFF-LDAP-AUTHENTIK.md` updated with a `negate=True` rule line so this can't get re-introduced without a doc trigger.
- The session that diagnosed this on `tak-10` is captured in the project transcripts; the proof was a direct `inspect.getsource()` call on the running container's `ReputationPolicy.passes` — exactly the workflow `.cursor/rules/consult-upstream-docs.mdc` requires.

---

## Operator action required

**Update Now is the only action needed for the majority of installs.** The new `_auto_harden_*` post-update steps patch existing CloudTAK / TAK Portal / MediaMTX / remote Authentik installs in place. Expect:

- Brief downtime on TAK Portal and MediaMTX during the post-update recreate (typically <60s each).
- Remote Authentik will reconnect after Caddy reloads on the remote host (≈10-30s).
- For two-server TAK Server installs, the next Guard Dog deploy or external-DB provision call will apply the new UFW rules; existing installs continue working with the previous rules until then.

**One manual step recommended:** if you run multi-server (TAK Server two-server, remote Authentik, or remote MediaMTX), make sure `Settings → Server IP` is filled with your console's public IP. Without it, source-scoped UFW rules for LDAP `389/636` and Guard Dog `8080` fall back to public-allow with a warning logged in the deploy output.

---

## Test cadence (reinstated)

Per the operator's explicit request after the v0.9.7 / v0.9.8 / v0.9.9 churn, v0.9.12 was developed under a stricter cadence:

1. Code on `dev` branch.
2. Tag `v0.9.12-alpha-rc1`, push to `dev`, test in-product on `responder`, `tak-10`, and a third box.
3. Verify with the validation checklist below.
4. Only then merge `dev` → `main`, tag `v0.9.12-alpha`, push.

---

## Validation checklist (for operators / reviewers)

- [ ] `grep '^VERSION' app.py` shows `VERSION = "0.9.12-alpha"`.
- [ ] CloudTAK ports: `ss -tlnp | grep -E '0\.0\.0\.0:(5000|5002|5003|9997|18888)'` returns nothing.
- [ ] TAK Portal: `ss -tlnp | grep '0\.0\.0\.0:3000'` returns nothing.
- [ ] MediaMTX: `ss -tlnp | grep -E '0\.0\.0\.0:(5080|8888|9898)'` returns nothing.
- [ ] Authentik (local): `ss -tlnp | grep -E '0\.0\.0\.0:(9000|9443)'` returns nothing.
- [ ] UFW: `ufw status | grep -E '(5000|5002|5003|5433|9000|9997|18888|3000|5080|8888|9898).*DENY'` shows all the Tier 3/4 ports denied.
- [ ] Two-server: `ufw status | grep DENY | grep 5432` shows Postgres denied to general internet (source-scoped allow above it).
- [ ] App still works: `curl -sS https://cloudtak.example.com/` returns 200, console pages load, MediaMTX webedit reachable through Caddy.
- [ ] Snapshot routes refuse `../../etc` payloads with `400 label must match [A-Za-z0-9._-]+`.

---

## Pre-existing release notes

- [v0.9.11-alpha — Security hotfix (CloudTAK PG_MEM/PGMiner)](RELEASE-v0.9.11-alpha.md)
- [v0.9.10-alpha — orphan-postgres killer fix](RELEASE-v0.9.10-alpha.md)
- [v0.9.9-alpha](RELEASE-v0.9.9-alpha.md)
- [v0.9.8-alpha](RELEASE-v0.9.8-alpha.md)
- [v0.9.7-alpha](RELEASE-v0.9.7-alpha.md)
- [v0.9.6-alpha](RELEASE-v0.9.6-alpha.md)
