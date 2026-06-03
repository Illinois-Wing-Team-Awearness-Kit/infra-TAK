# Active Context — infra-TAK

> Current work focus, recent changes, next steps, and active decisions. Updated frequently.

## Current state (snapshot)

- **Active branch:** `dev` — v0.9.42-alpha complete, ready to selective-merge to `main`.
- **Latest tag on `main`:** `v0.9.41-alpha` (`VERSION = "0.9.41-alpha"`). Released 2026-05-28.
- **Dev tip SHA:** `70207c8` (Guard Dog TVR healthcheck fix — last commit before ship).

## In-flight: v0.9.42-alpha (2026-05-29) — shipping now

New module: **TAK Video Restreamer** (Flask + MediaMTX + FFmpeg). Full details in `docs/RELEASE-v0.9.42-alpha.md`.

Key decisions made during this release:
- TVR uses host port **3100** (not 3000 — TAK Portal owns 3000).
- TVR has **no Authentik `forward_auth` wrapper** — it has its own built-in Flask login. Double-login was rejected; single TVR login is the UX.
- Guard Dog health check uses `GET /login` (port 3100) — TVR has no `/api/health` endpoint.
- TVR and MediaMTX are **mutually exclusive** — same streaming ports (8554/8555/8890/1935/8888). Marketplace enforces bidirectional blocking.

## Recent release: v0.9.41-alpha (2026-05-28)

Two independent bug areas patched in this release:

### LDAP identification stage spiral fix
- `_ensure_ldap_flow_authentication_none`: verify PATCH took effect by reading stage back; on failure DELETE + force recreation. Previously a silent `except: pass` caused `password_stage` to persist, resulting in Authentik recursion depth errors on every bind → LDAP error 49 → system deleted/recreated webadmin → spiralled again.
- `_ensure_authentik_webadmin`: 10-retry × 6s bind loop after outpost recreate (was single immediate probe — timing race).
- Authentik vetted-release pinning on fresh install; "unvetted" badge if running version is ahead of validated.

### Azure External DB hardening (5 fixes)
1. **Provision hard-fail** — `takserver_external_db_provision()` now returns `success: false` if any of the 5 required Azure extensions fail to create, with explicit Azure Portal instructions. Previously reported `success: true` on silent extension failures.
2. **Deploy gate** — backend blocks `external_db` deploy if no host or no martiuser password stored; frontend `window._edbTestAllOk` flag blocks Deploy button until Test Connection passes.
3. **SchemaManager cwd fix** — `cd /opt/tak && java -jar ...` ensures SchemaManager finds the patched `CoreConfig.xml` (not `CoreConfig.example.xml` → was writing schema to local PG instead of Azure RDS).
4. **Uninstall cleans local PG side-effect** — `_uninstall_tak_server()` now drops local `cot` DB + `martiuser` even in `external_db` mode; previously left stale state causing `password authentication failed` on re-deploy.
5. **XML-escape password** — `&` in generated password produced malformed CoreConfig.xml → config service crash → no `distributed-configuration` Ignite service → API crash → WebGUI inaccessible (~1-in-3 deploys). Fixed: removed `&` from alphabet, added `html.escape(pass, quote=True)` at both CoreConfig.xml write sites.

## Active focus: next release (v0.9.42-alpha)

No active in-flight work on dev. Ready to pick up next items from backlog.

## Recent changes worth remembering

- **CoreConfig.xml XML safety (v0.9.41):** Any password written into a CoreConfig.xml attribute value must be `html.escape()`-ed. TAK Server's XML parser automatically unescapes `&amp;` → `&` at read time, so the correct password reaches PostgreSQL.
- **SchemaManager behaviour:** Does NOT accept JDBC CLI arguments. Reads `CoreConfig.xml` from the current working directory. Always `cd /opt/tak` before invoking.
- **Azure `azure.extensions` must be set before Provision Database.** infra-TAK v0.9.41+ now hard-fails and tells the operator exactly what to fix. Pre-v0.9.41 was silent.
- **Azure PostgreSQL Flexible Server PG version:** Use PG 15 for TAK Server deployments. PG 18 (Azure default) is outside TAK Server 5.7's tested envelope — SchemaManager applies 94 migrations fine but Flyway logs a version warning; production behavior untested.
- **`_ensure_ldap_flow_authentication_none` PATCH verification (v0.9.41):** Now reads the stage back after PATCH. Critical: the function previously swallowed PATCH failures silently — any future changes to this function must preserve the read-back verification.

## Open items / watch list

- **flows.json conflict on `git pull`** is a recurring footgun on tester VPSes. Recovery: `git checkout -- nodered/flows.json && git pull && bash nodered/deploy.sh --no-pull`.
- **TLS node `tls=undefined` in deploy log** for dynamic engine tabs created before the v0.6+ TLS fix — harmless cosmetic, resolves once those tabs are rebuilt via Configurator.
- **`Skipped configurator.html template injection (EACCES)`** — appears in deploy log when `build-flows.js` runs inside the container. Harmless.
- **Phase 0 spike (flat-file `nodered` user) — NOT YET RUN.** `docs/SPIKE-flatfile-nodered.md` has six curl tests (T0–T6).

## Active decisions

- **No static feeds in `flows.json`.** `FEEDS=[]` in `build-flows.js` — non-negotiable.
- **Always use `nodered/deploy.sh`.** Never raw-`docker cp flows.json` into the container.
- **Read upstream docs first.** Before debugging Authentik / TAK Server / Caddy / Node-RED behavior, find the project's official docs. `.cursor/rules/consult-upstream-docs.mdc` codifies the rule.
- **Reuse what works first.** When adding a variant (remote deploy, new target, new env), try the existing local pattern with minimal tweaks.
- **Cardinal LDAP rule.** The LDAP outpost (`authentik-ldap-1`) is NEVER recreated as a side effect of server/worker recreates — only when its own config genuinely changed.
- **XML-escape all passwords written into CoreConfig.xml.** `html.escape(password, quote=True)` at every write site. The XML parser handles unescape transparently.

## Next steps

- Ship v0.9.42-alpha to `main` (selective merge + tag).
- Consider adding note in UI for Azure PostgreSQL: recommend PG 15 over PG 18.
- Two-server snapshot rollback (pg_dump over SSH) still pending from backlog.
- Future: fork `raytheonbbn/tak-video-restreamer` to `takwerx/tak-video-restreamer`, add `DISABLE_AUTH=true` env var to skip TVR's own login when behind Authentik. Parked for v0.9.43+.

## How to resume after a memory reset

1. Read `README.md` top line for current release version.
2. Read this file (`memory-bank/activeContext.md`) for context.
3. Read `memory-bank/progress.md` for what's known to work and what's known to be broken.
4. Re-read `.cursorrules` and `.cursor/rules/consult-upstream-docs.mdc` before touching Node-RED, Authentik, or any third-party config.
5. If the operator says **"update memory bank"** — review every file in `memory-bank/`, even if no changes are needed. Pay special attention to `activeContext.md` and `progress.md`.
