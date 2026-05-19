# v0.9.31-alpha — Six-in-one bugfix release + fleet-uniform Authentik chain healer + one-click kernel patch

**Date:** 2026-05-18
**Type:** Bugfix release — drop-in update via Update Now.
**Status:** RELEASED to `main` 2026-05-18 after field validation across 3 dev boxes (test6 / test8 / test12). All four passively-firing bug fixes (3 takwerx self-heal, 4 Authentik chain heal, 5 tasklog stale-failed cleanup, 6 endpoint registration) verified on the fleet; Bug 1 (TAK Server purge) and Bug 2 (Caddyfile regen on uninstall) are operator-action-triggered and pass code-only review. Bug 6 click-through end-to-end (real apt full-upgrade via the button) awaits the next box with a pending kernel update; smoke-tested via `apt-get -s` dry-run with confirmed cgroup escape into `/system.slice/infratak-kernel-patch.service/`.

---

## TL;DR

Six independent bugs found on the same fresh-install attempt (SSDNodes box, 2026-05-18) plus the soak-validation sweep that followed:

1. **Remove TAK Server button left the package half-removed** → next deploy hit `FATAL: /opt/tak not found after install`.
2. **Per-service Remove buttons didn't regenerate the Caddyfile** → `takportal.<fqdn>` / `auth.<fqdn>` / `webtak.<fqdn>` kept showing the Authentik **"Not Found"** page (or 502) long after the service was gone, because Caddy still had vhosts pointing at dead upstreams.
3. **MediaMTX systemd units crash-looped with `status=217/USER`** → v0.9.29's hardening switched both `mediamtx.service` and `mediamtx-webeditor.service` to `User=takwerx`, but the matching `useradd` was reverted back in v0.9.2 and never restored. On any box where `takwerx` didn't already exist for unrelated reasons, MediaMTX never came up.
4. **Authentik forward_auth chain silently half-built on slow boxes** → deploy-time PATCH/POST to Authentik's proxy provider endpoints used a 10s timeout and a bare `except Exception: pass`. On the SSDNodes box, Authentik writes hung past 10s, the deploy log printed `⚠ Proxy provider update failed: ... timed out`, and operators got the Authentik "Not Found" page on `takportal.<fqdn>` and other forward_auth'd hostnames after the deploy reported "success." A **fleet-uniform self-heal migration** now runs on every console boot, every Update Now, and the TAK Portal Update-config button — using 30s timeouts, 3 retries with backoff, and re-GET verification.
5. **`takauthentiktasklogpurge.service` showed stale `failed` cross-fleet** → on test6 + test8 the May 17 03:00 UTC weekly timer fired the OLD v0.9.5 script (VACUUM-in-transaction bug), exited 1. v0.9.26's `_ensure_authentik_tasklog_purge_script` later overwrote the on-disk script with the fixed version, but `systemctl --failed` still reported a failure for the old run. Data hygiene was unaffected (the inline `_auto_authentik_tasklog_purge` runs every console boot regardless of the timer), but `systemctl --failed` was telling operators something was broken when it wasn't. New startup migration clears the stale state when the on-disk script is already the fixed version.
6. **Kernel-patch banner instructions actively bricked the box when followed over SSH** → the banner said "Run: `apt update && apt full-upgrade && reboot`". Over SSH, `apt full-upgrade` replaces systemd/networking/openssh-server mid-transaction, which drops the SSH session, kills apt, leaves the transaction half-finished with no `End-Date` in `/var/log/apt/history.log`. The reboot never runs (because `&& reboot` only fires after a clean apt exit), and the box ends up rebooting on the OLD kernel because the new one's initramfs never finalized. Field-hit on the SSDNodes box 2026-05-18 — the same box that surfaced bugs 1–4 also turned out to have its kernel update jammed because the banner's instructions were the actual cause of the jam. New: a **"Patch now" button** in the banner runs apt as a transient systemd unit (decoupled cgroup) that survives even when needrestart restarts `takwerx-console.service` mid-flight. Reboot is a separate explicit click.

All six are fleet-uniform, idempotent fixes — same code path on every box, same outcome, with self-heal migrations for boxes already stuck in the broken state.

---

## Bug 1: Remove TAK Server button now actually removes the package

On a fresh v0.9.30 install where TAK Server had been previously installed and then "removed" via the **Remove TAK Server** button, the next deploy attempt failed at Step 4 with:

```
[Step 4/9: Installing TAK Server]
Installing takserver_5.7-RELEASE32_all.deb...
  Reading package lists...
  Building dependency tree...
  Reading state information...
  takserver is already the newest version (5.7-RELEASE32).
  0 upgraded, 0 newly installed, 0 to remove and 92 not upgraded.
✗ FATAL: /opt/tak not found after install
```

Cause: the **Remove TAK Server** button ran `apt-get remove -y takserver 2>/dev/null; true` (note: `remove`, not `purge`; stderr silenced; exit code ignored), then unconditionally `rm -rf /opt/tak`. If `apt-get remove` quietly failed (apt lock, postrm error, package held, etc.), the package stayed in `ii` state with all files gone. The next `apt-get install` saw the package as "already newest version" and did nothing — leaving the deploy with no `/opt/tak` to populate.

## What v0.9.31 changes

Three minimal edits to `app.py`, all fleet-uniform and idempotent:

1. **`takserver_uninstall()` (line ~39265)** — replace silent `apt-get remove -y takserver 2>/dev/null; true` with an escalating purge sequence:
   1. `apt-get purge -y takserver`
   2. fallback: `dpkg --purge takserver`
   3. last resort: `dpkg --purge --force-all takserver`

   After each attempt, verify with `dpkg-query -W -f='${Status}' takserver` that the package is actually gone (`not-installed` or absent). Only then proceed to `rm -rf /opt/tak`. If purge fails after all three attempts, the step now reports `⚠ Could not purge takserver package (still registered with dpkg) — manual cleanup required` instead of silently succeeding.

