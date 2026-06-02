# v0.9.40-alpha — External DB (Azure PostgreSQL) end-to-end support + CloudTAK first-time setup guide + MediaMTX readiness fix

**Date:** 2026-05-27
**Type:** New features + UX improvements + bug fixes — drop-in update via Update Now.
**Status:** PENDING T&E validation on test6 / test8 / test12 (≥60 min soak required).

---

## TL;DR

Four feature areas in this release:

1. **Azure PostgreSQL Flexible Server — full end-to-end support.** The External / Managed Database flow now handles every Azure-specific requirement automatically: creates the `cot` database, grants `azure_pg_admin` to `martiuser`, pre-creates all five required extensions (`fuzzystrmatch`, `postgis`, `postgis_topology`, `address_standardizer`, `pgcrypto`) as the admin user so SchemaManager never hits the extension permission wall. Test Connection runs an Azure extension whitelist probe after provisioning and returns exact portal instructions if any are missing. A collapsible "Using Azure Database for PostgreSQL?" guide on the External DB panel shows the exact `azure.extensions` value to copy-paste before provisioning. Uninstall drops the remote `cot` database using stored credentials so re-deploy starts clean.

2. **External DB UX fixes.** Button order corrected (Provision Database is step 2, Test Connection is step 3). Admin username field changed from hardcoded `postgres` to a placeholder. Password with special characters (`#`) no longer breaks psql's `-v` parser (SQL piped via stdin). Provision correctly targets the `postgres` system database first (before `cot` exists). Deploy mode (external_db / two_server) is preserved when the operator uses Configure after initial deploy.

3. **CloudTAK first-time setup guide.** Collapsible "First-Time Setup — Connect CloudTAK to TAK Server" card on the CloudTAK page (visible when running). Three-step guide: create `cloudtakadmin` in TAK Portal (regular user, with org suffix note), download `user.p12` + cert password from TAK Server → Certificates, configure CloudTAK with TAK Server address (`takserver.fqdn`), username+password, and cert. Notes that bootstrap is one-time — subsequent users log in with username and password only, no `.p12` required.

4. **MediaMTX deploy readiness poll.** Deploy log now waits up to 30 seconds for `systemctl is-active mediamtx` before declaring success. Eliminates the "Not Found — Powered by authentik" error that appeared when operators hit `stream.fqdn` immediately after deploy while the service was still starting.

---

## Changes

### External DB — Azure PostgreSQL support

- **Auto-detect Azure endpoint** (`.postgres.database.azure.com`) in Provision Database
- **Auto-create `cot` database** if it doesn't exist (Azure Flexible Server doesn't pre-create it)
- **Grant `azure_pg_admin` to `martiuser`** automatically — required for schema operations
- **Pre-create all 5 extensions as admin**: `fuzzystrmatch`, `postgis`, `postgis_topology`, `address_standardizer`, `pgcrypto` — Azure blocks `CREATE EXTENSION` for non-superusers even after whitelist without this grant
- **Test Connection Azure extension probe**: queries `pg_available_extensions` via the `postgres` system db; returns exact Azure Portal navigation path with the full extension value to copy-paste if any are missing; skipped gracefully (neutral `[SKIP]`) before provisioning so the pre-provision Test Connection still passes
- **Uninstall drops remote `cot` database** when mode is `external_db`: terminates connections, then `DROP DATABASE` using stored `martiuser` credentials; local-mode uninstall path unchanged
- **Uninstall clears saved external DB config** from `settings.json` so the form is blank on next visit (host, password cleared; mode reset to `single_server`)
- **Collapsible Azure pre-flight guide** on the External DB panel with the exact `azure.extensions` value (`FUZZYSTRMATCH,POSTGIS,POSTGIS_TOPOLOGY,ADDRESS_STANDARDIZER,PGCRYPTO`)

### External DB — UX / correctness fixes

- **Button order corrected**: Provision Database is now step 2, Test Connection is step 3
- **Admin username field**: placeholder `e.g. postgres or pgadmin` instead of hardcoded `postgres`
- **psql `-v` special-character fix**: passwords containing `#` (and other chars) no longer break the psql `-v` parser — SQL is now piped via stdin with `\set` directives
- **Provision targets `postgres` DB first**: before `cot` exists, admin connection, user creation, and `GRANT ON DATABASE` all correctly target `postgres`; `cot` is created in a dedicated step
- **Deploy mode preservation**: switching to Configure after an external_db or two_server deploy no longer resets the mode selector to `single_server`
- **`ok=None` correctly serialized as JSON `null`** in Test Connection checks (was being coerced to `false` via `bool(None)`, rendering skipped checks as `[FAIL]`)

### CloudTAK — first-time setup guide

- Collapsible card on CloudTAK page (only shown when CloudTAK is running and not mid-deploy)
- **Step 1**: create `cloudtakadmin` user in TAK Portal — regular user, not admin, any agency + group, set password; includes ⚠ note that TAK Portal appends an org suffix (e.g. `cloudtakadmin-orgname`) and the full suffixed name is what CloudTAK requires
- **Step 2**: download `user.p12` from TAK Server → Certificates (auto-created during deploy); note the cert password shown on that page
- **Step 3**: open CloudTAK, enter `takserver.fqdn` as the TAK Server address, enter `cloudtakadmin-suffix` + password + `user.p12` + cert password; CloudTAK saves and reloads to login page; includes `Cmd+Shift+R` / `Ctrl+Shift+R` force-reload tip if the setup page doesn't appear
- Footer clarifies: bootstrap is one-time; subsequent users create a TAK Portal account and log in with username + password only

### MediaMTX — readiness poll

- After `systemctl restart mediamtx` (final step of deploy), polls `systemctl is-active mediamtx` every 2 seconds for up to 30 seconds before marking deploy complete
- Deploy log shows `✓ MediaMTX is active` when ready; shows actionable warning if not active within 30s
- Eliminates the "Not Found — Powered by authentik" error that appeared when `stream.fqdn` was hit before `:5080` was listening

---

## Validation plan

- [ ] Fresh external_db deploy against Azure PostgreSQL Flexible Server — full flow: Save Config → Provision Database → Test Connection → Deploy TAK Server
- [ ] Verify all 5 extensions pre-created, `azure_pg_admin` granted, SchemaManager runs without extension errors
- [ ] Test Connection after provisioning: `[OK]` for TCP, pg_isready, auth; `[OK]` for Azure extensions
- [ ] Test Connection before provisioning: `[SKIP]` for Azure extensions (not `[FAIL]`)
- [ ] Uninstall with external_db mode: `cot` database dropped on Azure, form cleared on page reload
- [ ] CloudTAK first-time setup guide visible when CloudTAK is running; collapsed by default; arrow rotates on expand
- [ ] MediaMTX deploy log shows readiness wait and `✓ MediaMTX is active` before completion message
- [ ] Standard fleet soak: test6 / test8 / test12, ≥60 min, zero watchdog ALERTs, all containers `(healthy)`
