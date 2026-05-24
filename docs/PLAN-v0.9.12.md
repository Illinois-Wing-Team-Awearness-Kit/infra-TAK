# v0.9.12 — Cyber Security Hardening Release (Plan)

> Not yet implemented. This is the planning document for the v0.9.12 release.
>
> Scope decided 2026-05-10 after the comprehensive post-incident audit triggered by the v0.9.11 CloudTAK / PG_MEM compromise. The non-root console migration originally earmarked for this slot has been moved to **v1.0.0** (see `PLAN-v1.0.0.md`) so the v0.9.x cycle can land focused security patches without coupling them to a disruptive runtime change.

---

## Why v0.9.12 exists

The v0.9.11 CloudTAK fix patched ONE upstream-credential / port-exposure vulnerability. The audit that followed found the same class of issue (publicly-bound services + lax auth boundaries) elsewhere in the stack, plus a small cluster of post-authentication code bugs (SQL injection, command injection, path traversal) in admin-side routes.

None of these are live-exploited. Unlike v0.9.11, this release is a planned hardening pass, not a fire drill.

**Two-part goal:**
1. **Port exposure** — collapse the public attack surface from ~20-24 ports to **7 ports** by mirroring the v0.9.11 CloudTAK `!reset` override pattern across every other service we deploy.
2. **Route safety** — fix the post-auth code bugs surfaced by the routes audit (SQLi, RCE, path traversal in snapshot routes, hardcoded LDAP fallback password).

---

## Part A — Port hardening (`_auto_harden_*` pattern)

Adopt the formal Tier classification from `docs/PORT-EXPOSURE-POLICY.md` (new doc shipping with v0.9.12). Every service must be classified as **Tier 1 (public)**, **Tier 3 (Caddy-loopback)**, **Tier 4 (Docker-internal)**, or **Tier 5 (source-scoped)** before merge.

### A1. CloudTAK — extend the v0.9.11 override

`app.py:14939` `_cloudtak_build_override_yml()` — add `!reset` for the three remaining public services:

```yaml
services:
  api:
    ports: !reset
      - "127.0.0.1:5000:5000"
  tiles:
    ports: !reset
      - "127.0.0.1:5002:5002"
  media:
    ports: !reset
      - "127.0.0.1:9997:9997"
      - "127.0.0.1:18888:18888"
```

UFW belt-and-braces: extend `_auto_harden_cloudtak` to `ufw deny 5000, 5002, 9997, 18888 /tcp`.

### A2. TAK Portal — add port hardening to the override

`app.py:10101` `_write_takportal_override()` — add to the override block:

```yaml
services:
  tak-portal:
    ports: !reset
      - "127.0.0.1:3000:3000"
```

New `_auto_harden_takportal()` callable from `_run_post_update()` mirroring `_auto_harden_cloudtak`. UFW: `ufw deny 3000/tcp`.

### A3. MediaMTX — bind API + HLS + webedit to loopback

`app.py:13142-13154` MediaMTX config generator and `app.py:13353` webedit systemd:
- `apiAddress: 127.0.0.1:9997`
- `hlsAddress: 127.0.0.1:8888`
- webedit systemd: `Environment="PORT=5080"` → `Environment="PORT=5080"` + bind via mediamtx-installer to `127.0.0.1:5080`

New `_auto_harden_mediamtx()` runs on every Update Now, idempotent. UFW: `ufw deny 5080, 8888 /tcp`. Public RTSP/SRT/TURN (8554/8322/8890) untouched — those are the product.

### A4. Authentik — extend `_auto_authentik_ports()` to remote installs

`app.py:38060` `_auto_authentik_ports()` already hardens local Authentik installs to 127.0.0.1. Wrap it in a remote variant that runs the same string replacements over SSH on every Update Now when remote-Authentik mode is detected. UFW deny (over SSH) for 9090/9443/389/636 if no operator-defined source-scope rule exists.

Additionally: **remove `ports:` blocks entirely** for Authentik's `postgresql` and `redis` services. They only need Docker DNS — the host port binding adds nothing.

### A5. Server One Postgres — drop unconditional UFW allows

`app.py:2772-2773` and `app.py:2940-2941`:

```diff
- f'sudo ufw allow from {core_ip} to any port {db_port} proto tcp && '
- f'sudo ufw allow {db_port}/tcp && '
+ f'sudo ufw allow from {core_ip} to any port {db_port} proto tcp && '
```

