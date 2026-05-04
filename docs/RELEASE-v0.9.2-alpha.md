# Release Notes — v0.9.2-alpha

## What ships

Six features shipped in one build.

---

### Feature A — Authentik Reputation Policy

Flow-level brute-force blocking inside Authentik, complementary to Fail2ban.

- Every failed login decrements the source-IP score by 1
- When the score drops below the threshold (default -5), Authentik blocks the login at the flow level — before the password stage even runs
- Scores recover automatically over time
- Applied automatically to `ldap-authentication-flow` on first startup via `_authentik_setup_reputation_policy()`
- **UI card** on the Authentik page (visible when Authentik is running): enable/disable toggle, configurable threshold, live table of top flagged IPs, "Clear All Scores" button
- **API routes**:
  - `GET /api/authentik/reputation/status`
  - `POST /api/authentik/reputation/config`
  - `POST /api/authentik/reputation/scores/clear`

---

### Feature B — SSH Fail2ban Jail

Extends the existing Fail2ban module with an SSH jail for host-level brute-force protection.

- Uses the built-in `sshd` filter; monitors `/var/log/auth.log`
- Default thresholds: maxretry=3, findtime=10 min, bantime=60 min (stricter than Authentik — SSH brute-force is more severe)
- Opt-in via the Fail2ban page — does not require Authentik to be installed
- Guard Dog email alert fires on ban (reuses `infratak-guarddog.conf`)
- **New UI card** on the Fail2ban page: enable toggle, stats, config, whitelist (ignoreip), ban list, unban button
- **API routes**:
  - `GET /api/fail2ban/ssh/status`
  - `POST /api/fail2ban/ssh/config`
  - `POST /api/fail2ban/ssh/unban`

---

### Feature C — TAK Server Snapshots

Automated and on-demand snapshots of the full TAK Server state:

| What | How | Stored at |
|------|-----|-----------|
| Installed TAK version | `dpkg -l takserver` | snapshot metadata |
| `CoreConfig.xml` | file copy | `/opt/tak/snapshots/<label>/CoreConfig.xml` |
| JVM heap settings | file copy | `/opt/tak/snapshots/<label>/takserver.default` |
| PostgreSQL cot dump | `pg_dump -Fc` via `docker exec takserver-db` | `/opt/tak/snapshots/<label>/cot.pgdump` |
| Certificates | directory copy | `/opt/tak/snapshots/<label>/certs/` |

**Pre-upgrade snapshot**: automatically taken at the start of every `run_takserver_upgrade()`. If the snapshot fails the upgrade is **aborted** — current data is protected.

**Scheduled snapshots**: systemd timer (daily by default at 02:00 local time). Operator configures schedule and retention count from the UI.

**Manual snapshot**: "Take Snapshot Now" button on the TAK Server page.

**Off-box export**: each snapshot has a "Download" button (`GET /api/takserver/snapshot/<label>/download`) that streams a `.tar.gz`.

**API routes**:
- `GET /api/takserver/snapshots`
- `POST /api/takserver/snapshot/run`
- `GET /api/takserver/snapshot/status`
- `GET /api/takserver/snapshot/<label>/download`
- `DELETE /api/takserver/snapshot/<label>`
- `GET /api/takserver/snapshot/schedule`
- `POST /api/takserver/snapshot/schedule`

---

### Feature D — TAK Server Rollback

One-click restore from any snapshot:

1. Validates snapshot has a `cot.pgdump`
2. Stops `takserver`
3. Restores `CoreConfig.xml`, `takserver.default`, `certs/`
4. Runs `pg_restore --clean -d cot` inside the `takserver-db` container
5. Starts `takserver`

**UI card** on TAK Server page: snapshot table, Restore / Download / Delete buttons, scheduled snapshot config.

**API routes**:
- `POST /api/takserver/rollback`
- `GET /api/takserver/rollback/log`

---

### Feature E — infra-TAK Console Rollback

Before every "Update Now", the console records the current version and git tag. If the update breaks something, one click rolls back.

- "Update Now" saves `settings.console_rollback = {available, version, tag, snapshot_at}` before pulling
- Console page shows a yellow "Rollback available — vX.Y.Z" bar after updates
- One click: fetches the previous tag from GitHub and checks it out, then restarts
- Rollback availability is cleared after use (one rollback per update cycle)

**API routes**:
- `POST /api/console/rollback`

---

### Feature F — TAK Server Plugins

New collapsible "TAK Server Plugins" section on the TAK Server page (visible when TAK is installed). Based on the official TAK Server Plugin SDK (tak.gov) and reference plugins including TAK CAD Server Plugin (RTX/BBN).

**Plugin mechanics (from SDK):**
- Plugin JARs live at `/opt/tak/lib/` — loaded by the plugin manager on startup
- Plugin configs auto-generated at `/opt/tak/conf/plugins/<fully.qualified.ClassName>.yaml` on first run
- Restart required after any JAR or config change

**What the section does:**

- **Installed plugins list**: on section open, scans `/opt/tak/lib/*.jar` and `/opt/tak/conf/plugins/*.yaml` and renders a table — plugin name, config status, Edit config button, Remove button
- **Install**: single upload area accepts `.jar` (copied to `/opt/tak/lib/`) or `.yaml` (copied to `/opt/tak/conf/plugins/`) — portal routes by extension. After any file placement, a "Restart TAK Server to apply changes" banner appears with a Restart button
- **Edit config**: inline expand per plugin — reads YAML, shows in monospace textarea, save writes it back. If no YAML yet: "Config will be generated on first run after restart"
- **Remove**: deletes JAR from `/opt/tak/lib/` + matching YAML from `/opt/tak/conf/plugins/` if present, then prompts restart

**API routes:**
- `GET /api/takserver/plugins/list`
- `POST /api/upload/takserver-plugin`
- `POST /api/takserver/plugins/install-jar`
- `POST /api/takserver/plugins/install-yaml`
- `GET /api/takserver/plugins/config/<classname>`
- `POST /api/takserver/plugins/config/<classname>`
- `POST /api/takserver/plugins/remove/<jarname>`

---

## Scope discipline — what is NOT in v0.9.2

- Two-server TAK rollback (complex, lower demand)
- Rolling back TAK Portal or Authentik integrations
- Rolling back infra-TAK more than one version back

## Version

`0.9.1-alpha` → `0.9.2-alpha`
