# v0.9.41-alpha — LDAP spiral fix + Azure External DB hardening

**Date:** 2026-05-28
**Type:** Bug fix — drop-in update via Update Now.
**Status:** RELEASED to `main` 2026-05-28. Validated on tak-test-8 (Azure VM + PostgreSQL Flexible Server, full external-DB deploy end-to-end).

---

## TL;DR

Two independent bug areas patched:

1. **LDAP identification stage spiral** — silent PATCH failure caused every webadmin bind to return error 49, spiral persisted across resync attempts.
2. **Azure External DB deploy workflow** — five hardening fixes covering extension provisioning, deploy gating, SchemaManager execution, uninstall cleanup, and a malformed-XML crash from `&` in generated passwords.

---

## Fix 1 — LDAP identification stage spiral (silent PATCH failure)

### Root cause

`ldap-identification-stage` had `password_stage` set. When Authentik processed a real LDAP bind the identification stage found the user and immediately executed the password stage inline — recursing until Authentik hit its stage depth limit:

```
"error":"exceeded stage recursion depth","event":"failed to execute flow"
"bindDN":"cn=webadmin,ou=users,dc=takldap"
```

Authentik returns LDAP error 49 for any flow failure, so `ldapsearch` saw "Invalid credentials" and `_test_ldap_bind_dn_verdict` classified it as a confirmed password failure — not a spiral. The system then deleted and recreated webadmin, which immediately spiralled again.

`_ensure_ldap_flow_authentication_none` was supposed to PATCH `password_stage: null` before every resync. The PATCH was silently failing (exception swallowed with `except: pass`), the function reported "Flow: OK", and the spiral persisted.

### Fix

`_ensure_ldap_flow_authentication_none` now reads the stage back after patching to verify the PATCH took. If `password_stage` is still set:

1. Logs the failure explicitly
2. DELETEs the broken identification stage
3. Forces `wrong_bindings = True` so the binding recreation path runs
4. `_create_ldap_stage` builds a fresh `ldap-identification-stage` without `password_stage`
5. New bindings wired to the clean stage; outpost force-recreated to flush cached flow config

Additionally: `_ensure_authentik_webadmin` now uses a 10-retry × 6s bind check loop (same as the SA path) after outpost recreation instead of a single immediate probe — fixes a timing race where Docker reports "healthy" before the LDAP service has reconnected.

---

## Fix 2 — Authentik vetted-release pinning on fresh install

Fresh Authentik installs now pin the vetted release tag rather than pulling `latest`. An "unvetted" badge is shown in the console if the running version is newer than what infra-TAK has validated — operator is informed before upgrading.

---

## Fix 3 — Azure External DB deploy workflow (5 fixes)

### 3a — Provision hard-fail on missing Azure extension whitelist

**Before:** `Provision Database` ran `CREATE EXTENSION IF NOT EXISTS` for all 5 required extensions. If Azure's `azure.extensions` server parameter hadn't been set first, the `CREATE EXTENSION` commands failed silently — the step reported `success: true` and let the user proceed to deploy. TAK Server would then fail to start (PostGIS/fuzzystrmatch missing).

**After:** `takserver_external_db_provision()` tracks failed extension creates. If any of the 5 required extensions (`fuzzystrmatch`, `postgis`, `postgis_topology`, `address_standardizer`, `pgcrypto`) fail, the API returns `success: false` with explicit instructions to add them to `azure.extensions` in the Azure Portal before retrying.

### 3b — Deploy gate: must complete Provision + Test Connection first

**Before:** The Deploy TAK Server button was available immediately after saving config — no enforcement that provisioning or connection testing had been done.

**After:**
- **Backend gate** — `deploy_takserver()` blocks `external_db` deploys with HTTP 400 if no DB host is set or if the `martiuser` password has not been stored (which only happens after successful provisioning).
- **Frontend gate** — `window._edbTestAllOk` flag is set only when `testExternalDbConnection()` passes all checks. `startDeploy()` alerts and returns early if the flag is false.

Correct required sequence: **Save Config → Provision Database → Test Connection (all OK) → Deploy TAK Server**.