Source-scoped rule stays. Unconditional allow goes. Same class of bug as the CloudTAK 5433 incident, except UFW is actually effective here (Server One PG is native, not Docker-published).

### A6. Server One Guard Dog port 8080 — source-scope

`app.py:3913` and `app.py:6968`:

```diff
- 'sudo ufw allow 8080/tcp >/dev/null 2>&1; '
+ f'sudo ufw allow from {core_ip} to any port 8080 proto tcp >/dev/null 2>&1; '
```

---

## Part B — Route-level patches

### B1. Snapshot path traversal — 3 routes

Add a private helper at the top of the snapshot section:

```python
def _validate_snapshot_label(label):
    """Reject any label that could escape SNAPSHOT_DIR. Returns (ok, safe_label_or_error)."""
    if not label or not isinstance(label, str):
        return False, 'label is required'
    if label in ('.', '..'):
        return False, 'invalid label'
    if not re.fullmatch(r'[A-Za-z0-9._-]+', label):
        return False, 'label must be [A-Za-z0-9._-]+'
    snap_path = os.path.realpath(os.path.join(SNAPSHOT_DIR, label))
    if not snap_path.startswith(os.path.realpath(SNAPSHOT_DIR) + os.sep):
        return False, 'label resolves outside SNAPSHOT_DIR'
    return True, label
```

Call from all three sinks:
- `app.py:32757` `/api/takserver/snapshot/<label>/download` — currently only checks `os.path.basename(label) == label`, misses `.` / `..`.
- `app.py:32916` `/api/takserver/snapshot/<label>` DELETE — currently zero validation; `label='..'` blows away the parent of `SNAPSHOT_DIR` via `shutil.rmtree`.
- `app.py:32941` `/api/takserver/rollback` → `_tak_rollback(label, ...)` (`app.py:32426`+) — currently zero validation; attacker controls files copied into `/opt/tak/CoreConfig.xml`, `/opt/tak/certs/files`, and the pgdump streamed into `pg_restore`.

### B2. External-DB SQL injection (`/api/takserver/external-db/provision`)

`app.py:2370-2406` — replace f-string-built SQL with proper identifier validation + parameterized password:

```python
IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')
if not IDENT_RE.fullmatch(app_user):
    return jsonify({'success': False, 'error': 'invalid app_user'}), 400
if not IDENT_RE.fullmatch(db_name):
    return jsonify({'success': False, 'error': 'invalid db_name'}), 400
# Pass password via psql --variable substitution (-v) and quote in SQL as :'pass'
r = subprocess.run(
    ['psql', '-h', host, '-U', admin_user, '-d', 'postgres', '-v', f"pw={app_pass}",
     '-c', f"ALTER USER {app_user} WITH PASSWORD :'pw';"],
    env=dict(os.environ, PGPASSWORD=admin_pass),
    capture_output=True, text=True, timeout=30,
)
```

Identifiers (user, db) MUST be regex-validated before going into SQL. Passwords MUST go through `psql -v ... :'name'` substitution, never f-strings.

### B3. External-DB command injection (`/api/takserver/external-db/test-connection`)

`app.py:2465` — replace the `bash -c "</dev/tcp/{host}/{port}"` shell-out with Python:

```python
import socket
tcp_ok = False
try:
    with socket.create_connection((db_host, db_port), timeout=8):
        tcp_ok = True
        add_check(f'TCP {db_host}:{db_port}', True, f'Connected to {db_host}:{db_port}')
except (socket.timeout, OSError) as e:
    add_check(f'TCP {db_host}:{db_port}', False, str(e)[:200])
```

No shell, no injection vector. `db_host` is passed as a literal arg to `socket.create_connection` — no interpretation.

### B4. Webadmin-password command injection (`/api/takserver/webadmin-password` POST)

`app.py:29842` — drop `shell=True`, use argv:

```python
r = subprocess.run(
    ['java', '-jar', '/opt/tak/utils/UserManager.jar', 'usermod', '-A', '-p', pw, 'webadmin'],
    capture_output=True, text=True, timeout=30,
)
flatfile_ok = r.returncode == 0
```

Also call `_validate_cert_password(pw)` (the same helper used by the sibling cert-password POST route) to reject obvious junk before passing through.

### B5. Hardcoded LDAP service password fallback (`app.py:27375`)

Replace the literal `'B9wobRV8wlFJmnlEWB71gJjD3aoKOBBW'` fallback with generate-and-persist:

```python
if not ldap_svc_password:
    import secrets as _secrets
    ldap_svc_password = _secrets.token_urlsafe(24)
    # Write back to .env so future runs read this value
    with open(env_path, 'a') as f:
        f.write(f'\nAUTHENTIK_BOOTSTRAP_LDAPSERVICE_PASSWORD={ldap_svc_password}\n')
    plog(f"  ⚠ AUTHENTIK_BOOTSTRAP_LDAPSERVICE_PASSWORD was missing — generated a new value and saved it to ~/authentik/.env")
```

Then Authentik's `set_password` API call (already a few lines below at line ~27360) pushes the new value into the live install. Every install now has a unique LDAP service password instead of the shared 32-char literal that was baked into every install since the function was introduced.

### B6. SSH host/user validation in deployment configs

`app.py:9028` (`_normalize_module_deployment_config`) and `app.py:10379` (`_normalize_tak_deployment_config`):

```python
HOST_RE = re.compile(r'^[A-Za-z0-9.\-_:%]+$')  # hostnames, IPs, IPv6 with %scope
USER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9._-]{0,31}$')
host = (raw.get('host') or '').strip()
user = (raw.get('user') or 'root').strip()
if host and not HOST_RE.fullmatch(host):
    raise ValueError('invalid SSH host')
if user and not USER_RE.fullmatch(user):
    raise ValueError('invalid SSH user')
```

Blocks the `-oProxyCommand=...` style injection vector. Also covers Finding 9 (`core_ip`/`db_host` flowing through `_ssh_probe`) since those values come through these same normalizers.

---

## Part C — Out of scope for v0.9.12 (deferred to v0.9.13 or v1.0.0)

These are real findings but require Caddy regeneration, UI surface area, or operator-policy changes that are too disruptive to bundle into a security patch:

| Item | Why deferred | Earliest target |
|------|--------------|-----------------|
| `X-Authentik-Username` shared-secret header | Requires Caddy regen + careful staged testing of forward_auth path | v0.9.13 |
| Mask passwords in `/api/authentik/password`, `/api/takserver/webadmin-password` GET, `/api/takserver/cert-password` GET | UI change (Reveal button + re-auth flow) | v0.9.13 |
| `atakatak` keystore-password rotation button | Operator-policy change, needs ATAK client re-export workflow doc | v0.9.13 |
| `SESSION_COOKIE_SECURE = True` | Trivial change, but want it tested alongside the shared-secret header work | v0.9.13 |
| Console "Service Exposure" panel | UI work — operator visibility into Tier 1/3/4/5 status | v1.0.0 |
| Non-root console (`takwerx` user) migration | Disruptive runtime change | v1.0.0 |
| SVG agency-logo content sanitization | Low severity, separate cleanup | v0.9.13 |
| README ports table refresh | Doc update, lands with v0.9.12 release notes | v0.9.12 (docs only) |

---

## Files touched in v0.9.12 (planned)

| File | Change |
|------|--------|
| `app.py` (VERSION line) | `0.9.11-alpha` → `0.9.12-alpha` |
| `app.py:14939` | Extend `_cloudtak_build_override_yml()` with `!reset` for api/tiles/media |
| `app.py:10101` | Extend `_write_takportal_override()` with `ports: !reset` |
| `app.py:13142-13354` | Bind MediaMTX `apiAddress`/`hlsAddress`/`PORT` to `127.0.0.1` |
| `app.py:38060` (`_auto_authentik_ports`) | Add remote-install variant + remove host `ports:` for postgres/redis |
| `app.py:2772-2773, 2940-2941` | Drop unconditional `sudo ufw allow {db_port}/tcp` |
| `app.py:3913, 6968` | Source-scope `ufw allow 8080/tcp` for Server One Guard Dog |
| `app.py:2370-2406` | Parameterize SQL in `/api/takserver/external-db/provision` |
| `app.py:2465` | Replace `bash -c "</dev/tcp/...">"` with `socket.create_connection` |
| `app.py:29842` | argv-call UserManager.jar; add `_validate_cert_password` check |
| `app.py:32757, 32916, 32941, 32426` | Centralized `_validate_snapshot_label()` + call sites |
| `app.py:27375` | Generate-and-persist LDAP service password instead of literal fallback |
| `app.py:9028, 10379` | Add `HOST_RE` / `USER_RE` validation in deployment-config normalizers |
| **NEW** `app.py:_auto_harden_takportal()` | Mirror `_auto_harden_cloudtak()` |
| **NEW** `app.py:_auto_harden_mediamtx()` | Mirror `_auto_harden_cloudtak()` |
| **NEW** `docs/RELEASE-v0.9.12-alpha.md` | Operator-facing release notes |
| **NEW** `docs/PORT-EXPOSURE-POLICY.md` | Canonical Tier 1/3/4/5 reference |
| `docs/SECURITY-AUDIT-2026-05-10.md` | Audit findings + remediation status |
| `README.md` | Refresh ports table to match policy doc; changelog entry |
| `memory-bank/techContext.md` | VERSION constant + v0.9.12 entry |

