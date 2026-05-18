# v0.9.29-alpha — Security audit touch-up (Project Shakespear 2026-04) + Authentik MAX_REQUESTS cascade short-circuit + TAK LDAP verifier namespace fix + cloud public-IP detection + MediaMTX cert-perm self-heal + 8446 LE connector ns0-prefix root-cause fix + mediamtx-webeditor PermissionError fail-loop fix

**Date:** 2026-05-18
**Type:** Security + stability release — drop-in update via Update Now.
**Status:** Implemented on `dev`. Awaiting fleet validation gate (tak-10 / test8 / responder) before merge to `main`.

> Full per-finding analysis: [`SECURITY-AUDIT-RESPONSE-2026-04.md`](SECURITY-AUDIT-RESPONSE-2026-04.md).
> Plan: [`PLAN-v0.9.29-alpha.md`](PLAN-v0.9.29-alpha.md).
> Node-RED multipart polygon work moved to [`PLAN-v0.9.30-alpha.md`](PLAN-v0.9.30-alpha.md).

---

## TL;DR

A friend-of-the-operator audit ("Oblivion Edge Vulnerability Research LLC / Project Shakespear", April 2026, 74 findings, 9 CRITICAL) was forwarded for review. After cross-referencing every cited file path against `infra-TAK`'s current `dev` tree:

- **6 of 9 CRITs are not in `infra-TAK`** — they target `mediamtx-installer` (`mediamtx_config_editor.py`, `mediamtx_ldap_overlay.py`), owned and triaged separately. Forwarded.
- **2 of 9 CRITs the auditor manually verified are both in MediaMTX.** None in `infra-TAK`.
- **CRIT-06 (SSH probe command injection in `app.py:7932, 7962`)** — cited line numbers are stale. Current code is already mitigated by `_validate_ssh_target` (v0.9.12) plus `shlex.quote` throughout SSH command construction.
- **CRIT-07 (`eval` in guarddog scripts)** — real but root-only blast radius (config is root-owned).
- **CRIT-08 (`StrictHostKeyChecking=no` in 9 guarddog SSH calls)** — **real and unfixed.** `_ssh_probe` in `app.py` had been migrated to `accept-new` pre-audit, but the bash watchdogs were missed.

What v0.9.29 changes:

1. **CRIT-08 fix:** 9 guarddog scripts switched to `StrictHostKeyChecking=accept-new` + pinned `/opt/tak-guarddog/known_hosts`. Two raw-SSH callsites in `app.py` also migrated for consistency.
2. **CRIT-07 cleanup:** all 3 `eval "$(python3 ...)"` patterns replaced with newline-delimited `read`; `eval "$cmd"` in `remote_cmd()` replaced with `bash -c`.
3. **MED `atakatak` nag:** dismissable banner across every console page while the TAK cert password is still the upstream default.
4. **LOW CSP tightening:** `'unsafe-eval'` dropped from `script-src` (no `eval`/`new Function`/string-form `setTimeout` anywhere in the JS).
5. **Stability — Authentik MAX_REQUESTS cascade short-circuit.** Field forensic on `tak-10` 2026-05-18 caught the v0.9.23 autotune tune-UP cascade in action: after a leak burst halved MAX_REQUESTS to floor (100), the autotuner takes ~7 hours and **10 server+worker recreates** to climb back to baseline=1000 at +25%/30min (100 → 125 → 156 → 195 → 243 → 303 → 378 → 472 → 590 → 737 → 921 → 1000). Each recreate drops the LDAP outpost websocket for 1-5s. New startup migration `_patch_authentik_max_requests_snap_to_baseline_if_pgbouncer` snaps MAX_REQUESTS straight to baseline in **one shot** when PgBouncer is installed AND no fires in last 6h AND no autotune apply in last 30min. Idempotent no-op on boxes already at baseline (which is the safe steady state for any box with PgBouncer headroom).
6. **Cursor rule for release safety.** New `.cursor/rules/no-main-merge-no-tag-without-permission.mdc` codifies the boundary discovered during v0.9.28-alpha shipping: AI agents must never merge to `main` or push tags without explicit operator permission. Documents the incident and the required pre-merge prompt.
7. **TAK LDAP CoreConfig verifier — namespace-prefix tolerance.** Discovered on a fresh Azure deploy (`tak-test-4`, on v0.9.28 main) on 2026-05-18: the LDAP connect-sync banner reported `coreconfig=FAIL ready=NO` even though LDAP wiring was correct and `ldapsearch` against `cn=adm_ldapservice` succeeded. Root cause: TAK Server 5.7-RELEASE-32-HEAD canonicalizes `CoreConfig.xml` on restart and writes elements with the `ns0:` XML namespace prefix (`<ns0:auth ...>...</ns0:auth>`), and our verifier `_coreconfig_has_ldap()` was doing naive substring lookup for `<auth` / `</auth>` — false-negative. Defensively also patched `_apply_coreconfig_ldap_auth_text()` (the legacy regex-based fallback) for the same bug class. The ElementTree-based primary patcher `_apply_coreconfig_ldap_auth_et()` already handled namespaces correctly (line 34337-34338 builds `ns_prefix` from root tag) and continues to work.
8. **Cloud public-IP detection on fresh install.** Same `tak-test-4` deploy surfaced a second issue: `start.sh` used `hostname -I | awk '{print $1}'` to populate `settings.server_ip` and the SSL self-signed cert SAN, which on Azure/AWS/GCP returns the private/internal interface IP, not the public one operators actually browse to. New `detect_server_ip()` helper in `start.sh` queries Azure Instance Metadata Service → AWS IMDSv1 → `api.ipify.org` → `ifconfig.me` → falls back to `hostname -I`. Used in two sites: settings.json write and openssl SAN generation. **Existing installs:** `start.sh` only runs on fresh install, so cloud VMs that bootstrapped on v0.9.28 or earlier keep their private IP in `settings.server_ip`. Operator fix is one-click: open Settings, paste the public IP into `server_ip`, Save.
9. **MediaMTX deploy — Caddy LE cert read-access for `takwerx`.** Discovered on the same `tak-test-4` Azure deploy (also reported by a field operator on SSD Nodes preparing for SOF Week): after a successful `deploy-mediamtx` (all 7 steps pass), `mediamtx.service` enters a crash loop with `ERR stat /var/lib/caddy/.local/share/caddy/certificates/.../<host>.crt: permission denied`. Root cause: Caddy stores LE certs as `0600 caddy:caddy` under a `0700 caddy:caddy` directory chain (per Caddy's [storage docs](https://caddyserver.com/docs/conventions#data-directory) — "All Caddy data is stored with restrictive permissions (700) to prevent leaking secrets"). The MediaMTX systemd unit runs as `User=takwerx`, which cannot traverse the chain to read the cert. Fix has three parts:
   - Add `SupplementaryGroups=caddy` to the unit template in `deploy_mediamtx()` (line 14206).
   - After cert-wiring (line 14535), run `usermod -aG caddy takwerx` and `chmod g+rx` the directory chain + `chmod g+r` on the .crt/.key files, then `systemctl daemon-reload` so the supplementary group takes effect.
   - In `scripts/guarddog/tak-mediamtx-watch.sh`, re-apply the chmod/usermod block right before the watchdog restart, so an LE renewal (every ~60 days — right when SOF Week ends for this deploy) doesn't silently re-`0600` the new cert files and break MediaMTX again. Self-heals on any guarddog-triggered restart.
10. **Docs:** `docs/SECURITY-AUDIT-RESPONSE-2026-04.md` records the per-repo split, the CRIT-06 mitigation map, the already-addressed defaults (CSRF, session flags, security headers, persistent secret_key, rate limiting), and the outstanding work for v0.9.30+ (`shell=True` defense-in-depth sweep, CSP `unsafe-inline` removal, etc.).

### Item 11 — 8446 webadmin login fails after fresh deploy — `ns0:` namespace prefix root cause FIXED

**Symptom (operator-facing):** on a brand-new TAK Server install (Azure `tak-test-4` 2026-05-18, and reported separately by a field operator on SSD Nodes preparing for SOF Week), webadmin login at `https://<host>:8446` returns "Invalid username or password" even though:

- the correct password was used,
- the Authentik LDAP outpost is healthy and `ldapsearch -x -D 'cn=adm_ldapservice,...' -W -b 'ou=users,dc=takldap' '(cn=webadmin)'` succeeds from the TAK box,
- `<auth default="ldap"><ldap .../></auth>` is present in `/opt/tak/CoreConfig.xml`,
- `/Marti/api/version` curls return 200.

**Root cause (verified on `tak-test-4` 2026-05-18 via deploy-log forensics):** `_apply_coreconfig_ldap_auth_et()` in `app.py` had `ET.register_namespace('', '')` at line 34573 — the WRONG syntax to suppress the default-namespace prefix. The TAK Server `CoreConfig.xml` declares `xmlns="http://bbn.com/marti/xml/config"` on its root `<Configuration>`. The correct call is `ET.register_namespace('', 'http://bbn.com/marti/xml/config')`. Without proper URI registration, Python's `xml.etree.ElementTree.write()` assigns a synthetic prefix (`ns0:`) to **every** element in the document on serialization. The actual diff between an unbroken and broken CoreConfig:

```diff
- <Configuration xmlns="http://bbn.com/marti/xml/config">
-     <network ...><connector port="8446" .../></network>
-     <auth default="ldap" ...><ldap .../></auth>
+ <ns0:Configuration xmlns:ns0="http://bbn.com/marti/xml/config">
+     <ns0:network ...><ns0:connector port="8446" .../></ns0:network>
+     <ns0:auth default="ldap" ...><ns0:ldap .../></ns0:auth>
```

TAK Server's Spring Security LDAP wiring keys off non-prefixed `<auth>` / `<ldap>` elements during context init. The XML is still namespace-valid, but Spring's bean post-processor doesn't auto-prefix-resolve the same way — `<ns0:auth>` and `<ns0:ldap>` aren't picked up, so the OAuth2 password-grant manager initializes without an `LdapAuthenticationProvider` in its filter chain. The 8446 connector still starts on Tomcat (it's a port binding, not Spring Security state), so the box looks healthy in every other check — but `POST /oauth/token` returns 401 with no LDAP bind attempt visible in the LDAP outpost logs (because TAK Server never tries to bind).

`install_le_cert_on_8446()` had a related secondary bug: its regex `<connector port="8446"[^/]*/>` only matched non-prefixed elements, so on a CoreConfig already mangled to `ns0:` by a prior `_apply_coreconfig_ldap_auth_et()` run, the LE wire-up silently no-op'd (leaving the connector as `_name="cert_https"` with no LE keystore attrs). Operators only noticed this when the original v0.9.28 RELEASE doc misdiagnosed the symptom as "8446 connector silently reverts."

**Fix (three layers — all in `app.py`):**

1. `_apply_coreconfig_ldap_auth_et()` — detect the namespace URI from the file (`re.search(r'xmlns="([^"]+)"')`) and call `ET.register_namespace('', ns_uri)` BEFORE `ET.parse()`. Output is now clean `<Configuration xmlns="...">` form with zero `ns0:` prefixes. Verified via standalone roundtrip test: `assert '<ns0:' not in output`.
2. **Self-heal for legacy boxes.** Before parsing, strip any pre-existing `ns0:` prefixes from the file (the inverse of the bug: `<ns0:foo>` → `<foo>`, drop `xmlns:ns0=`, re-add `xmlns=` if missing). This means `Update Now` on a box previously mangled by v0.9.28 (or older) heals the file on the next LDAP sync without operator intervention. Logged as `✓ CoreConfig.xml: stripped legacy ns0: prefixes (was breaking LDAP auth)`.
3. **Belt-and-suspenders verification.** Three guards: (a) abort the write if `<ns0:` survives the ET roundtrip (post-write check on the patch tempfile); (b) re-read `/opt/tak/CoreConfig.xml` after the `sudo cp` and refuse to return success if `<ns0:` is present (detects partial-write / file-system races); (c) `install_le_cert_on_8446()`'s connector regex made namespace-tolerant (`<(?:[A-Za-z][\w-]*:)?connector\s+port="8446"[^/]*/\s*>`) AND a post-patch verify confirms `_name="LetsEncrypt"` and `takserver-le.jks` landed in the file — on failure restore from `.bak-le` and return False loudly.

**Operator impact:** Update Now from v0.9.28 (or any older release that hit this bug) will, on the next LDAP sync (either auto-fired from `_apply_ldap_to_coreconfig` during `Connect TAK Server to LDAP`, or as part of an Authentik redeploy), self-heal the CoreConfig namespace and unbreak 8446 login. Operators who previously hand-edited CoreConfig to work around this don't need to undo anything — the strip is idempotent on already-clean files.

**Forensic data on `tak-test-4` 2026-05-18:**

```
05:10:01  Step 8/9: CoreConfig.xml configured        ← run_takserver_deploy first auto-LDAP-connect
05:10:01  sudo cp /opt/tak/CoreConfig.xml.ldap-patch.xml /opt/tak/CoreConfig.xml
                                                     ← ET wrote `<ns0:Configuration ...>` with 64 prefixed elements
05:22:59  ✓ CoreConfig.xml 8446 connector patched    ← install_le_cert_on_8446 ran (TAK Server rewrote CC clean during restart)
05:23:00  Starting TAK Server with LE cert on port 8446...
05:27:44  sudo cp /opt/tak/CoreConfig.xml.ldap-patch.xml /opt/tak/CoreConfig.xml
                                                     ← _apply_ldap_to_coreconfig re-fired (from auto-flow), re-mangled to ns0:
05:27:59  Tomcat started on ports 8443, 8444, 8446   ← TAK Server "starts fine" with ns0: CC but with no LdapAuthenticationProvider
05:30:00  user attempts webadmin login → HTTP 401     ← silent failure, no LDAP bind in outpost log
```

Stripping `ns0:` prefixes from `/opt/tak/CoreConfig.xml` and restarting `takserver-api` made `POST /oauth/token` return 200 + JWT immediately (verified on `tak-test-4` 2026-05-18 at 23:51 UTC).

### Item 12 — `mediamtx-webeditor.service` fail-loop on fresh installs — `/usr/local/etc/mediamtx_backups` PermissionError

**Symptom (operator-facing):** after a successful MediaMTX deploy (all 7 steps pass, `mediamtx` itself running healthy on `:8554` / `:8888` / `:9898`), visiting `https://stream.<domain>/` returns the Authentik **"Not Found"** page (`authentik Logo / Not Found / Go home / Powered by authentik`). All 4 deploy checks pass — RTSP / RTSPS / SRT / HLS are reachable — but the Caddy-fronted browser UI is dead.

**Root cause (verified on `tak-test-4` 2026-05-18 0716 UTC):** the upstream `mediamtx_config_editor.py` (from `takwerx/mediamtx-installer`) hard-codes

```python
BACKUP_DIR  = '/usr/local/etc/mediamtx_backups'
CONFIG_FILE = '/usr/local/etc/mediamtx.yml'
```

and at module-import time (line ~93) calls `os.makedirs(BACKUP_DIR, exist_ok=True)`. The systemd unit `mediamtx-webeditor.service` runs the editor as `User=takwerx`, but `/usr/local/etc/` is `root:root 0755` (created by the deploy's `mkdir -p` for the .yml file) and the `BACKUP_DIR` doesn't exist → `PermissionError: [Errno 13] Permission denied: '/usr/local/etc/mediamtx_backups'` → editor exits 1 → systemd `Restart=always` re-launches → tight fail-loop. **264 restart cycles** observed on tak-test-4 before discovery, all logging the same traceback.

Downstream chain that produces the **Authentik "Not Found"** page (not a MediaMTX "page not available"):

```
1. Browser → https://stream.<domain>/ → Caddy
2. Caddy stream.* vhost → forward_auth 127.0.0.1:9090 (Authentik /outpost.goauthentik.io/auth/caddy)
3. Authentik approves (user authenticated, MediaMTX app has open access)
4. Caddy → reverse_proxy 127.0.0.1:5080  ← upstream is DOWN (fail-loop)
5. Caddy treats upstream-unreachable as auth-handler fallback → serves
   the body Authentik returned for /auth/caddy when the session check
   was IN-PROGRESS (Authentik's "application not found / not yet configured"
   intermediate page, which has the same `authentik Logo / Not Found` chrome
   as a true 404 from the outpost).
```

So the operator sees an Authentik-branded "Not Found" page on a domain Authentik IS configured for — confusing because the obvious culprit (Authentik) is innocent. The actual culprit is that nothing's listening on `:5080`.

Same class as the LE-cert read-access fix earlier in this release: a `takwerx`-run service needs writable paths in directories owned by `root`, and the deploy doesn't pre-create or chown them.

**Fix (two layers — both in `app.py`):**

1. **Deploy-time pre-chown in `deploy_mediamtx()`** (between `systemctl enable` and `systemctl start`). Three chowns:

```python
mkdir -p /usr/local/etc/mediamtx_backups
chown -R takwerx:takwerx /usr/local/etc/mediamtx_backups
chown -R takwerx:takwerx /opt/mediamtx-webeditor   # for theme_config.json, email_config.json, users_file, share_links, group_metadata, srt_passphrase_backup, pending_reg, reset_tokens, etc.
chown takwerx:takwerx /usr/local/etc/mediamtx.yml  # editor's Save-Config UI needs write; mediamtx itself runs as takwerx so this is fine for both
```

Logged as `✓ Web editor writable paths chowned to takwerx (backups + /opt/mediamtx-webeditor + mediamtx.yml)`.

2. **Startup migration `_heal_mediamtx_webeditor_writable_paths()`** wired into `_startup_migrations()`. On every console boot, if `/opt/mediamtx-webeditor` exists AND `mediamtx-webeditor.service` is loaded but NOT active, re-apply the chowns and `systemctl restart mediamtx-webeditor`. Verifies the service is active after the restart and logs the result. **Skips entirely on healthy boxes** (service is active → no-op). This means a tak-test-4-class box (fresh-deployed on v0.9.28, currently in fail-loop) self-heals on the next `Update Now` without operators having to redeploy MediaMTX from the console.

**Operator impact:** zero. Update Now → console reboot → migration fires once on boxes in fail-loop → service comes up → `stream.<domain>/` reaches the web editor login page. Idempotent on healthy boxes.

**Forensic data on `tak-test-4` 2026-05-18:**

```
06:21:13  ✓ Services enabled and started  ← user redeployed MediaMTX
07:15:45  PermissionError: '/usr/local/etc/mediamtx_backups'  ← restart counter 262
07:15:50  PermissionError: '/usr/local/etc/mediamtx_backups'  ← restart counter 263
07:15:56  PermissionError: '/usr/local/etc/mediamtx_backups'  ← restart counter 264
07:17:47  (live chown applied)  ← service started clean
07:17:47  * Running on http://127.0.0.1:5080  ← Flask up, port bound, login page served
```

After the live `chown -R takwerx:takwerx /opt/mediamtx-webeditor /usr/local/etc/mediamtx_backups /usr/local/etc/mediamtx.yml` + `systemctl restart mediamtx-webeditor`, `curl :5080/` returned HTTP 302 → `/login` (proper editor response), and the stream.* domain became reachable through Caddy + Authentik.

---

## What's not in this release (and why)

- **MediaMTX CRITs (01, 02, 03, 04, 05, 09).** Different repo. Operator owns separately.
- **CRIT-06 code change.** The cited line numbers in the audit (`app.py:7932, 7962`) no longer apply — file is ~46k lines and has shifted since the auditor's snapshot. Current code uses `_validate_ssh_target` (validates host/user/port against IP/DNS and POSIX username regex before any SSH invocation) and `shlex.quote` at every settings-derived interpolation in FedHub deploy / cert workflows. Documented in `SECURITY-AUDIT-RESPONSE-2026-04.md` rather than re-changed.
- **Full `shell=True` defense-in-depth sweep.** Tracked for v0.9.30+. The audit re-cited stale line numbers without re-verifying; we need to re-map the current threat surface before action.
- **CSP `'unsafe-inline'` removal.** Requires nonce-injection across ~25 `render_template_string` callsites. Scoped as a UI hardening sprint.
- **Authentik header-spoof check.** `infra-TAK` does not consume `X-Authentik-*` headers for auth (session-based with hashed passwords in `auth.json`). The MediaMTX CRIT-05 attack surface lives in their LDAP overlay, not here. Tracked as a belt-and-braces Caddyfile change for v0.9.30+.

---

## Changes

### Item 1 — CRIT-08 fixed: SSH host-key pinning in guarddog scripts

Replaced `-o StrictHostKeyChecking=no -o ConnectTimeout=10` with `-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/opt/tak-guarddog/known_hosts -o ConnectTimeout=10` across:

- `scripts/guarddog/tak-remotedb-watch.sh` (3 sites)
- `scripts/guarddog/tak-retention-guard.sh` (2 sites)
- `scripts/guarddog/tak-remotedb-auth-watch.sh` (1 site)
- `scripts/guarddog/tak-fedhub-watch.sh` (1 site)
- `scripts/guarddog/tak-db-repack.sh` (2 sites)
- `scripts/guarddog/tak-cotdb-watch.sh` (1 site)
- `scripts/guarddog/tak-auto-vacuum.sh` (2 sites)

Also migrated two raw `subprocess.run(['ssh', ...])` callsites in `app.py` (snapshot pg_dump, rollback pg_restore) from `=no` to `=accept-new`.

**Provisioning of `/opt/tak-guarddog/known_hosts`** (mode 0600, root:root) added to:

- the main Guard Dog deploy thread (right after directory creation)
- `_sync_guarddog_remote_db_from_settings` (right after `guarddog.conf` write)

Existing fleet boxes will pick up the empty file on next Guard Dog deploy or settings sync. First SSH cycle pins the DB host key; subsequent cycles refuse on mismatch.

**Operator escape hatch (host key rotation):** clear the pin with

```bash
sudo bash -c ': > /opt/tak-guarddog/known_hosts'
```

Guard Dog re-pins on the next watchdog cycle. No need to restart services.

### Item 2 — CRIT-07 cleaned up: `eval` removed from guarddog scripts

Three scripts had `eval "$(python3 ... shlex.quote ...)"` to load `/opt/tak-guarddog/guarddog.conf` values into shell variables. Blast radius was root-only (config is root-owned) but `eval` is bad form. Replaced with newline-delimited `read`:

```bash
{
  IFS= read -r TWO_SERVER_MODE
  IFS= read -r REMOTE_DB_HOST
} < <(python3 - <<'PY'
...
print(two)
print(host)
PY
)
TWO_SERVER_MODE="${TWO_SERVER_MODE:-0}"
```

Affected: `tak-db-repack.sh`, `tak-auto-vacuum.sh`, `tak-retention-guard.sh`.

Also replaced `eval "$cmd"` in the local branch of `remote_cmd()` in `tak-db-repack.sh` with `bash -c "$cmd"`. Same shell-interpretation semantics, no double-eval trap.

### Item 3 — `atakatak` default-password nag

Added `render_default_cert_password_warning(settings)` in `app.py`. Renders an orange dismissable banner across every console page when:

- TAK Server is installed (`/opt/tak/CoreConfig.xml` present), AND
- `tak_cert_password` is unset or equals `atakatak`, AND
- operator has not dismissed (`cert_pw_warning_dismissed`).

Wired into the existing `inject_cloudtak_icon` context processor. Dismiss is a POST to `/api/security/dismiss-cert-pw-warning` (login-required, same-origin CSRF gate). The flag auto-clears when a non-default password is later saved via `takserver_set_cert_password`, so accidental reverts to default re-trigger the nag.

### Item 4 — CSP: `'unsafe-eval'` removed

`apply_security_headers` in `app.py`:

```python
# v0.9.29 (security audit LOW): dropped 'unsafe-eval' — codebase has no
# eval()/new Function()/string-form setTimeout. Inline scripts still
# require 'unsafe-inline' (template surgery deferred to nonces).
"script-src 'self' 'unsafe-inline' https:; "
```

Pre-flight verified: zero `eval()` / `new Function()` / string-form `setTimeout` / string-form `setInterval` callsites in `static/*.js`, inline `<script>` content rendered from templates, or `app.py` itself.

`'unsafe-inline'` removal deferred to v0.9.30+ (requires nonces).

### Item 5 — Audit response doc

Created `docs/SECURITY-AUDIT-RESPONSE-2026-04.md` with:

- Per-repo split of all 74 findings (`infra-TAK` / `mediamtx-installer` / `takwerx` / unscoped).
- CRIT-06 mitigation map with current-file line references.
- "Already-addressed defaults" table mapping every generic HIGH/MED finding to current `app.py` line numbers (CSRF, session flags, secret_key persistence, rate limiting, security headers, etc.).
- Outstanding work for v0.9.30+ (track-only).
- Validation gate definition.

### Item 6 — Authentik MAX_REQUESTS cascade short-circuit

**Background — what happened on `tak-10` (test12), 2026-05-18:**

While soaking v0.9.28-alpha on the three-box dev fleet, a basic soak script
that only counted *running containers* missed a quietly-recurring server+worker
recreate every ~31 minutes for 10+ hours. Deep audit revealed the v0.9.23
MAX_REQUESTS autotuner climbing back to baseline after a real leak burst had
floored it the day before:

```
 16:20 UTC  MAX_REQUESTS=1000  (baseline)
 16:26 UTC  → 500  (TUNE DOWN, watchdog fire #1, halve)
 16:30 UTC  → 250  (TUNE DOWN, halve)
 16:36 UTC  → 125  (TUNE DOWN, halve)
 22:41 UTC  → 100  (TUNE DOWN, floored)
              ↓ 6 hours of quiet, no fires
 23:13 UTC  → 125  (TUNE UP, +25%)
 23:44 UTC  → 156  (TUNE UP, +25%)
 …
 03:53 UTC  → 1000 (TUNE UP, baseline reached, cascade ends)
```

Each tune-UP step triggers `_recreate_authentik_server_worker` to make
gunicorn pick up the new `MAX_REQUESTS` env var (which gunicorn only reads
at start). Cost: **10 unnecessary server+worker recreates over 7 hours**,
each dropping the LDAP outpost websocket for 1-5s (visible in Caddy logs
as 502s on `/api/v3/flows/executor/ldap-authentication-flow`).

**Why the existing PgBouncer install path didn't catch this:**

The v0.9.23 PgBouncer install function (`_ensure_authentik_pgbouncer`) was
*supposed* to reset MAX_REQUESTS to baseline on install ("autotune floor=100
is no longer load-bearing with PgBouncer in place" — line 27983-27986 of
`app.py`). But that reset only fires on FRESH installs. Boxes that were
already-stuck-at-floor when PgBouncer landed (because they hit the leak
pressure first, then got upgraded) never got the reset — they took the
7-hour autotuner cascade instead.

**Fix:**

New idempotent startup migration `_patch_authentik_max_requests_snap_to_baseline_if_pgbouncer`
runs on every console start. Gated:

- ~/authentik/.env exists
- PgBouncer is installed in compose AND wired in .env
- current MAX_REQUESTS < baseline (`authentik_max_requests_baseline`, default 1000)
- no SAFETY NET firing in last `_AUTHENTIK_MAX_REQUESTS_QUIET_WINDOW_S` (default 6h)
- no autotune apply in last `_AUTHENTIK_MAX_REQUESTS_TUNE_UP_COOLDOWN_S` (default 30min)

All conditions met → writes baseline to .env, **single server+worker recreate**,
clears autotune fire history, records the snap in `authentik_max_requests_tune_history`
with `reason='snap-to-baseline-pgbouncer-installed-quiet'`.

**Cost reduction:** 10 recreates × ~30s per recreate × ~30s LDAP-outpost
recovery → **single ~30s recreate**.

**Field-state at release time:** all three dev boxes (test12, test8, test6)
already at MAX_REQUESTS=1000 (baseline). test12 finished its natural cascade
in soak before this fix landed; test8/test6 were never at floor. The fix is
therefore preventive — silent no-op on the current fleet, kicks in only if
any future box (operator install or recovering production) ever drops below
baseline with PgBouncer present.

Sister helper `_estimate_cascade_recreates(cur, baseline)` is a pure function
that computes how many +25%/30min steps the autotuner would have taken from
`cur` to `baseline`, used purely for log-line accounting ("short-circuited
~10 +25%/30min tune-UP recreates").

### Item 7 — TAK LDAP CoreConfig verifier: XML namespace-prefix tolerance

**Symptom (tak-test-4, fresh Azure deploy on v0.9.28 main, 2026-05-18):**
operator connected TAK Server to LDAP through the console workflow, got the
post-sync banner:

```
✓ LDAP wiring synced
  • authentik: 200 OK (~340ms)
  • takserver: coreconfig=FAIL  ready=NO
```

`/opt/tak/CoreConfig.xml` was actually correct: `<ns0:auth default="ldap">…
<ldap url="ldap://127.0.0.1:389" serviceAccountDN="cn=adm_ldapservice,…"/></ns0:auth>`.
`ldapsearch -x -H ldap://127.0.0.1:389 -D cn=adm_ldapservice,ou=users,dc=takldap …`
succeeded against the running OpenLDAP container. The banner was lying.

**Root cause:** TAK Server 5.7-RELEASE-32-HEAD writes `CoreConfig.xml` in
canonical form on restart, applying the document's default namespace as the
`ns0:` prefix on every element it owns:

```xml
<ns0:Configuration xmlns:ns0="http://bbn.com/marti/xml/config">
  <ns0:auth default="ldap" x509groups="true" …>
    <ns0:File location="UserAuthenticationFile.xml"/>
    <ldap url="ldap://127.0.0.1:389" serviceAccountDN="cn=adm_ldapservice,…"/>
  </ns0:auth>
</ns0:Configuration>
```

Our verifier `_coreconfig_has_ldap()` was scanning with
`content.lower().find('<auth')` and `find('</auth>')` — neither matches the
`<ns0:auth>` / `</ns0:auth>` canonical form, so it returned False even
though LDAP was correctly wired.

**Fix:** the verifier now matches both shapes with a single regex
`<(?:[A-Za-z][\w-]*:)?auth\b` (and the corresponding closing tag), and the
inner `<ldap>` / `default="ldap"` checks use the same namespace-tolerant
pattern. The `adm_ldapservice` cookie inside the block is the source of
truth for "this auth block is actually LDAP-wired, not just `<auth
default="file">`-with-LDAP-element-leftover-from-an-aborted-setup".

Defensively patched the same regex bug in `_apply_coreconfig_ldap_auth_text()`
(the legacy text-based fallback writer, only invoked when ElementTree parsing
fails). On namespace-prefixed canonical XML the old regex would silently
no-op and return "format not recognized" — now it succeeds.

**Unit-tested** against 5 XML shapes before deploy:

| Case                                                                          | v0.9.28 | v0.9.29 | Expected |
|-------------------------------------------------------------------------------|---------|---------|----------|
| tak-test-4 actual (`<ns0:auth default="ldap">…<ldap …adm_ldapservice…/>`)     | False ✗ | True ✓  | True     |
| Older TAK (no namespace, `<auth default="ldap">…<ldap …/>`)                   | True    | True    | True     |
| `<auth default="file">…<File/>` only — no LDAP at all                         | False   | False   | False    |
| `<ns0:auth default="file">` with leftover `<ldap/>` element from aborted setup | False   | False   | False    |
| No `<auth>` element (broken CoreConfig)                                       | False   | False   | False    |

All 5 pass after the fix; v0.9.28 silently mis-classifies the first case
(the only one operators hit on fresh TAK 5.7-R32+ deploys).

**Operator impact for tak-test-4 and other affected fresh deploys:** after
Update Now to v0.9.29, the next LDAP connect-sync banner reports
`coreconfig=OK ready=YES` and the operator can stop chasing a non-bug.
LDAP functionality was never broken; only the verification was.

### Item 8 — Cloud public-IP detection on fresh install

**Symptom (same tak-test-4, Azure):** operator browsed to
`https://<azure-public-ip>:5001` after `sudo ./start.sh`, console loaded,
but `Settings → Server IP` displayed the Azure VM's *internal* IP
(`10.0.0.x`), not the public IP. SSL cert SAN also encoded only the
private IP. Caddy public bootstrap and TAK Server external reachability
both build on `settings.server_ip`, so all of them silently inherit the
wrong address — operator either tolerates an SSL warning forever or
manually edits Settings on every fresh cloud install.

**Root cause:** `start.sh` used `hostname -I | awk '{print $1}'` twice
(settings.json write and openssl SAN generation). On cloud VMs `hostname
-I` reports the interface IP, which is always the private/internal NIC.
The script ALREADY had public-IP-via-`api.ipify.org` logic at the very
end (purely for the access-URL display) but never used it for the
persisted config.

**Fix:** new `detect_server_ip()` helper near the top of `start.sh`,
called from both sites. Detection order, each step with its own timeout
and silent fall-through:

1. Azure Instance Metadata Service
   (`http://169.254.169.254/metadata/.../publicIpAddress?api-version=…&format=text`)
   — link-local, no internet egress required, no auth needed
2. AWS Instance Metadata Service v1
   (`http://169.254.169.254/latest/meta-data/public-ipv4`)
   — token-less, still works on older AMIs; on token-required AMIs it
   falls through to step 3
3. `https://api.ipify.org` (external echo)
4. `https://ifconfig.me` (external echo, second-source)
5. Fallback to `hostname -I | awk '{print $1}'` (preserves on-prem /
   no-public-IP behavior)

Final result is validated against the IPv4 dotted-quad regex
`^[0-9]{1,3}(\.[0-9]{1,3}){3}$` before being trusted — broken/HTML/empty
responses fall through to the next source.

**Smoke-tested** on this developer Mac before deploy:

- IPv4 validation correctly accepts `20.115.4.137`, `54.244.13.5`,
  `192.168.1.1`; rejects empty, HTML, DNS-strings, truncated `23.45`,
  `not.an.ip.at.all`.
- Live call on a NAT-behind-CG machine returned the developer's actual
  WAN IP via `api.ipify.org` (Azure/AWS IMDS correctly 404'd in 2 s and
  fell through).

**Operator impact:**

- **Fresh cloud installs (new operator running `start.sh` on Azure/AWS/GCP):**
  `settings.server_ip` and the self-signed cert SAN now contain the
  public IP from first boot. No manual Settings edit needed.
- **Existing installs already on v0.9.28-or-earlier on cloud VMs:**
  new startup migration `_patch_settings_server_ip_prefer_cloud_public`
  auto-corrects `settings.server_ip` on the next console restart after
  Update Now. Two-stage detection:
  1. **Cloud-VM gate** — `_detect_cloud_environment()` proves we're on a
     cloud VM via IMDS reachability (link-local `169.254.169.254` only
     responds on Azure / AWS, never on bare-metal / laptops / CGNAT
     home servers / corp LANs).
  2. **Public-IP detection** — first ask IMDS for a public IP directly
     attached to the NIC (highest confidence). If IMDS reports none
     (Azure VM behind Load Balancer / NAT Gateway — tak-test-4's
     case), fall through to `api.ipify.org` / `ifconfig.me`. This
     fall-through is safe BECAUSE the cloud-VM gate has already passed
     (echo services would be too permissive on bare-metal alone,
     but the cloud-VM precondition rules that case out).

  Gating (all must be true to apply):
  - current `settings.server_ip` is in RFC 1918 private ranges
    (`10/8`, `172.16/12`, `192.168/16`)
  - `_detect_cloud_public_ip()` returns a valid IPv4 from one of:
    `azure-imds-direct` (Azure NIC has Public IP attached),
    `aws-imds-direct` (AWS NIC has Public IP attached),
    `azure-nat` (Azure VM behind LB / NAT Gateway — the tak-test-4
    pattern, very common), or `aws-nat` (AWS VM behind NAT Gateway)
  - the detected public IP differs from current
  - migration hasn't already been applied (audit blob in
    `settings.json → server_ip_auto_corrected_migration`)

  **Field-validated on tak-test-4 (Azure):**
  - Azure IMDS reports `publicIpAddress: ""` (VM has no direct public IP
    on the NIC — Azure routes external traffic via the subscription's
    NAT Gateway or Load Balancer)
  - `api.ipify.org` returns `20.112.88.218` (the actual ingress IP for
    external clients)
  - Migration result: `server_ip` rewritten `10.0.0.5 → 20.112.88.218`,
    audit blob `source='azure-nat'`.

  If the operator actually wanted the private IP (VPN-only deploy etc.),
  they paste it back in Settings → Server IP and Save — the migration is
  one-shot and never re-fires (audit blob is recorded).

- **On-prem / no-public-IP / private-only deployments:** unchanged
  behavior. `start.sh` falls back to `hostname -I` at install time, and
  the auto-correct migration is a no-op (Azure/AWS IMDS times out → no
  definitive cloud signal → gate fails closed).

---

## Files touched

```
modified:   app.py                                              (VERSION bump + 7 security patches + cascade-fix migration + LDAP verifier namespace fix + text-fallback patcher fix)
modified:   start.sh                                            (cloud public-IP detection helper, used in 2 sites)
modified:   scripts/guarddog/tak-auto-vacuum.sh                 (CRIT-07 + CRIT-08)
modified:   scripts/guarddog/tak-cotdb-watch.sh                 (CRIT-08)
modified:   scripts/guarddog/tak-db-repack.sh                   (CRIT-07 + CRIT-08)
modified:   scripts/guarddog/tak-fedhub-watch.sh                (CRIT-08)
modified:   scripts/guarddog/tak-remotedb-auth-watch.sh         (CRIT-08)
modified:   scripts/guarddog/tak-remotedb-watch.sh              (CRIT-08)
modified:   scripts/guarddog/tak-retention-guard.sh             (CRIT-07 + CRIT-08)
new:        .cursor/rules/no-main-merge-no-tag-without-permission.mdc
new:        docs/SECURITY-AUDIT-RESPONSE-2026-04.md
new:        docs/PLAN-v0.9.29-alpha.md                          (rewrote; security + cascade-fix + LDAP/IP fixes scope)
new:        docs/PLAN-v0.9.30-alpha.md                          (Node-RED multipart, shifted)
new:        docs/RELEASE-v0.9.29-alpha.md                       (this file)
```

`app.py` patches (in order):

1. `render_default_cert_password_warning` helper added after `render_custom_banner`.
2. `inject_cloudtak_icon` context processor concatenates the nag.
3. `/api/security/dismiss-cert-pw-warning` POST endpoint added near customization routes.
4. `takserver_set_cert_password` auto-clears the dismiss flag on non-default save.
5. `apply_security_headers` CSP dropped `'unsafe-eval'`.
6. `_sync_guarddog_remote_db_from_settings` provisions `/opt/tak-guarddog/known_hosts` (0600, root:root).
7. Guard Dog deploy thread also provisions `known_hosts` on first install.
8. Two raw-SSH callsites (snapshot pg_dump, rollback pg_restore) switched from `=no` to `=accept-new`.
9. `_patch_authentik_max_requests_snap_to_baseline_if_pgbouncer` migration + `_estimate_cascade_recreates` helper added after `_patch_authentik_web_max_requests_to_1000` (line ~26847), wired into `_startup_migrations` after `_ensure_authentik_pgbouncer_pool_size`.
10. `_coreconfig_has_ldap()` (verifier) — namespace-prefix tolerant regex.
11. `_apply_coreconfig_ldap_auth_text()` (legacy text-fallback patcher) — same namespace-prefix tolerance, defensive.
12. `_ip_is_rfc1918_private()` + `_detect_cloud_environment()` + `_detect_cloud_public_ip()` + `_patch_settings_server_ip_prefer_cloud_public()` — auto-correct migration for existing-install cloud VMs whose `settings.server_ip` is the private interface IP. Two-stage detection (cloud-VM gate via IMDS reachability + public-IP detection that falls through to echo services on Azure/AWS NAT'd VMs). Wired into `_startup_migrations` after the MAX_REQUESTS snap-to-baseline.
13. `VERSION` bumped `"0.9.28-alpha"` → `"0.9.29-alpha"`.

`start.sh` patches:

1. New `detect_server_ip()` helper near the top of the script. Order: Azure IMDS → AWS IMDS → `api.ipify.org` → `ifconfig.me` → `hostname -I`. Validates IPv4 dotted-quad before trusting.
2. `SERVER_IP=$(hostname -I | awk '{print $1}')` → `SERVER_IP=$(detect_server_ip)` at the settings.json write site.
3. Same swap at the openssl SAN-generation site (so the self-signed cert SAN matches what operators browse to).

---

## Validation plan

Per [`.cursor/rules/fleet-uniform-config.mdc`](../.cursor/rules/fleet-uniform-config.mdc):

1. Push v0.9.29 to `dev`.
2. Pull on `tak-10`, `test8`, and `responder` (`infratak-vps`) via Update Now. **No manual config edits on any box.**
3. On each box:
   - Confirm `cat /opt/tak-guarddog/known_hosts` exists and is empty (or has entries if prior runs already pinned).
   - Trigger a watchdog cycle (e.g. wait for `tak-remotedb-watch.timer` or `systemctl start tak-remotedb-watch.service`).
   - Confirm `cat /opt/tak-guarddog/known_hosts` is non-empty after the first cycle (key pinned).
   - Tail `/var/log/takguard/restarts.log` for at least 60 min: zero `permission denied (publickey)`, zero `host key verification failed`, no new alert behavior.
4. Browser-check the console with DevTools open: no CSP `unsafe-eval` violations across `/`, `/takserver`, `/caddy`, `/authentik`, `/nodered`, `/cloudtak`, `/marketplace`, `/guarddog`, `/firewall`, `/log-tools`.
5. Visual check the `atakatak` nag:
   - Fresh install (no password override): orange banner appears at top of every page.
   - Click Dismiss: banner disappears immediately; refresh — banner stays gone.
   - Go to TAK Server → Save Password with a new non-default password: dismiss flag clears in settings.json.
   - Manually reset to `atakatak` in settings.json: banner re-appears on next page load.
6. **MAX_REQUESTS cascade short-circuit verification:**
   - `grep AUTHENTIK_WEB__MAX_REQUESTS /root/authentik/.env` on each box: expect `1000` (the baseline).
   - `journalctl -u takwerx-console --since "10 min ago" | grep snap-to-baseline`: expect either zero matches (already-at-baseline path is silent) OR a single APPLY line on a box that was previously below baseline. Multiple APPLY lines on the same box across reboots = bug.
   - In the 60-min soak, `journalctl --since "1 hour ago" | grep -c "authentik recreate: starting.*max-requests-autotune"` should drift to 0 on every box (no further cascade-triggered recreates).
   - Sanity: `python3 -c "import settings_loader; print(load_settings().get('authentik_max_requests_snap_migration'))"` — if the migration fired, a `success` outcome dict appears.
7. **TAK LDAP verifier (namespace-prefix tolerance) — primary repro is tak-test-4:**
   - On `tak-test-4` (Azure, was on v0.9.28 main): Update Now → wait for restart → click TAK Server → LDAP → Connect / Sync.
   - Expected banner: `takserver: coreconfig=OK  ready=YES` (previously `coreconfig=FAIL ready=NO` on the same CoreConfig.xml content).
   - `grep -E '<(ns[0-9]+:)?auth' /opt/tak/CoreConfig.xml` should show `<ns0:auth default="ldap" …>` — the canonical namespace-prefixed form that v0.9.28 mis-classified.
   - On already-working dev fleet (test12/test8/test6): no behavior change — they were on pre-canonical CoreConfig.xml. Verifier still passes.
8. **Cloud public-IP on fresh install — separate Azure VM:**
   - Spin up a clean Azure Ubuntu VM with a Public IP resource attached.
   - `git clone … && sudo ./start.sh`.
   - After bootstrap: `cat .config/settings.json | python3 -m json.tool | grep server_ip` should show the **public** IP (e.g. `20.115.x.x`), not the private `10.0.0.x`.
   - `openssl x509 -in .config/ssl/console.crt -text -noout | grep -A1 'Subject Alternative Name'` should list the **public** IP in the SAN.
   - End-of-bootstrap "Access (public): https://x.x.x.x:5001" line should match.
   - **Fallback test (cosmetic):** clone a copy onto an on-prem laptop with no public IP; bootstrap should fall through Azure IMDS / AWS IMDS / api.ipify / ifconfig.me / `hostname -I` and write the LAN IP. (api.ipify.org WILL return the operator's public WAN IP on most laptops — that's also fine; the SSL cert is local-only.)
9. **Auto-correct migration for existing cloud installs (tak-test-4 is the primary repro):**
   - tak-test-4 was running v0.9.28 main on Azure with `settings.server_ip = 10.0.0.x` (the Azure private interface IP). Pulling v0.9.29-alpha + restarting:
   - `journalctl -u takwerx-console --since "5 min ago" | grep "server_ip auto-correct"` should show TWO log lines: one "detected azure public IP X.X.X.X, current settings.server_ip is private (10.0.0.x) — updating", and one "applied. 10.0.0.x → X.X.X.X (source=azure)".
   - `python3 -c "import json; print(json.load(open('.config/settings.json'))['server_ip'])"` should now show the **public** IP.
   - The Console → Settings UI should reflect the public IP.
   - Restart the console a second time: no new auto-correct log lines (idempotent — already-recorded audit blob short-circuits).
   - On the dev fleet (test12 / test8 / test6) the migration should silently no-op: their `settings.server_ip` was set when those boxes were Vultr-style (no Azure/AWS IMDS responses), and the gate fails closed.
   - Operator escape-hatch test: manually edit `.config/settings.json` back to a private IP and restart. The migration should NOT re-fire (because `server_ip_auto_corrected_migration.applied=true` is recorded). Operator wanted private — operator gets private.

Soak gate: ≥ 60 min stable on all three boxes with no manual config edits, AND tak-test-4 reports `coreconfig=OK ready=YES`, AND a fresh Azure VM bootstraps with the public IP in settings/cert. Only then merge `dev` → `main`.

---

## Operator-visible changes

**For most operators: nothing changes.** Guard Dog watchdogs continue to work as before; the only behavioral difference is the first SSH cycle after upgrade will write a host-key entry into `/opt/tak-guarddog/known_hosts`. Subsequent cycles use the pinned key.

**The orange banner is new** for boxes that still have `tak_cert_password = "atakatak"` (i.e. never set a custom cert export password). Click Dismiss to hide, or change the password under **TAK Server → Certificates → Save Password**.

**Authentik MAX_REQUESTS cascade fix:** boxes where the v0.9.23 autotuner had previously halved MAX_REQUESTS down toward the floor (e.g. during a leak burst pre-PgBouncer) get a single `authentik-server-1` + `authentik-worker-1` recreate at console startup as MAX_REQUESTS is snapped back to baseline=1000. **Most fleet boxes are already at baseline and see no change** (the migration silently no-ops). On boxes where it fires, the LDAP outpost websocket drops for 1-5s once at startup, then stable — saving the ~7-hour, 10-recreate climb the autotuner would otherwise take.

**TAK LDAP verifier fix:** if you ever ran the LDAP connect-sync workflow and saw `coreconfig=FAIL ready=NO` even though LDAP login actually worked, that was the false-negative we fixed. After Update Now, re-run the LDAP connect on TAK Server → LDAP and the banner reports `coreconfig=OK ready=YES` against the same `CoreConfig.xml`. No CoreConfig edits required.

**Cloud public-IP fix:** affects only **new** cloud-VM installs from `start.sh`. If you're already running infra-TAK on Azure/AWS/GCP and `Settings → Server IP` shows your private IP (`10.x.x.x` / `172.16.x.x` / `192.168.x.x`), paste the public IP into Settings once and Save. We don't rewrite `settings.server_ip` automatically because some operators legitimately deploy on private-only LANs.

**No database changes, no TAK Server changes.** Guard Dog script swap, cosmetic nag, tighter CSP, one preventive Authentik migration, one LDAP verifier fix, one cloud-IP detection helper.

---

## References

- [`docs/SECURITY-AUDIT-RESPONSE-2026-04.md`](SECURITY-AUDIT-RESPONSE-2026-04.md) — full per-finding response
- [`docs/PLAN-v0.9.29-alpha.md`](PLAN-v0.9.29-alpha.md) — implementation plan
- [`docs/PLAN-v0.9.30-alpha.md`](PLAN-v0.9.30-alpha.md) — Node-RED multipart polygon (moved)
- OpenSSH `StrictHostKeyChecking` reference: [`man ssh_config(5)`](https://man.openbsd.org/ssh_config.5#StrictHostKeyChecking)