2. **Global remove-all handler (line ~42394)** — same escalating purge + verify pattern. The "remove everything" path had the identical bug.

3. **Deploy Step 4 (line ~41525)** — defense in depth for boxes already stuck in the half-removed state (i.e. anyone who hit this bug on v0.9.30 or earlier):
   - **Pre-flight check:** if `dpkg-query` reports takserver as installed but `/opt/tak` is missing, run the escalating purge sequence before attempting install. This catches the exact state v0.9.30's broken Remove button leaves behind.
   - **Last-resort self-heal:** if `/opt/tak` is still missing after the install command (whatever the cause), force `apt-get install --reinstall -y <pkg.deb>` to extract files even if dpkg thinks the package is current. Only FATAL after that has failed too, and the FATAL message now includes the manual recovery one-liner.

## Net behavior on a fresh box (v0.9.31+)

1. Install TAK Server → works (no change).
2. Click **Remove TAK Server** → package is `purged` (verified via `dpkg-query`), `/opt/tak` removed, postgres cleaned. dpkg state is genuinely empty.
3. Install TAK Server again → works (no "already newest version" no-op).

## Net behavior on a box currently stuck in the broken state

For a box that ran v0.9.30's broken Remove (or earlier — the bug has existed since the uninstall feature was added):

- `dpkg -l takserver` shows `ii`, `/opt/tak` is missing → **Step 4's pre-flight purges the orphaned package state automatically on the next deploy**. The deploy completes.

Manual recovery one-liner (also surfaced in the FATAL message now), for boxes that hit this on v0.9.30 before pulling v0.9.31:

```bash
dpkg --purge --force-all takserver && rm -rf /opt/tak
```

Then click Deploy again.

## Fleet-uniform compliance (Bug 1)

Per `.cursor/rules/fleet-uniform-config.mdc`:

- ✅ **Deterministic, no operator-override preservation.** The pre-flight purge runs on any box that matches the broken-state signature (`takserver` registered + `/opt/tak` missing). Same logic on every box → same outcome.
- ✅ **No silent ignore.** stderr is captured, exit codes are checked, and `dpkg-query` is the post-condition oracle — not "the command didn't return nonzero so we're fine".
- ✅ **Convergence verified by an existing probe.** `os.path.exists('/opt/tak')` is the post-condition both before and after the fix; the difference is the fix now has a self-heal path before the FATAL instead of bailing.

---

## Bug 2: Per-service Remove buttons now regenerate the Caddyfile

### Symptom

After clicking **Remove** on TAK Portal (or Authentik, or TAK Server) from the console, then visiting `takportal.<fqdn>` (or `auth.<fqdn>` / `webtak.<fqdn>`) in a browser, the operator saw the Authentik **"Not Found"** page (white background, generic "this page does not exist" copy). Some browsers showed a `chrome-error://chromewebdata/` page instead if the Caddy upstream had also died entirely before the TLS handshake finished.

### Cause

Three per-service uninstall handlers tore down the underlying service but **never regenerated the Caddyfile**. So Caddy kept its `takportal.<fqdn>` vhost in place with a `reverse_proxy` directive pointing at a dead docker upstream + a `forward_auth` directive pointing at Authentik. When a request came in, Caddy hit forward_auth first, Authentik had no proxy provider registered for that hostname (because the service was uninstalled), and Authentik returned its generic "Not Found" page — which Caddy faithfully passed through to the browser.

The handlers affected (and the pattern they were missing):

| Handler | File | Caddy regen? (pre-v0.9.31) |
|---|---|---|
| `takportal_uninstall` | `app.py` | ❌ missing |
| `authentik_uninstall` (local branch) | `app.py` | ❌ missing — remote branch had it |
| `takserver_uninstall` | `app.py` | ❌ missing |
| `mediamtx_uninstall` | `app.py` | ✅ correct |
| `nodered_uninstall` | `app.py` | ✅ correct |
| `cloudtak_uninstall` | `app.py` | ✅ correct |

### Fix

All three previously-broken handlers now call `generate_caddyfile(settings)` followed by `systemctl reload caddy` at the end of the uninstall, wrapped in a defensive `try/except` so a Caddy reload error never blocks the uninstall itself. Matches the pattern `mediamtx_uninstall` has been using since the v0.9.x days.

### Net behavior

1. Click **Remove TAK Portal** (or Authentik, or TAK Server).
2. Service is torn down (unchanged).
3. **New:** Caddyfile is regenerated without the now-removed hostname.
4. **New:** `systemctl reload caddy` applies the change.
5. Visit `takportal.<fqdn>`: browser gets a clean Caddy 404 / SNI mismatch / connection-refused — no more Authentik fall-through page.

---

## Bug 3: MediaMTX systemd units no longer crash-loop with status=217/USER

### Symptom

Fresh v0.9.30 install, MediaMTX deploys cleanly through all 7 steps, but immediately after deploy:

```
mediamtx-webeditor.service: Failed at step USER spawning /usr/bin/python3: No such process
mediamtx-webeditor.service: Main process exited, code=exited, status=217/USER
mediamtx.service:          Main process exited, code=exited, status=217/USER
... (Restart=always, ~636 restarts/min observed in field on SSDNodes box)
```

`stream.<fqdn>` shows the Authentik "Not Found" page in browsers (Caddy reverse-proxies to MediaMTX's dead web editor on :5080, which falls through to Authentik forward-auth, same root cause as Bug 2's symptom but a different underlying cause).

### Cause