---

## Test plan — back to dev-first cadence

The v0.9.5 → v0.9.11 rollercoaster taught us not to push security fixes directly to `main` without a smoke test. v0.9.12 lands on `dev` first.

### Phase 1 — `dev` branch on `responder`
1. Push v0.9.12-alpha to `dev`.
2. SSH to `responder` (already running v0.9.11, CloudTAK uninstalled, the riskiest box).
3. `cd ~/infra-TAK && git checkout dev && git pull && systemctl restart takwerx-console`.
4. Trigger Update Now from the console.
5. Verify expected hardening:
   - `ss -tlnp` shows TAK Portal 3000, MediaMTX 5080/8888, Authentik 9090/9443/389/636, CloudTAK 5000/5002/9997/18888 all on **127.0.0.1** (or absent entirely).
   - `ss -tlnp | grep -E '0\.0\.0\.0:(22|80|443|5001|8089|8443|8446|8554|8322|8890)'` — exactly the Tier 1 set.
   - Public ports test from off-box: `nc -zv responder.example.com 3000` → refused; `nc -zv responder.example.com 443` → connects.
6. Functional checks:
   - Hit Caddy 443 → TAK Server admin loads at 8446.
   - Reinstall CloudTAK → MapL loads, no DB exposure.
   - Reinstall TAK Portal → portal loads at portal FQDN.
   - Trigger Server One PG sync (if two-server mode active) → still works.
   - Snapshot create + download + delete — verify validator rejects `..`, `.`, `foo/bar`, etc.
   - Webadmin password set — verify argv path works (no shell injection vector).
7. Re-check `~/authentik/.env` for the new `AUTHENTIK_BOOTSTRAP_LDAPSERVICE_PASSWORD` line.

### Phase 2 — `dev` branch on `tak-10`
Same procedure on the clean-state box. Confirms the same fixes work on a never-compromised install.

### Phase 3 — `dev` branch on the third box
Confirms portability.

### Phase 4 — merge `dev` → `main`, tag v0.9.12-alpha
Only after all three boxes show clean Phase-1 results.

### Phase 5 — production rollout
Operators run Update Now. v0.9.12 banner in the console after update completes.

---

## Operator-facing release notes outline

`docs/RELEASE-v0.9.12-alpha.md` will read like the v0.9.11 notes — same "What was wrong / What's fixed / What you need to do" structure. Key messages:

- **You do not need to take action.** Update Now applies all hardening automatically. No "Remove + Reinstall" required (unlike v0.9.11).
- **Visible change:** ports no longer publicly reachable. If you were depending on direct access to (e.g.) MediaMTX HLS on port 8888 from off-box, you now reach it via the FQDN through Caddy. Document any operator-side adjustments.
- **Pointer to `docs/PORT-EXPOSURE-POLICY.md`** as the canonical "which ports are public and why" reference.
- **Credit:** triggered by the v0.9.11 CloudTAK compromise post-mortem. Same audit methodology applied to every other service.

---

## Acceptance criteria

v0.9.12 ships when:
- [ ] All Part A items merged and tested on 3 boxes.
- [ ] All Part B items merged and unit-smoke-tested.
- [ ] `ss -tlnp` on a fresh post-update box shows exactly the Tier 1 set on `0.0.0.0` (+ Tier 2 federation if enabled).
- [ ] `docs/PORT-EXPOSURE-POLICY.md` published.
- [ ] `docs/RELEASE-v0.9.12-alpha.md` published with operator-facing language.
- [ ] README ports table reflects new policy.
- [ ] `memory-bank/techContext.md` updated.
- [ ] Tag `v0.9.12-alpha` pushed to `main`.
- [ ] Updates verified on responder, tak-10, third box.
