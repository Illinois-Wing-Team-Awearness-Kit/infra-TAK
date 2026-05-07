# v0.9.4 — Feature Plan

> Not yet implemented. This is a planning document.

---

## TAK Server Snapshots — split / two-server support

Currently, `_tak_snapshot()` runs `sudo -u postgres pg_dump -Fc cot` locally on the console host. In a standard single-server deployment this is correct — postgres is co-located with the TAK Server application. In a **split (two-server) deployment**, the PostgreSQL database lives on Server One while the infra-TAK console runs on Server Two. The local pg_dump either fails (no local postgres) or captures nothing useful.

**Plan:**

- `_tak_snapshot()` checks `settings.tak_two_server` — if true, uses the existing `server_one` SSH key/host config to stream the pg_dump from Server One:
  ```bash
  ssh server_one "sudo -u postgres pg_dump -Fc cot" > /opt/tak/snapshots/<label>/cot.pgdump
  ```
- `_tak_rollback()` checks the same flag — if true, streams the `.pgdump` back to Server One via SSH and runs `pg_restore` there instead of locally.
- Config files (CoreConfig.xml, UserAuthenticationFile.xml, /etc/default/takserver, certs/) already live on Server Two — those snapshot/restore steps are unchanged.
- The two-server SSH infrastructure (`server_one` config, `_ssh_probe()`, key management) is already in place and will be reused.

**Current behavior on split deployments (v0.9.2–v0.9.3):** snapshot captures config files and certs correctly but skips the database dump, making rollback fail with `has no cot.pgdump`. Operators on split deployments should use manual `pg_dump` on Server One until v0.9.4 ships.

---

## Reminder — v0.9.3 scope

v0.9.3 is dedicated to the non-root console migration (`takwerx` sudo user). Split-server snapshot support is explicitly deferred to v0.9.4 to keep v0.9.3 focused.