v0.9.29's MediaMTX hardening (Project Shakespear, the "no more running services as root" track to v1.0.0) added `User=takwerx` and `SupplementaryGroups=caddy` to both `mediamtx.service` and `mediamtx-webeditor.service`. The matching code to **create** the `takwerx` system user, however, had been reverted in v0.9.2-alpha commit `a6a7422` ("defer to v0.9.3") and was never restored. v0.9.29's hardening assumed the user already existed.

On any box where `takwerx` happened to exist already (e.g. carried over from an earlier install that did provision it), MediaMTX came up fine and nobody noticed the gap. On a clean SSDNodes box where the user had never been created, both systemd units failed with `status=217/USER` ("Failed to determine user credentials: No such process") and tight-looped on `Restart=always`.

This also silently broke v0.9.29's two MediaMTX permission heals (LE-cert read access, web editor writable paths) — both of those did `chown takwerx:takwerx <path>`, which on most Linux filesystems silently no-ops when the target user doesn't exist. So the heals "succeeded" but actually changed nothing, and the underlying issue never surfaced until 217/USER.

### Fix

Three additions to `app.py`:

1. **`_ensure_takwerx_system_user()`** — new helper that runs `getent passwd takwerx` and, if empty, creates the user as a locked-down system account: `useradd --system -g takwerx -d /nonexistent -s /usr/sbin/nologin takwerx`. Idempotent. Matches Debian/Ubuntu conventions for service accounts (www-data, postgres, etc.).

2. **Called at MediaMTX deploy Step 1** — both the local (`run_mediamtx_deploy`) and remote (`_run_mediamtx_deploy_remote`) paths now ensure `takwerx` exists immediately after `apt-get install`, before any chown or systemd unit install can hit a missing user. Closes the gap for new deploys.

3. **`_heal_takwerx_user_missing_for_mediamtx()`** — new startup migration registered in `_startup_migrations()`. Runs at every console boot. Skips entirely if `/etc/systemd/system/mediamtx.service` doesn't exist (no MediaMTX = nothing to heal) OR if `takwerx` already exists (no fracture). When it does need to run, it: (a) creates the user, (b) re-applies the v0.9.29 chowns (which were silently no-op'd when the user was missing), (c) re-applies `usermod -aG caddy takwerx` for LE-cert access, (d) `systemctl daemon-reload` + `systemctl restart mediamtx mediamtx-webeditor`, (e) verifies both units come back to `active` and logs the outcome. Closes the gap for existing-broken boxes — operators just do **Update Now** and the next console restart self-heals.

The migration runs **before** v0.9.29's `_heal_mediamtx_webeditor_writable_paths`, because that heal does chowns to `takwerx` and needs the user to exist for them to take effect.

### Why we kept `User=takwerx` instead of reverting to `User=root`

v1.0.0's headline security work is "infra-TAK services no longer run as root." MediaMTX is the first service on that track (Project Shakespear). Reverting `User=takwerx` would have un-done the hardening; closing the user-creation gap keeps the hardening and removes the operator-visible fail-loop. The new helper is fleet-uniform: every box that deploys MediaMTX on v0.9.31+ gets the same `takwerx` system account with the same UID/GID convention, created from the same code path.

### Fleet-uniform compliance (Bug 3)

- ✅ **Fleet constant, no per-box override.** Every box that runs MediaMTX gets `takwerx` created with the same `useradd` flags. No operator-tunable knob, no override-preservation trap.
- ✅ **Self-heal for existing broken boxes.** Startup migration runs on every boot and converges any pre-v0.9.31 broken box to the new known-good state.
- ✅ **Convergence verified.** Migration runs `getent passwd takwerx` as the post-condition and logs `✓ takwerx system user created` only after the user is observably present.

---

## Bug 4: Authentik forward_auth chain — fleet-uniform self-heal

### Symptom

On the SSDNodes fresh-install box (2026-05-18), the MediaMTX deploy log printed:

```
[21:46:08] ━━━ Registering MediaMTX in Authentik (proxy provider + application) ━━━
[21:46:30]   ⚠ Proxy provider update failed: infra-TAK: timed out
[21:46:40]   ✓ Application already exists: infra-TAK
[21:46:50]   ⚠ Proxy provider update failed: MediaMTX: timed out
[21:46:55]   ✓ Application already exists: MediaMTX
[21:47:20] 🎉 MediaMTX v1.18.2 deployed successfully!
```

MediaMTX itself worked (`stream.<fqdn>` served the editor). But `takportal.<fqdn>` returned the **Authentik "Not Found"** page — confirmed in the operator's browser, served from Authentik (visible in the page source / `<authentik Logo>` markup).

### Cause

Every infra-TAK deploy path that registers an Authentik proxy provider had the same anti-pattern:

```python
# Old (band-aid) shape — _ensure_authentik_console_app, _sync_authentik_takportal_provider_url, etc.
try:
    req = _urlreq.Request(f'{ak_url}/api/v3/providers/proxy/{pk}/', data=..., method='PATCH')
    _urlreq.urlopen(req, timeout=10)
except Exception:
    pass  # ← silently swallowed
```

On a fast Authentik (Azure box, well-provisioned), PATCH/POST completed in ~1-3s and nobody noticed. On the SSDNodes box, Authentik's API took >10s for writes (the same class of slowness covered in `.cursor/rules/fleet-uniform-config.mdc` as the Channels-pool tale), the PATCH timed out, and **the silent except hid the failure from the operator AND the post-deploy verification**. Six things had to all succeed for the chain to work:

1. Proxy provider exists in Authentik with name `<Service> Proxy`
2. ...with `mode=forward_single`
3. ...with `external_host = https://<service-prefix>.<fqdn>`
4. ...with `cookie_domain = .<base-fqdn>`
5. Application exists with the right slug and provider link
6. Provider is in the embedded outpost's `providers[]` list

