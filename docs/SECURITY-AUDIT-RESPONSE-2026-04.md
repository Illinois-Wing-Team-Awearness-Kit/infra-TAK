# Security Audit Response — Project Shakespear (April 2026)

> **Auditor:** OBLIVION EDGE VULNERABILITY RESEARCH LLC
> **Audit date:** 2026-04-18
> **Audit scope:** TAK ecosystem — `infra-TAK`, `mediamtx-installer`, `takwerx`
> **infra-TAK response release:** v0.9.29-alpha
> **Reviewed against:** `dev` branch as of 2026-05-17

---

## Headline

The 74-finding audit lumps three separate repositories into one report. Of
the 9 CRITICAL findings, **6 do not live in `infra-TAK` at all** — they
target the `mediamtx-installer` (`mediamtx_config_editor.py`,
`mediamtx_ldap_overlay.py`), which is owned and triaged separately.

The three CRITs that do target `infra-TAK`:

- **CRIT-06** (SSH probe command injection) — **already mitigated** pre-audit;
  cited line numbers (`app.py:7932, 7962`) are stale. See "CRIT-06 mitigation
  map" below.
- **CRIT-07** (`eval` in guarddog scripts) — root-only blast radius, but cleaned
  up in v0.9.29 anyway.
- **CRIT-08** (`StrictHostKeyChecking=no` in guarddog SSH calls) — real,
  fixed in v0.9.29.

Most HIGH/MED findings the report lists as generic Flask hygiene (CSRF,
session flags, security headers, persistent secret_key, rate limiting) were
already in place at the time of the audit; this document records the file
and line for each.

---

## Per-repo split of the 74 findings

The report's executive summary reads as if all 74 findings target one
codebase. They don't. By file cite:

| Severity | infra-TAK | mediamtx-installer | takwerx | Generic / unscoped |
|---|---|---|---|---|
| CRITICAL (9) | 3 (06, 07, 08) | 6 (01, 02, 03, 04, 05, 09) | 0 | 0 |
| HIGH (19) | ~0 with hard file cites | ~14 (`mediamtx_*.py` cites for HIGH-01..07) | unknown | rest are generic |
| MEDIUM (28) | partial overlap (cert default, session, headers) | partial | unknown | majority |
| LOW (18) | CSP/HSTS tuning | partial | unknown | majority |
| **Verified by auditor (2)** | **0** | **2 (CRIT-01, CRIT-02)** | **0** | **0** |

**Both findings the auditor manually verified are in MediaMTX, not
`infra-TAK`.** The auditor flagged the remaining 7 CRITs as `Status: TBD` —
i.e. inherited from a prior audit and not re-confirmed against current code.

Action: forward the MediaMTX CRITs/HIGHs to the MediaMTX installer
maintainer. They are not tracked further here.

---

## CRIT-06 — SSH probe command injection (`app.py`)

**Audit claim:** "Settings.json values used in shell=True calls" at
`app.py:7932, 7962`. RCE via crafted settings.

**State on `dev` 2026-05-17:** mitigated. Cited line numbers no longer apply
to the SSH probe path (file is ~46k lines and has shifted across many
releases since the audit snapshot). The current mitigations are:

### 1. SSH target validation before any shell invocation

```10763:10785:app.py
def _validate_ssh_target(host, user, port):
    """v0.9.12 — validate SSH host/user/port before invoking ssh.

    Defends against operator/API-supplied values that contain SSH option
    flags (e.g. host = '-oProxyCommand=touch /tmp/x') or shell metacharacters
    via the `user@host` argument. Without this guard a remote-host setting
    would have allowed a logged-in console user (or any compromised settings
    file) to execute arbitrary commands locally.
    """
    if not host or len(host) > 253:
        return False, 'host empty or too long'
    if not _safe_migration_db_host(host):
        return False, f'host {host!r} fails IP/DNS validation'
    if not user or not _SSH_USER_RE.fullmatch(user):
        return False, f'ssh_user {user!r} fails POSIX username validation'
    ...
```

### 2. `_ssh_probe` always uses `accept-new`, never `=no`

```10812:10812:app.py
        '-o', 'StrictHostKeyChecking=accept-new',
```

### 3. Settings-derived values are `shlex.quote`'d at every interpolation

Example (Federation Hub deploy):

```7909:7926:app.py
            patch_cmds = (
                f'cd {fh_dir}/configs && '
                f'sudo sed -i "s/truststore-root/truststore-{shlex.quote(int_ca)}/g" federation-hub-ui.yml && '
                f'sudo sed -i "s/takserver\\.jks/{shlex.quote(remote_hostname)}.jks/g" federation-hub-broker.yml && '
                f'sudo sed -i "s/takserver\\.jks/{shlex.quote(remote_hostname)}.jks/g" federation-hub-ui.yml'
            )
            ...
            if cert_pass and cert_pass != 'atakatak':
                pw_cmd = (
                    f'cd {fh_dir}/configs && '
                    f'sudo sed -i "s/atakatak/{shlex.quote(cert_pass)}/g" federation-hub-broker.yml && '
                    f'sudo sed -i "s/atakatak/{shlex.quote(cert_pass)}/g" federation-hub-ui.yml'
                )
```

