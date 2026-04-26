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

**Fix:** Before aborting, `deploy.sh` now checks whether `flows.json` has any `http in` routes. Zero routes = fresh install (nothing to protect) → deploy proceeds normally. Non-zero routes + missing context = potentially lost data → abort still fires to protect existing configs.

| File | Change |
|------|--------|
| `nodered/deploy.sh` | Fresh-install detection added to abort gate — skips abort when `flows.json` has zero http-in routes |

---

*More fixes and features will be added to this release. Notes are in progress.*
