# v0.9.4 — Hotfix Plan

> v0.9.3 shipped to main before testing was complete. v0.9.4 closes the bugs found during post-release testing and completes the remaining v0.9.3 test checklist items.

---

## Bug fixes carried over from v0.9.3 testing

### CloudTAK Reset Config — full connection reset + nginx worker fix

**Bugs found during tak-10 testing (2026-05-09):**

1. **SQL only cleared `auth`, not the connection URL** — Reset config only ran `UPDATE server SET auth = '{}'::jsonb`. With `url`, `api`, and `webtak` still populated, the CloudTAK API attempted a TAK Server connection on restart with no credentials. The Node.js event loop blocked on the failed SSL connection, making every HTTP request hang indefinitely (`map.<fqdn>` → `about:blank`).

   **Fix (shipped):** Reset SQL now clears all four fields: `UPDATE server SET auth = '{}'::jsonb, url = '', api = '', webtak = '' WHERE id = 1;`

2. **nginx workers crash on every container start (exit code 2)** — The CloudTAK container image's `nginx.conf.js` generates `user nginx;`. Workers immediately fail to write to `/dev/stdout`/`/dev/stderr` after dropping root privileges and die. The nginx master stays alive with zero workers — port 5000 accepts TCP connections but never serves a response (`about:blank` in browser).

   **Fix (shipped):** `_cloudtak_fix_nginx_user()` helper patches `user nginx;` → `user root;` in the generated nginx.conf and reloads workers. Called after every API container start/restart in: `cloudtak_reset_server_config`, `run_cloudtak_redeploy`, `run_cloudtak_update`, `cloudtak_control` (start/restart).

### Feature A — CPU/RAM Refresh button JS SyntaxError (two-stage fix)

**Bug 1 (first attempt):** `font-family:\'JetBrains Mono\',monospace` inside a JavaScript string was believed to be the cause.

**Bug 2 (root cause):** `refreshBtn` was built with `\'` escape sequences inside Python's triple-quoted `CONSOLE_TEMPLATE`. Python resolves `\'` → `'` before rendering, turning `'...(\''+hostId+'\'')"...'` into adjacent string literals `'...(''` with no `+` operator — `SyntaxError: Unexpected string`. Both `toggleResourceBreakdown` and `refreshResourceBreakdown` were undefined.

**Fix (shipped):** Switched `refreshBtn` to a JavaScript template literal (backtick string) so `${hostId}` handles the variable substitution with no quote escaping needed.

---

## Remaining v0.9.3 test items to complete in v0.9.4

The following items from `docs/TEST-v0.9.3-alpha.md` were not reached before v0.9.3 shipped:

- Bug Fix 2 — `cert-metadata.sh` ownership auto-fix
- Feature A — CPU/RAM Refresh button (JS fix shipped; needs live re-test)
- Feature B — MediaMTX RTSP Fail2ban jail (enable/disable, thresholds, post-update auto-install)
- Feature C — Kernel Patch Banner (API + dismiss behavior)
- Feature D — Authentik Domain Migration audit panel + pre-flight confirm dialog
- Feature E — Caddy Custom Blocks hint (UI + COMMANDS.md section)
- Feature F — Container hardening audit (CapDrop verification in logs)

---

### Feature B — MediaMTX RTSP Jail UI gaps (found during testing 2026-05-09)

1. **"Currently Watching" stat has no expand panel** — shows a count (e.g. 10) but there's no onclick/caret to reveal which IPs fail2ban is watching. TAK Server jail has `toggleWatchingPanel()` for its equivalent stat; MediaMTX jail never received this.

2. **"Currently Banned" caret hidden at 0** — `caret.textContent` is set to `''` when `ips.length === 0`, making the stat card look non-interactive even though clicking it does open the (empty) ban panel.

**Fix needed:** Add a watching panel + `toggleMtxWatchingPanel()` to the MediaMTX jail section; keep the `▼ details` caret visible at all counts (show count in parentheses or just always show it).

---

## Scope discipline — what is NOT in v0.9.4
- Split-server snapshot/rollback (→ v0.9.5)
- Non-root console migration (→ v0.9.6)
- RTSPS (port 8322) MediaMTX jail (→ future)
- Per-feed Node-RED certs (→ future)
