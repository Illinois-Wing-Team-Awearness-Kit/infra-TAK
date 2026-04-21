# infra-TAK v0.6.7-alpha — DataSync read-only missions + multi-flow shared missions; FedHub sudo fix; Postfix VPS fix

Release date: April 2026

---

## Summary

**v0.6.7-alpha** delivers three operator-facing fixes discovered in field testing:

1. **DataSync read-only missions now work end-to-end.** Admin cert connections (Node-RED) are automatically elevated to `MISSION_OWNER` after subscribing, so data flows into `MISSION_READONLY_SUBSCRIBER` missions while field devices stay read-only. No workarounds needed.
2. **Multiple Node-RED flows can share a single DataSync mission** (e.g. KML + ArcGIS to the same feed) without erasing each other, using `strictMode: false` in the Configurator.
3. **Federation Hub cert generation/rotation** now detects missing passwordless sudo early and prints an actionable fix instead of failing silently.
4. **Postfix install** no longer fails on VPS configs where `hostname -f` returns an unresolvable name (`mydomain=0` error).
5. **TAK Server channel support** for manually-issued certs (Skydio drones, `makeCert.sh`) via `x509useGroupCacheRequiresExtKeyUsage="false"` in the generated `<auth>` block.

---

## DataSync — Read-only mission write access

### Problem

Creating a DataSync mission/feed with `defaultRole: MISSION_READONLY_SUBSCRIBER` (the correct setting for field-user read-only access) silently blocked all writes from Node-RED. TAK Server's `PUT /subscription` assigns the mission's `defaultRole` to the subscriber — **even for admin** — downgrading the effective role. Subsequent `PUT /contents` calls returned `200 OK` but UIDs never appeared in the mission.

### Fix

The `Build subscribe URL` function node in all DataSync engine tabs (ArcGIS + TFR) now automatically performs a role elevation call 5 seconds after subscribe:

```
PUT /Marti/api/missions/{name}/role?username=admin&clientUid=admin&role=MISSION_OWNER
```

Uses Node.js `https` module with the admin cert (`/certs/admin.pem`). Fire-and-forget, 5 second delay gives TAK time to register the subscription before the elevation fires.

**Debug log sequence (confirms working):**
```
"Subscribing to <mission> as admin"                    ← subscribe
"Elevated admin to MISSION_OWNER on <mission> (HTTP 200)"  ← 5s later
"<feed> PUT -> N UIDs -> ..."                          ← 30s later, writes succeed
```

**Verified 2026-04-20:** Mission created as `MISSION_READONLY_SUBSCRIBER` in TAK Portal, Node-RED admin cert writes 13 UIDs, ATAK field devices see data read-only. No `defaultRole` change needed.

### What this corrects in the docs

The old guidance ("feed's `defaultRole` must be `MISSION_SUBSCRIBER` for writes") was incorrect. The correct setup is:

| Setting | Value |
|---------|-------|
| `defaultRole` | `MISSION_READONLY_SUBSCRIBER` |
| Admin write access | Automatic via role elevation in Node-RED |
| Field device access | Read-only (they get `defaultRole`) |

---

## DataSync — Multiple flows sharing one mission

### Use case

KML network link (FIRIS only, 15 min faster than ETL) and ArcGIS feature service (USFS + CAL FIRE) writing to the same DataSync mission.

### Problem

With `strictMode: true` (default), each flow's reconciler deletes any mission UID it doesn't recognize — causing the ArcGIS flow to erase KML UIDs and vice versa on every poll.

### Fix

Set `strictMode: false` on both flows in the Configurator ("Strict mission ownership" checkbox). Each flow then only deletes UIDs that start with **its own prefix** (`kml-*` vs `arcgis-*`) — they never touch each other's data.

**Recommended architecture for mixed KML + ArcGIS feeds:**

```
FIRIS polygons  → KML engine   (uidPrefix: kml-)    → shared mission
USFS + CALFIRE  → ArcGIS engine (uidPrefix: arcgis-) → same mission
```

- Remove FIRIS from the ArcGIS source filter — KML owns FIRIS exclusively
- KML provides the 15-minute lead time; ArcGIS adds multi-agency data alongside
- Neither flow erases the other's UIDs
- Both flows use `strictMode: false`

---

## Node-RED deploy — template sync improvements

### `libs` property now synced alongside `func`

