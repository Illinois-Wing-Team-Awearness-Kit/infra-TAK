# 8446 Webadmin Login — Workflow and Fix

## Critical: TAK Server CoreConfig.xml behavior

**TAK Server rewrites portions of `CoreConfig.xml` at runtime and on startup.** Specifically:

1. **`<File/>` tag**: TAK Server always re-adds a bare `<File/>` tag to the `<auth>` block on startup if one doesn't exist. This is normal and cannot be prevented.
2. **Connector attributes**: TAK Server normalizes `<connector>` elements on startup, potentially resetting `_name`, keystore, and other attributes to its defaults. **Any connector changes made while TAK Server is running will be overwritten on restart.**

### Safe pattern for CoreConfig changes

| What you're changing | Safe while running? | Pattern |
|---------------------|--------------------|----|
| `<auth>` block (LDAP, File) | Yes | Patch, then restart |
| `<connector>` elements (8446 LE cert, names) | **No** | Stop TAK Server → patch → start |
| `<repository>` / JDBC | Yes | Patch, then restart |
| `<security>` / TLS | Yes | Patch, then restart |

**Code rule**: `install_le_cert_on_8446()` and any future connector patches must stop TAK Server before patching CoreConfig.xml, then start it.

---

## Deploy flow (v0.4.0+ / v0.3.9+)

### With Authentik (LDAP)

1. **Authentik** — deploy first. LDAP provider, outpost, flows created. No `webadmin` user yet.
2. **TAK Server** — deploy. You set the **webadmin password**.
   - Step 8: CoreConfig patched for LDAP (`_apply_ldap_to_coreconfig()`)
   - Step 9: **`webadmin` is NOT created in flat-file.** Log says: "Authentik detected — webadmin will be created directly in Authentik (skipping flat-file)"
   - Post-deploy: `_ensure_authentik_webadmin()` creates `webadmin` directly in Authentik with `tak_ROLE_ADMIN` + `authentik Admins` groups, sets the password from settings.
   - LE cert install: Stops TAK Server, patches 8446 connector with LE keystore, starts TAK Server.
3. **Login** — use `webadmin` + the password you set at deploy on port 8446.

### Without Authentik (flat-file)

1. **TAK Server** — deploy. You set the **webadmin password**.
   - Step 9: `UserManager.jar usermod -A -p <password> webadmin` creates `webadmin` in `UserAuthenticationFile.xml`
   - 8446 login uses the flat-file password directly.
2. **Login** — use `webadmin` + the password you set at deploy on port 8446.

---

## Why webadmin is never in the flat-file when Authentik exists

TAK Server has two auth providers: flat-file (`UserAuthenticationFile.xml`) and LDAP. When both are present, **flat-file takes precedence** for any user that exists in it. If `webadmin` exists in the flat-file with hash X, and in Authentik/LDAP with password Y, TAK Server authenticates against hash X — not LDAP.

This caused persistent "invalid username/password" errors because:
- The flat-file hash was set by `UserManager.jar` at deploy time
- The Authentik password was set by `_ensure_authentik_webadmin()`
- These could differ (timing, password rotation, etc.)
- Even when they matched, the flat-file hash format differed from what the user typed

**Fix (v0.3.9)**: When Authentik is detected at deploy time, `UserManager.jar usermod webadmin` is skipped entirely. `webadmin` only exists in Authentik. No shadowing possible.

---

## If you can't log in to 8446 with webadmin

1. **Check TAK Server is fully started** — the API service takes ~3 minutes. Look for `Started TAK Server api Microservice` in `/opt/tak/logs/takserver-api.log`. A 403 before this means the server isn't ready.

2. **Check the 8446 connector** — `grep '8446' /opt/tak/CoreConfig.xml`. It should show:
   ```
   <connector port="8446" clientAuth="false" _name="LetsEncrypt" keystore="JKS" keystoreFile="certs/files/takserver-le.jks" .../>
   ```
   If `_name="cert_https"` or keystore attributes are missing, the LE cert install was overwritten. Stop TAK Server, fix the connector, start it.

3. **Use "Resync LDAP to TAK Server"** — on the TAK Server page. This re-patches CoreConfig, removes any stale `webadmin` flat-file entry, and syncs to Authentik.

4. **Use "Sync webadmin to Authentik"** — pushes the password from settings to Authentik. Use if password drift suspected.

---

## Summary

| Scenario | Where webadmin lives | 8446 auth path |
|----------|---------------------|----------------|
| Authentik deployed | Authentik only (LDAP) | TAK Server → LDAP outpost → Authentik |
| No Authentik | `UserAuthenticationFile.xml` (flat-file) | TAK Server → flat-file |
| Upgrading from pre-v0.3.9 with Authentik | May be in both — Resync LDAP cleans flat-file | After resync: LDAP only |