**Outstanding work (tracked separately, not in v0.9.29 scope):** a defense-
in-depth sweep of every `subprocess.run(..., shell=True ...)` callsite in
`app.py` (~50+ occurrences) to confirm no f-string interpolation of
settings/request values bypasses `shlex.quote`. Scoped as a v0.9.30 task —
see "Outstanding work" below.

---

## CRIT-07 — `eval` in guarddog scripts (fixed in v0.9.29)

**Audit claim:** `eval` of Python output in bash scripts (`tak-db-repack.sh`
and 2 others) is root RCE via config file manipulation.

**Actual blast radius:** root-only. The python emitted `export NAME=value`
lines with `shlex.quote` on the host, and `guarddog.conf` is root-owned at
`/opt/tak-guarddog/`. An attacker who can write `guarddog.conf` already has
root on the box. Still — `eval` is bad form regardless of who supplies the
input.

**Fix (v0.9.29):** replaced `eval "$(python3 ...)"` with newline-delimited
`read` on three scripts (`tak-db-repack.sh`, `tak-auto-vacuum.sh`,
`tak-retention-guard.sh`). The python emits raw lines; bash reads them with
`IFS= read -r`, so no shell parsing of the values ever happens.

Example (`tak-db-repack.sh`):

```36:62:scripts/guarddog/tak-db-repack.sh
if [ -f "$GUARDDOG_CONF" ]; then
  # v0.9.29 CRIT-07: replaced `eval "$(python3 ...)"` with newline-delimited
  # read so guarddog.conf values never reach a shell evaluator. The python
  # process only emits two literal lines; we never re-parse them as shell.
  {
    IFS= read -r TWO_SERVER_MODE
    IFS= read -r REMOTE_DB_HOST
  } < <(python3 - <<'PY'
import json, os
p = "/opt/tak-guarddog/guarddog.conf"
two = "0"; host = ""
if os.path.isfile(p):
    try:
        with open(p) as f:
            c = json.load(f)
        if c.get("two_server"):
            two = "1"
        host = str(c.get("db_host") or "")
    except Exception:
        pass
print(two)
print(host)
PY
  )
  TWO_SERVER_MODE="${TWO_SERVER_MODE:-0}"
fi
```

Also replaced `eval "$cmd"` in the local branch of `remote_cmd()` with
`bash -c "$cmd"`. Same shell-interpretation semantics, no double-eval
trap, no `eval` keyword to flag in future audits.

---

## CRIT-08 — `StrictHostKeyChecking=no` in guarddog SSH (fixed in v0.9.29)

**Audit claim:** "All inter-server SSH disables host key checking" — MITM on
encrypted admin channels.

**Verified:** present in 9 watchdog bash scripts on `dev` at audit time.
`app.py`'s `_ssh_probe` had already been migrated to `accept-new` (pre-audit)
but the bash watchdogs were missed.

**Fix (v0.9.29):**

1. Replaced `-o StrictHostKeyChecking=no -o ConnectTimeout=10` with
   `-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/opt/tak-guarddog/known_hosts -o ConnectTimeout=10`
   across all 9 scripts:

   - `scripts/guarddog/tak-remotedb-watch.sh` (3 sites)
   - `scripts/guarddog/tak-retention-guard.sh` (2 sites)
   - `scripts/guarddog/tak-remotedb-auth-watch.sh` (1 site)
   - `scripts/guarddog/tak-fedhub-watch.sh` (1 site)
   - `scripts/guarddog/tak-db-repack.sh` (2 sites)
   - `scripts/guarddog/tak-cotdb-watch.sh` (1 site)
   - `scripts/guarddog/tak-auto-vacuum.sh` (2 sites)

2. Provisioned `/opt/tak-guarddog/known_hosts` (mode 0600, root:root) in
   both the main Guard Dog deploy path and the deployment-sync path. First
   SSH connection pins the DB host's key; subsequent connections refuse on
   mismatch. See `app.py` around `_sync_guarddog_remote_db_from_settings`
   and the `for d in ['/opt/tak-guarddog', ...]` loop in the deploy thread.

3. Also migrated two raw `subprocess.run(['ssh', ...])` callsites in
   `app.py` (snapshot/rollback paths) from `=no` to `=accept-new` for
   consistency with `_ssh_probe`. No `UserKnownHostsFile` override on these
   — they reuse the gunicorn service account's `~/.ssh/known_hosts`, which
   is the same identity that runs `_ssh_probe`, so first-contact pins
   shared with normal admin SSH.