### 3c — SchemaManager working directory fix

**Before:** The Step 8 SchemaManager call ran as `sudo -u tak java -jar /opt/tak/db-utils/SchemaManager.jar upgrade`, without ensuring the working directory was `/opt/tak`. SchemaManager reads `CoreConfig.xml` from the current directory and fell back to `CoreConfig.example.xml` (pointing at 127.0.0.1), writing the schema to the local PG instead of the Azure RDS.

**After:** Command changed to `cd /opt/tak && java -jar /opt/tak/db-utils/SchemaManager.jar upgrade` — SchemaManager finds the patched `CoreConfig.xml` with the correct Azure JDBC URL and writes the schema to the correct database.

### 3d — Uninstall: clean local PG side-effect

The TAK Server `.deb` postinstall always creates a local `martiuser` role and `cot` database regardless of deployment mode. On re-deploy in `external_db` mode, this stale local state caused `password authentication failed` noise during the postinstall's own schema run.

`_uninstall_tak_server()` now drops the local `cot` database and `martiuser` role unconditionally (even in `external_db` mode), leaving a clean slate for re-deploys.

### 3e — XML-escape password before writing CoreConfig.xml (critical)

**Root cause:** The `martiuser` password alphabet included `&` (`'!@#%^&*'`). A generated password containing `&` written raw into a CoreConfig.xml attribute value produces malformed XML:

```xml
<!-- malformed — & is a reserved XML character -->
<connection ... password="abc&def" .../>
```

The config microservice (`-Dspring.profiles.active=config`) failed to parse the file, crashed before deploying `distributed-configuration` into the Ignite cluster, causing the API service to crash with `Failed to find deployed service: distributed-configuration` → WebGUI inaccessible. Affected roughly 1-in-3 Azure external-DB deploys (probabilistic — depends on whether the random password draw included `&`).

**Fix:**
- Removed `&` from the provision password alphabet (all newly generated passwords are XML-safe)
- Added `html.escape(password, quote=True)` at both CoreConfig.xml write sites (pre-patch at Step 4 and JDBC patch at Step 8) — protects any password already stored in `settings.json` that may contain `&`, `<`, `>`, or `"`

TAK Server's XML parser transparently unescapes `&amp;` → `&` when reading the attribute, so the correct password reaches PostgreSQL.

---

## Changes

- `app.py` `_ensure_ldap_flow_authentication_none` (~line 38469): verify PATCH took; DELETE + force recreation on failure
- `app.py` `_ensure_authentik_webadmin` (~line 39824): 10-retry × 6s bind loop after outpost recreate
- `app.py` `takserver_external_db_provision()` (~line 2820): hard-fail on extension create errors; return `success: false` with portal instructions
- `app.py` `deploy_takserver()` (~line 44634): backend deploy gate for `external_db` mode
- `static/takserver.js` `testExternalDbConnection()` / `startDeploy()`: `window._edbTestAllOk` flag + frontend deploy gate
- `app.py` `run_takserver_deploy()` SchemaManager call (~line 45069): `cd /opt/tak &&` prefix
- `app.py` `_uninstall_tak_server()` (~line 42514): drop local `cot` + `martiuser` in `external_db` mode
- `app.py` provision password alphabet (~line 2705): remove `&`
- `app.py` CoreConfig.xml write sites (~lines 44888, 45037): `html.escape(password, quote=True)`
- `VERSION` remains `0.9.41-alpha`

---

## Validation

Validated on tak-test-8 (Azure VM, Azure PostgreSQL Flexible Server PG 18.3):
- Provision Database hard-failed correctly when `FUZZYSTRMATCH` was removed from `azure.extensions` — instructed operator to fix before retry
- After whitelist corrected: Provision succeeded, all 5 extensions created
- Test Connection: all checks green, deploy gate opened
- Deploy TAK Server: clean 9-step run, SchemaManager applied all 94 migrations to Azure RDS, WebGUI reachable on 8443 and 8446
- Confirmed: `&` in generated password no longer corrupts CoreConfig.xml; config service starts cleanly; WebGUI up on first try
