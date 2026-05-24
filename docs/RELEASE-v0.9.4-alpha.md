# Release Notes — v0.9.4-alpha

## What ships

Hotfix release completing the v0.9.3 test checklist, fixing bugs found during post-release testing, and adding one new feature: local Federation Hub deployment. All changes apply automatically on next "Update Now."

---

### Bug Fix 1 — CloudTAK full connection reset

**Problem:** After "Reset config" on the CloudTAK page, the CloudTAK API still attempted a TAK Server connection because `url`, `api`, and `webtak` were not cleared — only `auth` was. The Node.js event loop blocked on the failed SSL connection, making every HTTP request hang indefinitely (`map.<fqdn>` → `about:blank`).

**Fix:** Reset SQL now clears all four fields: `UPDATE server SET auth = '{}'::jsonb, url = '', api = '', webtak = '' WHERE id = 1;`

---

### Bug Fix 2 — CloudTAK nginx worker crash on every container start

**Problem:** The CloudTAK container image generates `user nginx;` in `nginx.conf`. Workers immediately failed to write to `/dev/stdout`/`/dev/stderr` after dropping privileges and died with exit code 2. The nginx master stayed alive but served nothing — `about:blank` in browser.

**Fix:** New `_cloudtak_fix_nginx_user()` helper patches `user nginx;` → `user root;` in the generated nginx.conf and reloads workers. Called after every container start/restart in `cloudtak_reset_server_config`, `run_cloudtak_redeploy`, `run_cloudtak_update`, and `cloudtak_control`.

---

### Bug Fix 3 — CPU/RAM refresh button JS SyntaxError

**Problem:** The "What's using CPU/RAM?" Refresh button rendered broken JavaScript. `refreshBtn` was built with `\'` escape sequences inside Python's triple-quoted template. Python resolves `\'` → `'` before rendering, turning `'...(\''+hostId+'\'')...'` into adjacent string literals with no `+` operator — `SyntaxError: Unexpected string`. Both `toggleResourceBreakdown` and `refreshResourceBreakdown` were undefined.

**Fix:** Switched `refreshBtn` to a JavaScript template literal (backtick string) so `${hostId}` handles variable substitution with no quote escaping needed.

---

### Bug Fix 4 — Authentik page JavaScript SyntaxError (JetBrains Mono)

**Problem:** Seven `innerHTML` assignments in the Authentik template contained `font-family:\'JetBrains Mono\'`. Python resolves `\'` → `'` in triple-quoted strings, breaking the JS string literal and killing the entire script block. The "Domain Migration Audit" card was present in the HTML but all JS functions were dead, including container log loading.

**Fix:** Changed all seven occurrences to `font-family:\"JetBrains Mono\"` so double-quotes survive Python rendering and stay valid inside single-quoted JS strings.

---

### Bug Fix 5 — Authentik "Run Audit" returned no result

**Problem:** After the SyntaxError fix, "Run Audit" would change to "Scanning..." then silently revert with no output. The `escapeHtml` helper was defined in a different template (`Customization`) and was missing from the Authentik template's script block. Both the success path and the catch block failed with `ReferenceError: escapeHtml is not defined`, preventing `res.style.display='block'` from ever being reached.

**Fix:** Added `escapeHtml` definition directly into the Authentik template's script block.

---

### Bug Fix 6 — Authentik domain audit false positive on empty cookie_domain

**Problem:** After deploying without ever changing the FQDN, the domain audit reported 1 stale reference for `Authentik brand` → `cookie_domain`. The audit logic flagged an empty `cookie_domain` as stale. An empty value is Authentik's default — it means "use the request domain for cookies" and is perfectly valid.

**Fix:** Audit now only flags `cookie_domain` if it is non-empty AND does not contain the current FQDN.

---

### Bug Fix 7 — Caddy domain update dialog JS SyntaxError

**Problem:** On the Caddy SSL page, clicking "Update & Reload" did nothing. The `confirm()` dialog string contained Python `\n` characters that rendered as literal newlines in JavaScript, breaking the single-quoted string literal. A second instance in the success `alert()` caused the same error after the first was fixed.

**Fix:** Escaped all `\n` to `\\n` in both the `confirm()` and `alert()` strings so JavaScript receives the intended newline escape sequences.

---

### Feature A — MediaMTX RTSP Fail2ban jail — watching panel + manual ban

Completes the MediaMTX jail UI to match the TAK Server jail pattern:

- **"Currently Watching" expand panel** — clickable caret reveals which IPs fail2ban is actively watching, with source port and a "Ban Now" button per entry
- **New API endpoints:** `GET /api/fail2ban/mediamtx/watching` and `POST /api/fail2ban/mediamtx/ban`
- **Auto-refresh** — watching panel refreshes on the same interval as the banned list
- The caret no longer disappears at zero (was hiding the panel entirely)

---

### Feature B — Kernel Patch Banner

Dismissible banner on the Console page when a kernel update is available. Shows the running kernel version, the upgradable version, and the one-liner to apply it. Dismiss persists in `localStorage`; banner re-appears on the next load that still shows a pending update.

- `GET /api/system/kernel-patch-status` — runs `uname -r` and `apt list --upgradable | grep linux-image`, cached 60s

---

### Feature C — Authentik Domain Migration Audit panel

New card on the Authentik page: **Domain Migration Audit**. Scans all seven known locations where the old domain is stored and reports any that still have the stale value.

- Locations checked: `~/authentik/.env` (`AUTHENTIK_HOST`, `AUTHENTIK_COOKIE_DOMAIN`), `docker-compose.yml` (`AUTHENTIK_HOST` in LDAP service), Authentik brand (`domain`, `cookie_domain`), `~/authentik/.env` `AUTHENTIK_COOKIE_DOMAIN`
- Shows each stale location with a "Fix All" button
- Pre-flight `confirm()` dialog lists what will change before applying

---

### Feature D — Caddy Custom Blocks hint repositioned

The "Custom vhosts / rules" hint box on the Caddy SSL page now appears **above** the Caddyfile content instead of below, so it's visible before editing begins.

---

### Feature E — Federation Hub local deployment

Federation Hub can now be deployed on the **same machine as the console** in addition to the existing remote SSH mode — the same pattern as TAK Server local vs split-server mode.

**How it works:**

- New **local / remote radio toggle** on the Federation Hub → Deployment Target section. SSH fields hide when local is selected.
- In local mode: `.deb` is `sudo cp`'d to `/tmp/` locally (no SCP); all install commands run via `subprocess` (no SSH). `_module_run()` already handled this dispatch — the FedHub functions were just blocked by hard `target_mode != 'remote'` guards.
- Firewall: local mode only opens 9101-9103 for federation peers. 8080/9100 are not exposed externally — Caddy proxies to `localhost:8080`.
- All management APIs updated for local: service control, status, cert expiry, webadmin cert download, Authentik OAuth enable, CA rotation.
- After deploy: `fedhub.<fqdn>` URL shown if FQDN is set; otherwise `localhost:9100`.

**To use:** Federation Hub page → Deployment Target → **This machine (local)** → Save target → upload `.deb` → Deploy.

---

### Operator notes

- No manual steps required — all changes apply on "Update Now."
- `cert-metadata.sh` ownership auto-fix: post-update hook now corrects `tak:tak` ownership on the cert script if it was changed by an unattended package upgrade.
- FedHub users on remote mode: no change — existing remote configs continue to work.