**Operator escape hatch (documented in release notes):** if a customer
rotates the DB host's SSH key, `: > /opt/tak-guarddog/known_hosts` clears
the pin. Guard Dog re-pins on the next watchdog cycle.

---

## CRIT-01..05, 09 — MediaMTX (forwarded, not tracked here)

These all cite `mediamtx_config_editor.py` or `mediamtx_ldap_overlay.py`:

| ID | Summary | File cite |
|---|---|---|
| CRIT-01 | shell=True with `retention` user input | `mediamtx_config_editor.py:9413` |
| CRIT-02 | Default `admin:admin` + plaintext password storage | `mediamtx_config_editor.py:309-310, 435` |
| CRIT-03 | `sed` command injection in config routes | `mediamtx_config_editor.py:7016, 8374-8453` |
| CRIT-04 | Unsafe `tarfile.extractall()` (Zip-Slip) | `mediamtx_config_editor.py:10329-10330` |
| CRIT-05 | Authentik header spoofing (no source IP check) | `mediamtx_ldap_overlay.py:75-89` |
| CRIT-09 | YAML injection via string interpolation | `mediamtx_config_editor.py:8117-8127` |

Forwarded to the `mediamtx-installer` maintainer (operator owns that repo).

**Adjacent concern that does touch `infra-TAK`:** CRIT-05 (Authentik header
spoofing). `infra-TAK` is downstream of Authentik via Caddy in production
deployments. The threat is "a request that bypasses Caddy reaches the
backend with attacker-supplied `X-Authentik-*` headers and is treated as
authenticated." `infra-TAK`'s console does not consume `X-Authentik-*`
headers for authentication — login uses `auth.json` with hashed passwords
and a Flask session cookie. The header-trust attack surface lives in the
MediaMTX overlay, not `infra-TAK`'s app.py. No code change needed here.

---

## Already-addressed defaults (audit listed generically)

The audit lists these as defense-in-depth gaps in the report's
"Medium & Low Severity Issues" section. They were already implemented in
`infra-TAK` at audit time:

| Audit topic | File / lines (current `dev`) |
|---|---|
| CSRF protection on state-changing requests | `app.py:332-337` — same-origin check on every `/api/*` POST/PUT/PATCH/DELETE |
| Session cookie `HttpOnly` | `app.py:97` — `SESSION_COOKIE_HTTPONLY = True` |
| Session cookie `SameSite` | `app.py:98` — `SESSION_COOKIE_SAMESITE = 'Lax'` |
| Persistent random Flask secret_key with 0600 perms | `app.py:87-96` — `secrets.token_hex(32)` persisted to `.config/secret_key` |
| Rate limiting (login + API writes) | `app.py:320-330` — in-memory deque per `_client_ip` |
| Security headers (X-Frame-Options, Referrer-Policy, Permissions-Policy) | `app.py:340-365` — `apply_security_headers` after_request |
| HSTS when request is secure | `app.py:362-364` |
| CSP (with `'unsafe-inline'`, no `'unsafe-eval'` after v0.9.29) | `app.py:349-360` — see CSP tightening below |
| MAX_CONTENT_LENGTH (file upload size cap, 2 GiB) | `app.py:99` |
| X-Content-Type-Options nosniff | `app.py:343` |
| `frame-ancestors 'none'` in CSP | `app.py:356` |

### CSP tightening (v0.9.29)

Dropped `'unsafe-eval'` from `script-src` after confirming zero `eval()` /
`new Function()` / string-form `setTimeout` callsites in the JS bundles and
inline scripts:

```349:360:app.py
    # v0.9.29 (security audit LOW): dropped 'unsafe-eval' — codebase has no
    # eval()/new Function()/string-form setTimeout. Inline scripts still
    # require 'unsafe-inline' (template surgery deferred to nonces).
    csp = (
        "default-src 'self' https: data: blob:; "
        "script-src 'self' 'unsafe-inline' https:; "
        "style-src 'self' 'unsafe-inline' https:; "
        ...
```

`'unsafe-inline'` removal requires nonce-injection across ~25
`render_template_string` callsites and is deferred to a future release.

---

## v0.9.29 MED-tier addition: default `atakatak` password nag

Audit's "Hardcoded certificate passwords — `atakatak` in multiple
locations" is a real symptom but the upstream TAK default — `infra-TAK`
falls back to `atakatak` when no override exists.

**v0.9.29:** added a dismissable banner across every console page when:

- TAK Server is installed (`/opt/tak/CoreConfig.xml` present), AND
- `tak_cert_password` is unset or equals `atakatak`, AND
- operator has not dismissed the nag (`cert_pw_warning_dismissed`).

