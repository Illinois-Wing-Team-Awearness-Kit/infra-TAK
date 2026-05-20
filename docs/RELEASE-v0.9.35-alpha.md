# v0.9.35-alpha — Seven bugfixes: Node-RED backup restore CORS fix + CloudTAK update checkout + CloudTAK spinner + server-side update banner + atakatak nag removed + Help CLI recovery + TAK Server corrupted .deb self-healing

**Date:** 2026-05-20
**Type:** Bugfix release — drop-in update via Update Now.
**Status:** RELEASED to `main` 2026-05-20. Validated on test6 / test8 / test12 (SHA `2bb8137`, 3h13m soak).

---

## TL;DR

Seven independent fixes shipped together. No operator action required on any of them.

---

## Fix 1 — Node-RED Emergency Config Restore: "Failed to fetch" on session expiry

### Root cause (confirmed on tak-10 2026-05-20)

Authentik Proxy Provider for Node-RED has `token_validity: hours=1`. When the 1-hour proxy session cookie expires:

1. `fetch('/config/backups')` hits Caddy
2. Caddy's `forward_auth` asks Authentik → session expired → Authentik returns HTTP 302
3. `location:` redirects to `https://tak.<domain>/application/o/authorize/...` (cross-origin)
4. Browser follows the redirect cross-origin; Authentik's OAuth page has no `Access-Control-Allow-Origin` for `nodered.<domain>`
5. Browser CORS-blocks the response → `TypeError: Failed to fetch`

The Configurator page stays visible (HTML loaded before expiry) but the Refresh fetch fails silently with a cryptic error. Same failure path affected Restore.

### What changed

**`nodered/configurator.html` — `loadBackupList()` and `restoreBackup()`:**
- Bare `fetch('/config/backups')` with default `redirect: 'follow'` → `_fetchT('/config/backups', { redirect: 'manual' })`
- `redirect: 'manual'` → Caddy's 302 produces an `opaqueredirect` response (`r.status === 0`, `r.type === 'opaqueredirect'`) instead of a CORS-blocked network error
- Explicit check before `.json()`: shows amber **"Session expired — reload the page to re-authenticate, then click Refresh again."**
- `restoreBackup()` also guards against submitting the placeholder `— session expired —` option

**`nodered/flows.json`:** Regenerated.

---

## Fix 2 — CloudTAK update: "local changes to the following files would be overwritten by checkout"

### Root cause

`run_cloudtak_update()` ran `git checkout {tag}` without first resetting locally modified tracked files. The TAK Portal update path has always done `git checkout -- .` before its pull; CloudTAK's path was missing the equivalent step.

### What changed

`git checkout -- .` added immediately before `git fetch + git checkout {tag}` in both the local and remote CloudTAK update paths. CloudTAK's persistent data (DB volume, `.env`, `docker-compose.override.yml`) is untracked — not affected.

---

## Fix 3 — CloudTAK update button: no visual feedback during long build

### Root cause

`docker compose build --no-cache` on a low-memory VPS can take 10–20 minutes. The update button was simply disabled with no indication of progress.

### What changed

`startCloudtakUpdate()` now:
- Sets button to spinning `.ct-btn-spinner` indicator + "Updating…" text with reduced opacity
- Restores the original button label and enables the button on completion or error

---

## Fix 4 — "Update Available" banner never shows when Console JS is broken

### Root cause

The update banner is `display:none` in HTML and only revealed by `checkUpdate()` in the Console page `<script>` block. If the script block fails to parse (as in v0.9.31 due to escape-sequence bugs), `checkUpdate()` is never called and users see no update path.

### What changed

**Background cache warmer:** A daemon thread starts 8 seconds after server startup and queries GitHub for the latest release tag, populating `update_cache`.

**Server-side rendered banner:** `CONSOLE_TEMPLATE` checks `update_cache` at render time. If a newer version is cached, `#srv-update-banner` is rendered directly in HTML — visible immediately, no JS required. Includes a plain HTML `<form>` "Update Now" button that POSTs to `/api/update/apply` via standard form submit.

**JS hide on load:** When JS does work, the server-side banner is immediately hidden (JS banner takes over). No double-banner.

---

## Fix 5 — atakatak default-cert-password warning removed

`render_default_cert_password_warning()` now always returns `''`. The fixed-top orange banner warning about the upstream default certificate password was removed — it was too alarmist for operators who know what they're doing.

---

## Fix 6 — Help page: "Force update via CLI" section

New collapsible section added to `/help` with a copyable CLI block:

```bash
cd $(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service)
git fetch https://github.com/takwerx/infra-TAK.git main
git checkout --force -B main FETCH_HEAD
grep '^VERSION' app.py
sudo systemctl restart takwerx-console
```

Includes a copy button and explanation. Appears when Update Now isn't responding and helps operators who are running a broken-JS version (v0.9.31) get back to current.

---

## Fix 7 — TAK Server: self-healing recovery for corrupted .deb installs

### Root cause

When an operator uploads a corrupted or truncated `.deb` (e.g. interrupted browser download), the installer burned through three install attempts (`apt-get install` → `dpkg -i` → `apt-get install --reinstall`) before landing on `FATAL: /opt/tak not found after install`. At that point `deploy_status` was stuck in `error: True`, the page offered no recovery button, and the operator needed SSH access to run `dpkg --purge --force-all takserver && rm -rf /opt/tak` manually.

### What changed

1. **`dpkg-deb --info` validation** before any install attempt — fails fast on corrupted uploads with a clear message instead of burning through all three attempts.

2. **`POST /api/takserver/purge-failed-install`** — new endpoint that runs `dpkg --purge --force-all takserver`, `rm -rf /opt/tak`, deletes any invalid `.deb` uploads (those that fail `dpkg-deb --info`), and resets `deploy_status` to clean.

3. **TAKSERVER_TEMPLATE error state UI** — when `deploy_error` is True, a recovery panel appears below the deploy log with a **"Clean up & retry"** button. One click purges the broken state, deletes the bad upload, and reloads the page to the clean upload screen. No SSH required.

---

## Upgrade notes

**No operator action required.** All fixes are drop-in.

---

## Field validation

- **test6** — SHA `2bb8137`, 3h13m soak, 0 app errors, all containers healthy, 0 `query_wait_timeout`
- **test8** — SHA `2bb8137`, 3h13m soak, 0 app errors, all containers healthy, 0 `query_wait_timeout`
- **test12** — SHA `2bb8137`, 3h13m soak, all containers healthy, 0 `query_wait_timeout`. Pre-existing `docker compose ps` latency spike on this box (2.4s normal, occasionally >5s → `TimeoutExpired` in `detect_modules`) — predates this release, unrelated to any of the seven fixes.
- No operator overrides on any validation box intersecting this release's changes.
- Fix 1 (Node-RED session expiry) confirmed on tak-10: `GET /config/backups` healthy at port 1880, session-expiry path produces `opaqueredirect` as expected.
- Fix 2 (CloudTAK checkout) code-reviewed; `git checkout -- .` insertion is identical to the TAK Portal update path that has been in production since v0.9.x.
- Fix 7 (TAK Server corrupted .deb) code-reviewed; `dpkg-deb --info` is the canonical way to verify a `.deb` before install.
