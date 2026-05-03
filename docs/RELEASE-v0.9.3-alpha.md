# Release Notes — v0.9.3-alpha

## What ships

### Part A — TAK Server Snapshots

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

### Part B — TAK Server Rollback

One-click restore from any snapshot:

1. Validates snapshot has a `cot.pgdump`
2. Stops `takserver`
3. Restores `CoreConfig.xml`, `takserver.default`, `certs/`
4. Runs `pg_restore --clean -d cot` inside the `takserver-db` container
5. Starts `takserver`

**API routes**:
- `GET /api/takserver/snapshots`
- `POST /api/takserver/snapshot/run`
- `GET /api/takserver/snapshot/status`
- `GET /api/takserver/snapshot/<label>/download`
- `DELETE /api/takserver/snapshot/<label>`
- `POST /api/takserver/rollback`
- `GET /api/takserver/rollback/log`
- `GET /api/takserver/snapshot/schedule`
- `POST /api/takserver/snapshot/schedule`

**UI card** on TAK Server page: snapshot table, Restore / Download / Delete buttons, scheduled snapshot config.

### Part C — infra-TAK Console Rollback

Before every "Update Now", the console records the current version and git tag. If the update breaks something, one click rolls back.

- "Update Now" saves `settings.console_rollback = {available, version, tag, snapshot_at}` before pulling
- Console page shows a yellow "Rollback available — vX.Y.Z" bar after updates
- One click: fetches the previous tag from GitHub and checks it out, then restarts
- Rollback availability is cleared after use (one rollback per update cycle)

**API routes**:
- `POST /api/console/rollback`

## Scope discipline — what is NOT in v0.9.3

- Two-server TAK rollback (complex, lower demand)
- Rolling back TAK Portal or Authentik integrations
- Rolling back infra-TAK more than one version back

## Version

`0.9.2-alpha` → `0.9.3-alpha`