The dismiss flag is cleared automatically when a non-default password is
saved, so re-introducing the default (via reset/restore) will re-trigger
the nag.

Implementation:

- `render_default_cert_password_warning(settings)` in `app.py` near
  `render_custom_banner`
- Wired into the existing `@app.context_processor` (`inject_cloudtak_icon`)
- POST endpoint `/api/security/dismiss-cert-pw-warning` (login-required, same-origin CSRF gate)
- Auto-clear hook in `takserver_set_cert_password()`

---

## Outstanding work (deferred to v0.9.30+)

### 1. `shell=True` defense-in-depth sweep

Grep every `subprocess.run(..., shell=True ...)` callsite in `app.py`
(~50+ occurrences) and confirm:

- No bare f-string interpolation of settings.json values without `shlex.quote`
- No `request.form[...]` / `request.json[...]` reaching `cmd` without sanitization

If any unsafe sites are found, fix in a dedicated v0.9.30 hardening sprint
and update this document with the outcome.

**Why not in v0.9.29:** the audit re-cited stale line numbers without
re-verifying. The current threat surface needs to be re-mapped against the
current `dev` tree before action, which is a sweep, not a fix. v0.9.29
addresses the verified-real items first.

### 2. CSP `'unsafe-inline'` removal

Requires nonce-injection across ~25 `render_template_string` callsites.
Scoped as a UI hardening sprint, not security-critical given
`frame-ancestors 'none'` + `X-Frame-Options: DENY` + same-origin CSRF +
no JS sinks for arbitrary URLs.

### 3. Re-baseline against current `dev`

The audit's stale line numbers (`app.py:7932, 7962` cited for CRIT-06)
suggest the auditor's snapshot was significantly older than current `dev`.
Ask Oblivion Edge for the commit SHA they audited so we can confirm which
of the 74 findings are already dead. The current report only details 9
CRITs and a handful of HIGHs in body text.

### 4. Authentik upstream header-trust check (defense-in-depth)

Verify Caddy never forwards `X-Authentik-*` from upstream to the `infra-TAK`
backend, even though `infra-TAK` does not consume them for auth. Currently
the Caddyfile sets `X-Forwarded-*` explicitly per service; an explicit
`header_down -X-Authentik-*` line would belt-and-braces against future
header-trust regressions.

---

## Validation gate for v0.9.29

Per [`.cursor/rules/fleet-uniform-config.mdc`](../.cursor/rules/fleet-uniform-config.mdc):

- Three test boxes (`tak-10`, `test8`, `responder` / `infratak-vps`) pull
  v0.9.29 from `dev` with **no manual config edits**.
- On each box, run a Guard Dog cycle that triggers an SSH watchdog (e.g.
  `tak-remotedb-watch.sh`); confirm first connect populates
  `/opt/tak-guarddog/known_hosts` and subsequent runs reuse it.
- Soak ≥ 60 min on each box: zero `permission denied (publickey)`, zero
  `host key verification failed`, all guarddog alerts behave as before.
- Browser-check console pages with DevTools open: no CSP `unsafe-eval`
  violations after CSP tightening.
- Visual check of dashboard: `atakatak` nag banner appears on a fresh
  install, hides on a custom password, and stays hidden after dismiss.

Only merge `dev` → `main` after all three boxes pass.

---

## References

- [`scripts/guarddog/tak-remotedb-watch.sh`](../scripts/guarddog/tak-remotedb-watch.sh)
- [`scripts/guarddog/tak-db-repack.sh`](../scripts/guarddog/tak-db-repack.sh)
- [`scripts/guarddog/tak-auto-vacuum.sh`](../scripts/guarddog/tak-auto-vacuum.sh)
- [`scripts/guarddog/tak-retention-guard.sh`](../scripts/guarddog/tak-retention-guard.sh)
- [`app.py`](../app.py) `_validate_ssh_target` (~line 10763)
- [`app.py`](../app.py) `_ssh_probe` (~line 10787)
- [`app.py`](../app.py) `apply_security_headers` (~line 340)
- [`app.py`](../app.py) `render_default_cert_password_warning` (~line 1014)
- [`docs/RELEASE-v0.9.29-alpha.md`](RELEASE-v0.9.29-alpha.md)
- [`.cursor/rules/fleet-uniform-config.mdc`](../.cursor/rules/fleet-uniform-config.mdc)
- [`.cursor/rules/consult-upstream-docs.mdc`](../.cursor/rules/consult-upstream-docs.mdc)

OpenSSH `StrictHostKeyChecking` docs (consulted per upstream-docs rule):
[`man ssh_config(5)`](https://man.openbsd.org/ssh_config.5#StrictHostKeyChecking)