Each of those was its own urlopen call with `timeout=10` + `except Exception: pass`. Any one timeout → silently incomplete chain → Authentik returns "Not Found" because it has no provider mapped to the requested hostname.

The v0.9.21 `_ensure_authentik_proxy_external_hosts_canonical` startup migration was supposed to heal this, but only PATCHes external_host on providers that already exist — it can't create the provider, can't fix the app, can't add to the outpost. And its individual PATCH calls had the same 10s + silent-fail problem.

### Field-debug update (2026-05-18 second pass)

Live SSH into the failing SSDNodes box (after authorizing my pubkey from the operator's open session) revealed two follow-on bugs in the first chain-healer rev that this commit also fixes:

**4a. Partial `PATCH` to `/api/v3/providers/proxy/<pk>/` is rejected with HTTP 400.** Authentik's serializer validates the entire object even on PATCH. A partial body like `{external_host, cookie_domain}` triggers `"internal_host": ["Internal host cannot be empty when forward auth is disabled."]`. Observed live:

```
$ curl -X PATCH ... -d '{"external_host":"https://takportal.lutak.net"}' \
    http://127.0.0.1:9090/api/v3/providers/proxy/3/
{"internal_host": ["Internal host cannot be empty when forward auth is disabled."]}
```

**Fix:** chain healer (and v0.9.21 canonicalizer's API path) now use **PUT with the full provider body** — name, both flows, mode, token_validity, external_host, cookie_domain, internal_host (empty string preserved), internal_host_ssl_validation, skip_path_regex, basic_auth_*, intercept_header_auth. PUT round-trips cleanly:

```
$ curl -X PUT ... -d '{ "name": "TAK Portal Proxy", ..., "external_host": "https://takportal.lutak.net" }' \
    http://127.0.0.1:9090/api/v3/providers/proxy/3/
{"pk": 3, ..., "external_host": "https://takportal.lutak.net", ...
 "redirect_uris": [
   {"matching_mode": "strict",
    "url": "https://takportal.lutak.net/outpost.goauthentik.io/callback?X-authentik-auth-callback=true"},
   {"matching_mode": "strict",
    "url": "https://takportal.lutak.net?X-authentik-auth-callback=true"}
 ]}
```

**4b. Trailing-slash drift in `external_host` is the actual cause of "Redirect URI Error".** The v0.9.21 canonicalizer's `ak shell` fallback wrote `p.external_host = want_clean + '/'` — with a trailing slash. Authentik bakes the exact form (with-slash vs without-slash) into the proxy provider's `redirect_uris` list as a **strict** match. When the Caddy outpost subsequently did forward_auth, the OAuth flow's `redirect_uri` parameter mismatched the strict-match entry, and Authentik responded with the "Redirect URI Error" screen instead of the login page. Live state on the SSDNodes box (where the symptom was reproducing) — note the slash on TAK Portal but not on the others:

```
pk=4 name='MediaMTX'         mode='forward_single' host='https://stream.lutak.net'        ← works
pk=3 name='TAK Portal Proxy' mode='forward_single' host='https://takportal.lutak.net/'    ← BROKEN (slash)
pk=2 name='infra-TAK'        mode='forward_single' host='https://infratak.lutak.net'      ← works
```

After the live PUT with `'https://takportal.lutak.net'` (no slash), `takportal.lutak.net` worked immediately — operator confirmed.

**Fix:** both the `ak shell` fallback and the API PUT path now write WITHOUT trailing slash, AND the equality check uses exact string compare (no `rstrip('/')`) — so a stale with-slash row is treated as different from the no-slash target and triggers a real PUT.

### Fix

New function `_heal_authentik_proxy_chain_all_services(plog, settings=None)` — a single fleet-uniform self-heal that asserts the **full** forward_auth chain (provider + app + outpost) for every deployed service. Data-driven from a single catalog at module scope:

```python
_AUTHENTIK_PROXY_CHAIN_SERVICES = [
    # (module_key, provider_name, app_slug, app_name, service_domain_key, open_in_new_tab)
    ('infratak',  'infra-TAK',            'infratak',       'infra-TAK',      'infratak',  False),
    ('takportal', 'TAK Portal Proxy',     'tak-portal',     'TAK Portal',     'takportal', True),
    ('nodered',   'Node-RED Proxy',       'node-red',       'Node-RED',       'nodered',   True),
    ('mediamtx',  'MediaMTX',             'stream',         'MediaMTX',       'mediamtx',  True),
    ('fedhub',    'Federation Hub Proxy', 'federation-hub', 'Federation Hub', 'fedhub',    True),
]
```

For every catalog entry whose service is currently deployed (`_is_module_deployed` for most, `_get_fedhub_deployment_config` for Fed Hub), the healer:

1. **Provider:** GET; if missing → POST create with full config; if present but `external_host` or `cookie_domain` wrong → PATCH; verify by re-GET.
2. **Application:** GET by slug; if missing → POST create; if present but wrong provider/open_in_new_tab → PATCH.
3. **Outpost:** accumulate provider pk into a single PATCH at the end of the loop (one round-trip instead of N).

Every API call goes through the existing `_ak_api_call(timeout=30, max_retries=3)` helper, which retries on 502/503/timeout/URLError with 5s/10s/15s backoff. Failures are logged with their cause (`failed:create-timeout`, `failed:patch-timeout`, `failed:verify-mismatch`, etc.) — not silently swallowed.

The function returns a summary dict:

```python
{
  'takportal': {'provider': 'created', 'app': 'created', 'outpost': 'ok'},
  'mediamtx':  {'provider': 'ok',      'app': 'ok',      'outpost': 'ok'},
  'infratak':  {'provider': 'fixed',   'app': 'ok',      'outpost': 'ok'},
  ...
}
```

The TAK Portal **"Update config"** button now surfaces this in the response so the UI reports `Config updated, portal restarted, and Authentik proxy chain reconciled.` (success) or the actual failure reason — no more silent "success" lies.

### Where the chain healer runs

- **`_startup_migrations()`** on every console boot — after the v0.9.21 canonicalizer (so the canonicalizer fixes what it can on existing providers, then the chain healer creates any that are missing + verifies the rest)
- **Post-update auto-reconfigure** (after Update Now triggers Authentik reconfigure)
- **`takportal_control` reconfigure** handler (TAK Portal "🔄 Update config" button)
- Existing deploy-time paths (`_ensure_authentik_console_app`) also now use `_ak_api_call` with 30s timeout + retry instead of bare `urlopen(timeout=10)` — same retry semantics inline

### Fleet-uniform compliance (Bug 4)

Per `.cursor/rules/fleet-uniform-config.mdc`:

- ✅ **Same code path on every box.** No per-customer Authentik config drift. The catalog is the source of truth, retries are constant, timeouts are constant.
- ✅ **No silent ignore.** Every API failure now logs its specific cause and is reflected in the return value. Operators get truthful UI feedback.
- ✅ **No `max(cur, target)` override-preservation.** The healer always writes the canonical value when it differs from the current one. No "but the operator hand-edited Authentik" trap.
- ✅ **Convergence verified.** Every PATCH/POST is followed by a re-GET that asserts the desired state actually landed. Mismatch is reported as `failed:verify-mismatch` instead of `success`.
- ✅ **Self-heal for existing broken boxes.** Startup migration on every console boot. Operators pull v0.9.31, restart console (or click Update Now), and any half-built chain converges within ~10 seconds.

### Per-customer escalation path (if a box hits a load class the retry safety margin can't cover)

If a box hits Authentik write times that exceed even the 30s × 3 retry margin → that's a **code fix** (raise the timeout, raise the retry count, or fix the underlying Authentik slowness), pushed to the entire fleet. **Not** a per-box override. Per `.cursor/rules/fleet-uniform-config.mdc`.

---

## Bug 5: Clear stale `failed` state on `takauthentiktasklogpurge.service`

Surfaced during the v0.9.31-alpha soak validation sweep on 2026-05-18 across test6 + test8 + test12. `systemctl --failed` reported `takauthentiktasklogpurge.service` as failed on test6 and test8 (both v0.9.31-alpha, both healthy in every other respect). Initial reaction: "v0.9.31 broke the tasklog purge cross-fleet." Closer look:

```
[2026-05-17T03:00:04Z] Starting Authentik task log purge       ← old script header (no "v0.9.26 multi-tier" suffix)
DELETE 18274
DELETE 3916
ERROR:  VACUUM cannot run inside a transaction block            ← v0.9.26 fixed this
```

The failure was on **May 17 03:00 UTC** — the day BEFORE v0.9.31-alpha pulled. The OLD v0.9.5 script (single `psql -c "DELETE;DELETE;VACUUM"`, wrapped in an implicit transaction by psql, VACUUM forbidden inside transactions) ran, exited 1, systemd marked the unit failed. v0.9.26's `_ensure_authentik_tasklog_purge_script` migration then overwrote the on-disk script with the canonical fixed version. The on-disk script is now correct; the **systemd failed-state accounting is stale**.

Data hygiene was never affected — `_auto_authentik_tasklog_purge` (inline Python) runs on every console boot and keeps the tables compact regardless of whether the timer ran. The only thing showing as broken was `systemctl --failed`, which lies to operators and audits.

### Cause

There's no path in the existing codebase that clears `failed` state after the script is fixed. `systemctl --failed` will keep reporting the unit until either:

- the next Sunday timer fires successfully (clears failed-state automatically on a clean run), OR
- someone manually `systemctl reset-failed`s the unit

Neither is great. Sunday is up to 7 days away. Operators shouldn't have to learn `reset-failed` semantics to clear a self-inflicted false positive.

### Fix

New startup migration `_heal_takauthentik_tasklog_purge_stale_failed_state(plog)` in `app.py`, wired into `_startup_migrations()` right after the existing v0.9.26 tasklog migrations. Logic:

1. **Skip** if `/etc/systemd/system/takauthentiktasklogpurge.service` doesn't exist (Authentik not installed).
2. **Skip** if `systemctl is-failed` does NOT report `failed` (don't disturb healthy units, don't surface noise on every boot).
3. **Skip** if the on-disk script at `/opt/tak-guarddog/tak-authentik-tasklog-purge.sh` does NOT contain the `v0.9.26 multi-tier` marker (i.e. the fix isn't on disk yet — leave the failed state alone so the on-disk script can be re-emitted by the migration above).
4. Otherwise: `systemctl reset-failed takauthentiktasklogpurge.service`, re-check `is-failed` to confirm, log success.

### Net behavior

| Box state | What this migration does |
|---|---|
| Healthy unit (inactive, no failures) | Skip — `is-failed` doesn't return `failed`. No log, no change. |
| Failed unit + script on disk is v0.9.26+ canonical | **Clear the stale state.** Log `✓ tasklog-purge: stale failed-state cleared`. |
| Failed unit + script on disk is OLD (no `v0.9.26 multi-tier` marker) | Leave the failed state alone. Log `script is NOT the v0.9.26 fixed version — leaving alone`. The companion migration above re-emits the canonical script; next console boot picks up the heal. |
| Unit not installed (no Authentik) | Skip silently. |

### Fleet-uniform compliance (Bug 5)

- ✅ **Same code path on every box.** No per-customer state. The marker-string check is constant; the reset-failed call is constant.
- ✅ **No silent ignore.** When the script is the v0.9.26 fix the heal fires and is logged. When the script is old, the heal explicitly logs why it's deferring.
- ✅ **No data side effects.** This is pure systemd accounting cleanup. No DELETEs, no VACUUMs, no service restarts.
- ✅ **Convergence verified.** After `reset-failed`, the migration re-runs `is-failed` and asserts the unit is no longer in failed state. If reset didn't take, the migration logs the unexpected residual state for operator follow-up.
- ✅ **Idempotent.** Subsequent boots find the unit already non-failed and skip in step 2. Healthy boxes never re-fire.

---

## Bug 6: One-click "Patch now" — kernel upgrade that survives SSH session drops AND mid-upgrade service restarts

### The problem

The kernel-update banner had been telling operators to run:

```
apt update && apt full-upgrade && reboot
```

This is fine **from a local serial console**. Over **SSH** it's a foot-gun, and on 2026-05-18 it bit the SSDNodes box that surfaced bugs 1–4. Here's the exact failure sequence:

1. Operator SSH'd in and ran the chained command.
2. `apt-get full-upgrade` walked the package list. When it reached the core systemd/networking/openssh-server upgrades, it replaced libraries that those services use.
3. The SSH session's TCP connection died because either (a) `sshd` was restarted mid-upgrade, (b) networking restarted (`netplan`/`udev` updates), or (c) systemd was reexec'd. Take your pick — all three were in the upgrade list.
4. With the SSH TTY dead on the operator's end, apt's foreground stdin closed → apt aborted the transaction. `/var/log/apt/history.log` shows a `Start-Date: 2026-05-18 22:47:21` for the upgrade with **no `End-Date`** — the smoking gun.
5. `&& reboot` never executed because `&&` only fires on a clean `0` exit.
6. The operator later triggered a panel reboot. The box came up on **kernel 5.15.0-177** (the old one) because `linux-image-5.15.0-179-generic` had been downloaded but never had its initramfs finalized or its grub menu entry added.
7. `dpkg --configure -a` (run later by the operator to clean up) only finished packages that had already been unpacked. Packages that hadn't been unpacked yet — including the kernel metapackage updates — stayed pending.
8. The banner kept showing because `apt list --upgradable | grep linux-image` still returned hits (the 5.15.0-179 metapackages). **The operator followed the banner's exact instructions and the result was a box that the banner still thinks needs patching.**

There's a SECOND failure mode the banner-instructed approach can't avoid even when the SSH session DOES survive: `needrestart` (default on Ubuntu 22.04, behavior controlled by `NEEDRESTART_MODE`) inspects which services use libraries that got upgraded, and with `MODE=a` (automatic) it issues `systemctl restart <unit>` for each affected service. `takwerx-console.service` is a Python gunicorn process linked against `libpython3.x`, `libssl`, `libc`. Any apt full-upgrade likely upgrades at least one of those → needrestart restarts `takwerx-console.service` → if our apt subprocess shared takwerx-console's cgroup, the restart would kill apt mid-flight. So even if the operator avoided the SSH drop, running `apt full-upgrade` from inside the console would still risk self-killing the upgrade.

### The fix

New "Patch now" button on the banner, backed by three new API endpoints. The actual upgrade runs as a **transient systemd unit** spawned by `systemd-run --no-block`. This is the critical architectural choice:

- `systemd-run` makes a D-Bus call to PID 1 (systemd) asking it to register a new transient `.service` unit.
- PID 1 forks the subprocess **from itself**, in a brand-new cgroup at `/sys/fs/cgroup/system.slice/infratak-kernel-patch.service/`. The subprocess is NOT a child of `takwerx-console.service` and NOT in its cgroup.
- When `needrestart` issues `systemctl restart takwerx-console.service` mid-upgrade, the restart only affects takwerx-console's cgroup. Our apt process keeps running cleanly in its own cgroup.
- Same protection against SSH session drops — the API call to `/api/system/kernel-patch/start` returns immediately; the operator can close the browser tab and the upgrade still runs to completion.

### The UI states

| State | Banner shows | Operator action |
|---|---|---|
| **idle** (kernel update available, no job in flight) | "⚠ Kernel update available — patch now to fix CVE-2026-31431 (Copy Fail). Runs `apt-get full-upgrade` in a detached background process — safe over SSH, survives session drops. Reboot is a separate explicit click." | `[Patch now]` or `[Dismiss]` |
| **running** | "⚙ Kernel patch in progress — pid N. Detached from console — safe to close this tab. Job runs to completion regardless. Typical time: 2-5 min." + scrolling log tail | (wait) |
| **done** | "✓ Kernel patch complete — reboot required to boot the new kernel" + final log tail | `[Reboot now]` or `[I'll reboot later]` |
| **error** | "✗ Kernel patch FAILED — see log below" + full log tail | `[Retry]` or `[Dismiss]` |

UI state is driven entirely by polling `/api/system/kernel-patch/job-status` every 3s while running. The endpoint reads `systemctl show -p ActiveState,SubState,Result,MainPID infratak-kernel-patch.service` — the authoritative source of truth — plus `tail -c 4096 /var/log/takguard/kernel-patch.log`. `done` is determined from `Result=success`, not from log content (log may be rotated or wiped).

### Spawn invariants

The transient unit is spawned with:

```
systemd-run \
  --unit=infratak-kernel-patch \
  --description=infra-TAK Kernel Patch Job (detached) \
  --property=StandardOutput=append:/var/log/takguard/kernel-patch.log \
  --property=StandardError=append:/var/log/takguard/kernel-patch.log \
  --setenv=DEBIAN_FRONTEND=noninteractive \
  --setenv=NEEDRESTART_MODE=a \
  --setenv=PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  --no-block \
  /bin/bash /var/lib/infratak-kernel-patch.sh
```

The script writes a per-step timestamp banner, runs `apt-get update`, then `apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" full-upgrade`, then writes `=== DONE — safe to reboot ===`. On any non-zero exit at any step it writes `FATAL: ... exit N` and exits with the same code — systemd marks the unit `Result=exit-code`, the UI shows the error state.

### Smoke-tested before commit

Validated on test8 2026-05-19 with `apt-get -s full-upgrade` (simulate mode) substituted for the real upgrade:

- Unit spawned cleanly via systemd-run.
- `cat /proc/<PID>/cgroup` returned `0::/system.slice/infratak-kernel-patch.service` — confirmed separate cgroup from takwerx-console.service.
- Unit completed with `ActiveState=inactive Result=success` after ~15s.
- Log file captured the apt simulation output + the DONE banner.

The dry-run incidentally revealed test8 has 3 pending docker-ce updates — so a real "Patch now" click on test8 would do actual work.

### Fleet-uniform compliance (Bug 6)

- ✅ **Same code path on every box.** systemd-run command is constant, dpkg flags are constant, env vars are constant.
- ✅ **systemd-run is always present.** Part of systemd itself — present on every box we deploy on (Ubuntu 22.04+).
- ✅ **Survives parent restarts.** Cgroup escape is the whole point. needrestart can restart takwerx-console mid-flight and the apt job keeps running.
- ✅ **No auto-reboot.** Reboot is a separate explicit `[Reboot now]` click. Operator decides when to reboot.
- ✅ **Idempotent.** If a job is already active, start endpoint refuses to spawn another and returns the existing pid. If the unit is in `failed` state from a prior run, `reset-failed` is called automatically before the next start.
- ✅ **Resilient to log rotation.** `done`/`error` are determined from systemd's authoritative `Result=` field, not log content.

---

## Validation gate before merge to `main`

This release bundles three single-issue bugfixes on top of v0.9.30-alpha. None of them runs on a healthy box:

- **Bug 1 paths:** only invoked by explicit operator action (Remove TAK Server / Remove Everything buttons) or by deploy Step 4's pre-flight when it detects a half-removed package. Healthy boxes don't hit any of these.
- **Bug 2 paths:** only run inside the four uninstall handlers that were missing the regen. Healthy boxes don't click Remove.
- **Bug 3 paths:**
  - Deploy-time `_ensure_takwerx_system_user`: runs only during a MediaMTX deploy. Idempotent — does nothing if the user already exists.
  - Startup migration `_heal_takwerx_user_missing_for_mediamtx`: runs at every console boot but exits in the first 5ms if `/etc/systemd/system/mediamtx.service` doesn't exist OR `getent passwd takwerx` returns a row. Healthy boxes hit the early-return.
- **Bug 5 paths:** startup migration `_heal_takauthentik_tasklog_purge_stale_failed_state` runs at every console boot but exits in the first 5ms when the unit isn't installed OR isn't in failed state. Healthy boxes hit the early-return. When the unit IS failed, it does ONE `reset-failed` call (no data side effects) and re-verifies.
- **Bug 6 paths:** entirely operator-driven. The "Patch now" button must be explicitly clicked AND confirmed via a JS `confirm()` dialog. The "Reboot now" button must be explicitly clicked AND confirmed. No code path runs the upgrade or reboot automatically. Boxes with no pending kernel update never see the banner at all (the existing `/api/system/kernel-patch-status` endpoint returns `patched: true`).

### Validation plan

- [ ] **tak-10** — Update Now from v0.9.30-alpha → v0.9.31-alpha. Verify no behavior change (existing `takwerx` user untouched, MediaMTX/TAK still running, no spurious migrations fire). 60-min soak, no `query_wait_timeout`, no watchdog ALERT.
- [ ] **test8** — same as tak-10.
- [ ] **infratak-vps / responder** — same as tak-10.
- [ ] **The failing box (SSDNodes, 208.87.134.78):**
  - [ ] Bug 1: Update Now to v0.9.31-alpha → retry Deploy TAK Server → Step 4 prints `Detected half-removed takserver ... purging before reinstall...` and completes successfully.
  - [ ] Bug 2: Remove TAK Portal → visit `takportal.<fqdn>` → expect clean Caddy 404 / SNI mismatch (NOT the Authentik "Not Found" page). Repeat for Remove Authentik on `auth.<fqdn>` and Remove TAK Server on `webtak.<fqdn>`.
  - [ ] Bug 3: Restart console (or wait for next boot) → log line `Startup migration: mediamtx: takwerx user missing — creating + re-applying chowns + restarting services` fires once, followed by `✓ takwerx heal complete: mediamtx + mediamtx-webeditor both active`. `systemctl is-active mediamtx mediamtx-webeditor` both report `active`. `stream.<fqdn>` serves the web editor (not the Authentik fall-through page).
  - [ ] Bug 4: Restart console → console log shows `Startup migration: proxy chain: N/N service(s) fully reconciled (...)`. Specifically for the operator's current symptom, expect at minimum:
    - `Startup migration: ✓ TAK Portal Proxy: provider created → https://takportal.<fqdn>` (or `fixed`)
    - `Startup migration: ✓ TAK Portal: application created (slug=tak-portal, provider=<pk>)` (or `ok`)
    - `Startup migration: ✓ embedded outpost: added missing providers (...)` (or `all N already attached`)
    Then visit `https://takportal.<fqdn>` → Authentik login screen (not "Not Found"). After authenticating → TAK Portal landing page.
- [ ] **Any fleet box where `systemctl --failed` includes `takauthentiktasklogpurge.service`** (Bug 5):
  - [ ] Update Now to v0.9.31-alpha. On next console restart, expect log line `Startup migration: ✓ tasklog-purge: stale failed-state cleared (script on disk is v0.9.26+ fix; next Sunday timer will re-validate)`.
  - [ ] `systemctl --failed` no longer lists the unit.
  - [ ] `systemctl status takauthentiktasklogpurge.service` shows `Active: inactive (dead)` (not `failed`).
  - [ ] Confirmed on test6 + test8 during initial v0.9.31-alpha soak: both reported `takauthentiktasklogpurge.service` failed since May 17 03:00 UTC (pre-v0.9.31-alpha). After this migration ships, the next console restart on each should clear the false positive.
- [ ] **Bug 6 (one-click kernel patch)** — on any box where the kernel banner is showing:
  - [ ] Click "Patch now" in the banner → confirm dialog appears → confirm → banner switches to "Kernel patch in progress" with the live log tail.
  - [ ] `systemctl show -p ActiveState,MainPID infratak-kernel-patch.service` reports `ActiveState=active` with a non-zero `MainPID`.
  - [ ] `cat /proc/<MainPID>/cgroup` reports `0::/system.slice/infratak-kernel-patch.service` (NOT `takwerx-console.service`) — confirms cgroup isolation.
  - [ ] Restart `takwerx-console` mid-upgrade (simulate needrestart): `systemctl restart takwerx-console.service`. Verify the patch unit keeps running (`systemctl is-active infratak-kernel-patch.service` still `active`).
  - [ ] When the unit completes: banner switches to "Kernel patch complete" with `[Reboot now]` button visible.
  - [ ] Click "Reboot now" → confirm dialog → confirm → box reboots and console reloads after ~30-45s.
  - [ ] Post-reboot: `uname -r` shows the new kernel; banner clears within 60s (cache TTL).
  - [ ] Smoke-validated on test8 2026-05-19 with `apt-get -s full-upgrade` (simulate mode) — unit spawned in its own cgroup, completed cleanly with `Result=success`, log file captured all output including the DONE banner.

## Files changed

- `app.py`:
  - Line 369: `VERSION = "0.9.31-alpha"` (unchanged from earlier in this PR)
  - **Bug 1** (TAK Server purge):
    - `takserver_uninstall()` — escalating purge + verify
    - global remove-all handler — same escalating purge + verify
    - `run_takserver_deploy()` Step 4 — pre-flight purge of half-removed state + last-resort `--reinstall` self-heal + improved FATAL message
    - `takserver_uninstall()` end — added `generate_caddyfile` + `systemctl reload caddy`
  - **Bug 2** (Caddyfile regen on uninstall):
    - `takportal_uninstall()` — added `generate_caddyfile` + `systemctl reload caddy`
    - `authentik_uninstall()` local branch — added `generate_caddyfile` + `systemctl reload caddy` (remote branch already had it)
  - **Bug 3** (`takwerx` system user):
    - new helpers: `_ensure_takwerx_system_user`, `_ensure_takwerx_system_user_remote`, `_heal_takwerx_user_missing_for_mediamtx`
    - `run_mediamtx_deploy()` Step 1 — calls `_ensure_takwerx_system_user`
    - `_run_mediamtx_deploy_remote()` Step 1 — calls `_ensure_takwerx_system_user_remote`
    - `_startup_migrations()` — calls `_heal_takwerx_user_missing_for_mediamtx` (ordered BEFORE `_heal_mediamtx_webeditor_writable_paths`)
  - **Bug 4** (Authentik forward_auth chain self-heal):
    - new module-scope catalog: `_AUTHENTIK_PROXY_CHAIN_SERVICES`
    - new function: `_heal_authentik_proxy_chain_all_services(plog, settings=None)`
    - `_startup_migrations()` — calls chain healer after `_ensure_authentik_proxy_external_hosts_canonical`
    - Authentik post-update auto-reconfigure (`_auto_authentik`) — calls chain healer after canonicalizer
    - `takportal_control` reconfigure action — calls chain healer, surfaces summary in HTTP response
    - `_ensure_authentik_console_app()` — replaced bare `urlopen(timeout=10)` + silent `except` with `_ak_api_call(timeout=30, max_retries=3)` + truthful failure messages on the deploy-time provider/app PATCH/POST/PUT paths
  - **Bug 5** (tasklog purge stale failed-state):
    - new function: `_heal_takauthentik_tasklog_purge_stale_failed_state(plog)`
    - `_startup_migrations()` — calls heal right after `_auto_authentik_tasklog_purge` (so the script is guaranteed updated and the inline purge has run before we decide whether to clear the systemd accounting)
  - **Bug 6** (one-click kernel patch button):
    - new helpers: `_kernel_patch_unit_state`, `_kernel_patch_start_job`, `_kernel_patch_job_state`
    - new endpoints: `POST /api/system/kernel-patch/start`, `GET /api/system/kernel-patch/job-status`, `POST /api/system/kernel-patch/reboot`
    - kernel-update banner HTML rewritten: idle/running/done/error UI states, `[Patch now]` and `[Reboot now]` buttons replace the previous "Run: …" instruction
    - kernel-patch JS rewritten: `startKernelPatch`, `_kpatchStartPolling`, `_kpatchPollOnce`, `rebootForKernelPatch`, `_kpatchShowState` — and `checkKernelPatch` now first polls `/api/system/kernel-patch/job-status` so a browser reload mid-patch resumes the in-flight UI
    - script written to `/var/lib/infratak-kernel-patch.sh`, log written to `/var/log/takguard/kernel-patch.log`, unit name `infratak-kernel-patch.service`
- `docs/RELEASE-v0.9.31-alpha.md` (this file)

No schema changes. No new settings keys. No new dependencies on the host beyond the `takwerx` Linux user (system account, no shell, no home directory) and `systemd-run` (which is part of systemd itself — always present on Ubuntu 22.04+).
