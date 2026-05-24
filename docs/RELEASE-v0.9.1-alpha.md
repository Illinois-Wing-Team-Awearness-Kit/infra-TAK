# v0.9.1-alpha Release Notes

## What this fixes

Three gaps in the v0.9.0 Fail2ban module. Auto-heals on next console restart after upgrade. No user action required beyond pulling the update.

---

### Bug 1 — Install silently succeeded even when the fail2ban daemon didn't start

`_fail2ban_install_and_configure` ran `systemctl enable --now fail2ban` and immediately wrote `last_outcome: applied` — even if the daemon failed to come up. On some Ubuntu images fail2ban installs as disabled and doesn't start on the first enable attempt.

**Fix:** After the enable call, poll `systemctl is-active fail2ban` up to 3 times (2 seconds apart). If still not active, the install now logs a clear `FAILED` message and returns `False` without writing `last_outcome: applied`. The operator sees the failure in the install log box and can investigate with `systemctl status fail2ban` on the server.

---

### Bug 2 — Jail toggle silently failed when the daemon was not running

`fail2ban-client reload` (called when enabling either jail) is a no-op or errors when the `fail2ban` daemon is `inactive`. Servers that had Fail2ban installed but the daemon stopped (reboot, manual stop) would appear to save jail config but the ban protection was never active.

**Fix:** Both `fail2ban_authentik_toggle_api` and `fail2ban_tak_config_api` now check `systemctl is-active fail2ban` before reloading. If the daemon is not active, they run `systemctl enable --now fail2ban` first. This means toggling any jail ON also self-heals a stopped daemon.

The `/api/fail2ban/status` endpoint now includes `daemon_running: bool`. The Fail2ban page shows a red warning banner if the daemon is down even though Fail2ban is installed — directing the operator to toggle a jail or run `systemctl enable --now fail2ban`.

---

### Bug 3 — Console module card showed no "Fail2ban" label

The Console card template suppresses the module name when the module has an `icon_url` (so logo cards don't double-label). Fail2ban has a logo URL, so no name was shown — leaving just the logo image with no text.

**Fix:** Added `fail2ban` to the exception list alongside `takportal`, `fedhub`, and `emailrelay`. The name "Fail2ban" now renders below the logo on the Console card (and the Marketplace card when not installed). Same fix applied to both `CONSOLE_TEMPLATE` and `MARKETPLACE_TEMPLATE`.

---

## Changes shipped

### `app.py`

- **VERSION**: `0.9.0-alpha` → `0.9.1-alpha`
- **`_fail2ban_install_and_configure`**: polls `systemctl is-active fail2ban` after enable, returns `False` on failure instead of recording `applied`
- **`fail2ban_authentik_toggle_api`** (enable branch): adds daemon-running guard before `fail2ban-client reload`
- **`fail2ban_tak_config_api`** (enable branch): same daemon-running guard
- **`fail2ban_status_api`**: adds `daemon_running` field to response
- **`FAIL2BAN_TEMPLATE`**: adds `#daemon-warn` banner element; `loadStatus` shows/hides it based on `daemon_running`
- **`CONSOLE_TEMPLATE` + `MARKETPLACE_TEMPLATE`**: `fail2ban` added to module-name exception list

---

## Operator acceptance check

```bash
# After pull and restart, confirm version
grep '^VERSION' ~/infra-TAK/app.py

# Confirm fail2ban daemon is running
systemctl is-active fail2ban

# If stopped, toggling either jail ON from the UI will start it automatically
# Or manually:
systemctl enable --now fail2ban
```

---

## What was explicitly NOT shipped

- Authentik Reputation policy — deferred to v0.9.2+
- SSH jail — deferred
- TAK Server rollback — still parked
