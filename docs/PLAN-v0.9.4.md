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

### Feature A — CPU/RAM Refresh button JS SyntaxError

`font-family:\'JetBrains Mono\',monospace` inside a single-quoted JavaScript string produced `SyntaxError: Unexpected string` at parse time, leaving `toggleResourceBreakdown` and `refreshResourceBreakdown` undefined — "What's using CPU/RAM?" did nothing.

**Fix (shipped):** Removed the redundant `font-family` from the refresh button style (inherits from parent div).

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

## Scope discipline — what is NOT in v0.9.4
- Split-server snapshot/rollback (→ v0.9.5)
- Non-root console migration (→ v0.9.6)
- RTSPS (port 8322) MediaMTX jail (→ future)
- Per-feed Node-RED certs (→ future)
