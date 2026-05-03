# v0.7.4-alpha Release Notes

---

## ⚠️ Action Required: Resync LDAP to TAK Server

If you haven't already done this, **do it now.**

Go to **TAK Server page → Resync LDAP to TAK Server**.

This fixes password changes taking up to 24 hours to propagate to ATAK/iTAK devices. After Resync, new passwords take effect within 2 minutes. Applies to every existing deployment.

---

## Bug Fixes

### `Cannot GET /configurator` on fresh installs and new Node-RED deployments

**Symptom:** After installing infra-TAK or deploying Node-RED for the first time, navigating to `nodered.<domain>/configurator` returned `Cannot GET /configurator` (404). The Node-RED editor was accessible, but the Configurator UI was not.

**Root cause:** The `deploy.sh` safety gate introduced in v0.7.3-alpha — which prevents wiping configs by aborting if no context data is found — was triggering incorrectly on fresh installs. A fresh Node-RED has no saved configs by definition, so the gate fired and aborted the deploy before flows were installed. No flows = no `/configurator` route.

**Fix:** Before aborting, `deploy.sh` now checks two things:

1. Does `flows.json` have any `http in` routes? Zero routes = fresh install → proceed.
2. Did the live API backup confirm all config arrays are length 0? Empty context on a server that hasn't saved any feeds is not a data loss event → proceed.

The abort only fires when routes exist AND at least one config array was previously non-empty but is now missing — the one case where aborting is genuinely protecting you.

| File | Change |
|------|--------|
| `nodered/deploy.sh` | Abort gate now distinguishes fresh installs, unconfigured servers, and actual data loss events |

---

### External / Managed Database (AWS RDS): one-click provisioning and fully automated deploy

The External / Managed DB deploy mode introduced in v0.7.1 now handles AWS RDS end-to-end without any manual SQL or SSH.

**New: Provision Database button**

The TAK Server deploy page now has a **Provision Database** step directly in the External DB config panel. Fill in your RDS admin credentials and click **Provision Database** before deploying — infra-TAK connects as your admin user and:

- Creates the `martiuser` application account (or updates its password if it already exists)
- Grants full database and schema privileges
- On AWS RDS, automatically grants `rds_superuser` to `martiuser` — the permission required for TAK Server to manage PostGIS extensions

If you leave the App User Password blank, a strong password is auto-generated and filled in for you.

**Automated deploy — no post-deploy SQL**

CoreConfig.xml is now patched to the RDS endpoint immediately after package install, before TAK Server's first start. SchemaManager runs against RDS from the start and again as a safety net during the final restart step. On a correctly provisioned database, the full deploy completes without any manual intervention.

**UI**

- Password fields in the External DB panel now have show/hide toggles
- The selected deployment mode (Single Server / Two-Server / External DB) now persists correctly when navigating the deploy config — it no longer reverts to Single Server when you open the config panel

| File | Change |
|------|--------|
| `app.py` | `POST /api/takserver/external-db/provision`: creates user, grants privileges, auto-grants `rds_superuser` on RDS; pre-deploy JDBC patch; explicit SchemaManager step at final restart |
| `static/takserver.js` | Provision Database panel, App User Password + Admin Password show/hide toggles, deployment mode persistence fix |

---

### CloudTAK overlays and tiles fail with CORS error (duplicate `Access-Control-Allow-Origin`)

**Symptom:** KMZ overlays, PMTiles overlays, and any file displayed as a map layer fail to render in CloudTAK with "Website Error — Failed to fetch." Browser DevTools shows every tile request to `tiles.<domain>` as a CORS error despite the server returning HTTP 200.

**Root cause:** The generated Caddyfile included `header Access-Control-Allow-Origin *` on the `tiles.<domain>` and `video.<domain>` stanzas. Both the CloudTAK tile server (nginx) and MediaMTX already set this header themselves. Caddy added a second copy, producing `Access-Control-Allow-Origin: *, *` — two values — which browsers reject per the CORS spec.

**Fix:** Removed the three `header Access-Control-Allow-Origin *` directives from the Caddyfile template. Both backends supply the header themselves.

After updating, go to **Caddy SSL → Domains → Save & Reload Caddy** to regenerate your Caddyfile and pick up the fix.

| File | Change |
|------|--------|
| `app.py` | Removed `header Access-Control-Allow-Origin *` from `tiles.<domain>` and `video.<domain>` Caddyfile stanzas |