The `deploy.sh` sync step previously only updated function node **code** (`func`) in preserved engine tabs. The `Build subscribe URL` node's new `https`/`fs` module imports (via `libs`) were not being applied to existing tabs, causing `_nodeHttps is not defined` errors.

**Fix:** `template-functions.json` now stores `{ func, libs }` objects (backward-compatible with old string format). The sync step applies both `func` and `libs` when updating preserved function nodes.

---

## Federation Hub — sudo pre-flight check

### Problem

FedHub cert generation and cert rotation run `sudo` commands over SSH (no TTY). If the SSH user requires a password for `sudo`, the commands fail silently with:
```
sudo: a terminal is required to read the password
✗ Certificate rotation failed
```

### Fix

Both cert generation and cert rotation now run `sudo -n true` as a pre-flight check. If it fails, a clear actionable error is shown immediately:

```
✗ Certificate rotation failed — SSH user requires a sudo password
  Fix on the Federation Hub host:
    echo "$(whoami) ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/infra-tak-nopasswd
  Or SSH as root. Then retry.
```

---

## Postfix — VPS `mydomain=0` install failure

### Problem

On some VPS configurations, `hostname -f` returns an unresolvable name or empty string. Postfix's debconf derives `mydomain` from the mailname — if that's bad, it stores `0`, and the install fails:
```
meter mydomain: bad parameter value: 0
dpkg: error processing package postfix (--configure)
```

### Fix

The Postfix install step now:
1. Prefers the saved FQDN from infra-TAK settings (most reliable on VPS)
2. Falls back to `hostname -f` then plain `hostname`
3. Guards against `0` or empty result
4. If the initial install still fails, auto-recovers via `postconf -e myhostname/mydomain` + `dpkg --configure postfix`

---

## TAK Server — Universal channel support for manually-issued certs

Added `x509useGroupCacheRequiresExtKeyUsage="false"` to the generated `<auth>` block in `CoreConfig.xml`. This enables TAK channels for certs issued via `makeCert.sh` (Skydio drones, custom integrations) without requiring re-enrollment. Certs created via QR code enrollment already have the correct EKU and are unaffected.

**Context:** Channels normally require an Extended Key Usage extension only added during enrollment. Setting this attribute to `false` makes TAK Server grant channels to all valid certs regardless of EKU — the right default when supporting non-enrollment devices.

**How to apply on existing servers:** In the infra-TAK console, click **"Connect TAK Server to LDAP"** on the main dashboard. This re-patches `CoreConfig.xml` with the full `<auth>` block (including the new attribute) and restarts TAK Server. Existing certs and users are not affected — only the auth block is rewritten.

---

## Upgrade notes

1. **Update Now** (or `git pull` + `sudo systemctl restart takwerx-console`).
2. **Node-RED:** `bash nodered/deploy.sh` on the server — sync step will update `Build subscribe URL` in all existing engine tabs automatically. No tab deletion needed.
3. **DataSync feeds:** existing feeds targeting `MISSION_SUBSCRIBER` missions continue to work unchanged. To switch a feed to `MISSION_READONLY_SUBSCRIBER`: change the `defaultRole` in TAK Portal, then restart Node-RED (clears `_subscribed` cache so elevation fires on next cold start).
4. **Multi-flow shared missions:** open each flow in the Configurator, uncheck **"Strict mission ownership"**, save.
5. **FedHub (manual installs):** if cert rotation was failing, run the sudoers fix on the FedHub host first.

---

## Pre-release testing (maintainers)

Follow **[docs/TESTING-UPDATES.md](TESTING-UPDATES.md)** before tagging. Node-RED smoke tests: **[docs/TESTING-NODERED-DEPLOYS.md](TESTING-NODERED-DEPLOYS.md)**.

**Release to `main`:** **[docs/COMMANDS.md](COMMANDS.md)** → Merge dev → main — update `docs/RELEASE-v0.6.7-alpha.md`, VERSION, tag.

---

## Related

- [RELEASE-v0.6.6-alpha.md](RELEASE-v0.6.6-alpha.md) — Guard Dog disk I/O + Node-RED deploy safety.
- [GIS-TAK-DATASYNC-HANDOFF.md](GIS-TAK-DATASYNC-HANDOFF.md) — full DataSync architecture, role behavior, and mission design patterns.
- [HANDOFF-LDAP-AUTHENTIK.md](HANDOFF-LDAP-AUTHENTIK.md) — TAK Server auth paths + `x509useGroupCacheRequiresExtKeyUsage` context.
