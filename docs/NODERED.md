# Node-RED — Operations Reference

> Single working reference for how Node-RED integrates with TAK Server in infra-TAK: what it does, why, how to deploy it, how to harden it, and how to operate it day-to-day.

---

## How it works — conceptual overview

### What Node-RED does in infra-TAK

Node-RED runs as a Docker container and acts as the DataSync bridge between external data feeds (ArcGIS feature services, TFR, KML, etc.) and TAK Server. Each Configurator feed maps to a TAK mission. Node-RED:

1. Polls the external feed on a schedule (interval set in Configurator)
2. Builds CoT XML events from the feed features
3. Streams CoT to TAK Server via TCP port 7001 (TLS) — this pushes data into the mission
4. Subscribes to the mission via the Mission API (`PUT /Marti/api/missions/{name}/subscription`)
5. Elevates itself to `MISSION_OWNER` on that mission (`PUT /Marti/api/missions/{name}/role`) — this is what gives Node-RED write authority to PUT/DELETE mission contents
6. Reconciles on each poll: adds new UIDs, deletes stale UIDs from the mission

The missions themselves are created by the operator in TAK Portal (or via the Configurator "Create / verify in TAK" button) — Node-RED does not create missions, it subscribes to and manages the contents of existing ones.

### Cert identity — why admin.pem

Node-RED authenticates to the TAK Server Mission API using `/certs/admin.pem`. This is the same pattern TAK Portal uses — both components authenticate with the admin cert.

**Why admin and not a scoped cert:** TAK Server 5.x has an unfixed bug where x509 certs that resolve their group membership via LDAP receive **OUT-only** direction on every group, even when the group is configured for both directions. The Mission API write path (PUT contents, DELETE contents) requires IN direction and returns 403 without it. Every LDAP-backed scoped cert (e.g. `nodered-global-datasyncfeed`, TAK Portal integration user certs) hits this bug.

The blast radius of admin.pem is the primary security concern: a compromised Node-RED flow has full TAK admin authority. The v0.9.2 container hardening (egress filtering, `cap_drop`, scoped cert mounts) addresses the runtime attack surface without requiring a cert change.

### Future: flat-file `nodered` user

A flat-file TAK Server user (defined in `UserAuthenticationFile.xml`, like `admin`) declares groups directly in XML and never touches the LDAP resolution path. If TAK Server honours `groupListIN`/`groupListOUT` correctly for flat-file users, a least-privilege `nodered` cert becomes viable — it would subscribe to missions and become owner with `ROLE_USER` authority only, reducing blast radius from "full admin" to "DataSync feeds only."

This has not been validated on production yet. When it is, the migration path is in `scripts/bootstrap-nodered-flatfile.sh`. Until then, admin.pem stays.

### Two TLS configs

Node-RED uses two separate TLS configurations to isolate concerns:

| Config name | Port | Purpose | Cert |
|---|---|---|---|
| **TAK Mission API TLS** | 8443 | PUT/DELETE/GET on Mission API | `admin.pem` (or `nodered.pem` if flat-file migration is done) |
| **TAK Stream TLS** | 7001 | CoT TCP streaming | `nodered-global-datasyncfeed.pem` (scoped, streaming-only) |

The streaming cert (`nodered-global-datasyncfeed`) only needs to be in the right group to stream CoT into the mission — it doesn't need write authority on the Mission API. Separating the two certs limits what each one can do.

---

## Deploy

### The right way — deploy script

```bash
cd ~/infra-TAK && bash nodered/deploy.sh
```

The script:
1. `git pull` — gets latest `build-flows.js` and templates
2. Runs `build-flows.js` — generates `flows.json` from Configurator context
3. Reads flow context for `creatorUid` — auto-populates TLS cert paths (`/certs/{creatorUid}.pem/.key`)
4. Backs up Node-RED global context (Configurator configs, TAK settings)
5. `docker cp flows.json nodered:/data/flows.json`
6. `docker restart nodered`
7. Restores context

**Never** `docker cp flows.json nodered:/data/flows.json` manually — it wipes dynamic context (operator's saved Configurator feeds, TAK settings). Always use `deploy.sh`. This rule is non-negotiable for production hosts (see `.cursorrules`).

### Manual deploy (first-time or if script fails)

```bash
cd ~/infra-TAK && git pull && node nodered/build-flows.js && docker cp nodered/flows.json nodered:/data/flows.json && docker restart nodered
```

> **Warning:** bypasses context backup/restore and cert auto-fill. Only use when `deploy.sh` fails. Follow up with steps 1–4 below in the Node-RED editor.

### First-time TLS config (Node-RED editor)

After a manual deploy or fresh install, configure these two TLS nodes in the Node-RED editor.

#### TLS Config: Mission API (8443)

Double-click any HTTP Request node → pencil icon next to TLS → select **TAK Mission API TLS**.

| Field | Value |
|---|---|
| Certificate | `/certs/admin.pem` (or `/certs/nodered.pem` if flat-file migration is done) |
| Private Key | `/certs/admin.key` (matched to cert above) |
| Passphrase | `atakatak` (default — change if rotated) |
| CA | leave blank |
| Verify server cert | unchecked |

#### TLS Config: TCP Streaming (7001)

Double-click the "CoT stream to TAK" TCP out node → pencil icon next to TLS → select **TAK Stream TLS**.

| Field | Value |
|---|---|
| Certificate | `/certs/nodered-global-datasyncfeed.pem` |
| Private Key | `/certs/nodered-global-datasyncfeed.key` |
| CA | leave blank |
| Verify server cert | unchecked |

#### TCP Out Node

Double-click the "CoT stream to TAK" tcp out node:

| Field | Value |
|---|---|
| Host | `host.docker.internal` |
| Port | `7001` |
| Type | Connect to |
| TLS | TAK Stream TLS |

### Re-save Configurator after restart

Any time Node-RED restarts, re-save Configurator settings to restore flow context:

1. Go to `http://<server-ip>:1880/configurator`
2. Open **TAK Settings** → **Save**
3. Open each feed config → **Save**

### Verify deploy worked

Wait ~30 seconds after restart. Check the Node-RED debug sidebar for:

```
SA ident sent for uid: admin
CA AIR INTEL: 10 CoT events built from 10 features
CA AIR INTEL reconcile: 10 streamed, 0 PUT, 0 DELETE...
```

### Console deploy (SSH, remote host)

```bash
# Mount /opt/tak/certs/files on the remote first, then:
cd ~/infra-TAK && bash nodered/deploy.sh
```

Remote deploy binds Node-RED's editor to `127.0.0.1:1880` (not `0.0.0.0`) — access via SSH tunnel.

---

## Container hardening (v0.9.2 baseline)

Applied automatically via `_auto_nodered` on next "Update Now":

| Setting | Value | Why |
|---|---|---|
| Image | `nodered/node-red:4.0` | Pinned — no `:latest` drift |
| `user` | `1000:1000` | Non-root inside container |
| `cap_drop` | `[ALL]` | No capability inheritance if flow gets container root |
| `no-new-privileges` | `true` | Belt-and-suspenders on capabilities |
| `mem_limit` | `2g` | Caps memory against runaway flows |
| `restart` | `unless-stopped` | Auto-recover on crash |
| `env_file` | `~/node-red/.env` | Scaffolds adminAuth vars (opt-in) |
| Cert mounts | Per-file, `:ro` | Only the certs Node-RED uses — not the whole `/opt/tak/certs/files` tree |
| Editor port | `127.0.0.1:1880` | Not exposed to internet — Caddy + Authentik front it |

### Optional: Node-RED built-in admin auth

Caddy + Authentik already protect the editor for external access. For defense-in-depth against SSH port-forward or host-shell access, populate `~/node-red/.env`:

```bash
# Generate password hash
docker exec nodered npx --yes node-red-admin@latest hash-pw
# Enter password, copy bcrypt hash

# Generate credential secret
openssl rand -hex 32

# Edit ~/node-red/.env (auto-created by deploy.sh, chmod 600)
NR_ADMIN_USER=admin
NR_ADMIN_PASSWORD_HASH=$2b$08$...the-hash...
NR_CREDENTIAL_SECRET=...the-hex...

# Apply
cd ~/node-red && docker compose up -d
```

`NR_CREDENTIAL_SECRET` enables stable encryption of stored credentials. Without it, Node-RED generates a per-restart key — deployed credentials get wiped on restart. Recommended for production.

---

## Ongoing operations

### Check docker.sock is NOT mounted

If `/var/run/docker.sock` is bind-mounted into Node-RED, a compromised flow has full Docker control on the host. This should never be present.

```bash
docker inspect nodered | grep -i 'docker.sock'
```

Expected output: empty. If anything appears, investigate and remove from `~/node-red/docker-compose.yml`.

Add to a recurring ops check:

```bash
docker inspect nodered 2>/dev/null \
  | grep -E 'docker\.sock|\/var\/run\/docker' \
  && echo "ALERT: Node-RED has docker.sock mounted" >&2
```

### Pin contrib package versions

After installing contrib nodes, lock exact versions in the package.json inside the container volume:

```bash
# Check what's installed
docker exec nodered cat /data/package.json

# Edit to replace ^x.y.z with x.y.z (exact pins)
# Then re-resolve
docker exec nodered sh -c 'cd /data && npm install --no-save'
docker restart nodered
```

**Before adding any new contrib from the Palette manager:**
1. Check the maintainer — is it the official `@node-red` org or a well-known author?
2. Check `npm info node-red-contrib-foo` — sudden recent download spikes on old packages are a red flag
3. Read the source repo on GitHub — does it match what it claims to do?

```bash
# After any install, audit for known vulns
docker exec nodered npm audit
```

### Project mode (git-backed flows for audit)

Enables full change history: every Deploy creates a git commit with author + diff. Combined with Authentik on the editor, every change has identity + diff.

In `~/node-red/settings.js`, add:

```js
projects: {
  enabled: true,
  workflow: {
    mode: 'manual'  // 'manual' for explicit commits, 'auto' for commit-on-deploy
  }
},
```

Restart and follow the wizard in the editor (hamburger → Projects → Create Project).

**Author attribution:** Caddy can pass the Authentik username through to Node-RED deploy commits:

```caddyfile
header_up X-Forwarded-User {http.reverse_proxy.header.X-Authentik-Username}
```

Then in `settings.js`:

```js
httpAdminMiddleware: function(req, res, next) {
  if (req.headers['x-forwarded-user']) {
    process.env.NODE_RED_DEPLOY_USER = req.headers['x-forwarded-user'];
  }
  next();
}
```

After this, `git log` in `/data/projects/<name>` shows the Authentik user as commit author per deploy.

**Trade-offs:** Project mode adds a project selection step the first time an operator opens the editor. Back up `~/node-red/data/flows.json` before enabling — it migrates on first enable.

**Rollback:** set `projects: { enabled: false }` in `settings.js` and restart.

### Resource quotas (belt-and-suspenders)

`mem_limit: 2g` is set in compose. Also enforce at the Docker daemon level in `/etc/docker/daemon.json`:

```json
{
  "default-ulimits": {
    "nofile": { "Name": "nofile", "Hard": 4096, "Soft": 1024 }
  },
  "default-shm-size": "64M"
}
```

### Centralized logging

| Source | Command | What it tells you |
|---|---|---|
| Node-RED runtime | `docker logs -f nodered` | `node.warn`, runtime errors, cert-identity tag in CoT |
| TAK Server | `/opt/tak/logs/takserver-messaging.log` | Mission API requests by `creatorUid`, group resolution |
| TAK Server | `/opt/tak/logs/takserver-api.log` | x509 cert CNs, TLS handshake events |
| Caddy (editor) | `journalctl -u caddy` | Authentik header values for editor access |
| Authentik audit | Authentik admin → Events | Login + group changes for operator identities |

Cross-correlate by:
- Timestamp window
- `<__nodered flow="..."/>` attribute inside CoT `<detail>` — ties each CoT event to a specific Configurator feed (added in v0.9.2)
- `creatorUid` query parameter on Mission API calls
- Cert CN in TAK Server TLS logs

---

## Egress filtering (optional, high-value)

Node-RED's cert mounts are `:ro` — a compromised flow can't modify certs, but it can still `fs.readFileSync('/certs/admin.key')` and POST the contents to an external host. Egress filtering closes this exfiltration path.

### Approach 1 — iptables (simple, strict)

`scripts/nodered-egress-firewall.sh` allows:
- TCP to host gateway on TAK Server ports `8443` and `7001`
- DNS (53) and NTP (123)
- Everything else from the Node-RED container: **dropped**

ArcGIS feeds work only if you populate `ALLOW_DESTS` at the top of the script with the specific hostnames you use — the script resolves them at apply-time and adds IP rules. Must re-run `apply` if upstream rotates IPs.

```bash
# Edit ALLOW_DESTS first if you need ArcGIS access
sudo nano ~/infra-TAK/scripts/nodered-egress-firewall.sh
# e.g. ALLOW_DESTS=("services.arcgis.com" "services3.arcgis.com")

sudo bash ~/infra-TAK/scripts/nodered-egress-firewall.sh dryrun  # preview
sudo bash ~/infra-TAK/scripts/nodered-egress-firewall.sh apply   # install
sudo bash ~/infra-TAK/scripts/nodered-egress-firewall.sh status  # verify
```

**Persistence across reboots** — add to `/etc/cron.d/nodered-egress`:

```cron
@reboot root sleep 60 && bash ~/infra-TAK/scripts/nodered-egress-firewall.sh apply >> /var/log/nodered-egress.log 2>&1
```

```bash
# Remove (restore unrestricted egress)
sudo bash ~/infra-TAK/scripts/nodered-egress-firewall.sh remove
```

**Trade-offs:** ArcGIS IP rotation breaks reconcile (0 features, HTTP failures in Node-RED logs) until you re-run `apply`.

### Approach 2 — Squid sidecar (hostname-aware)

Better for ArcGIS and other CDN-hosted services where IPs rotate. Add to `~/node-red/docker-compose.yml`:

```yaml
services:
  squid-egress:
    image: ubuntu/squid:latest
    container_name: nodered-egress-proxy
    restart: unless-stopped
    user: "13:13"
    cap_drop: [ALL]
    cap_add: [SETUID, SETGID]
    security_opt: [no-new-privileges:true]
    mem_limit: 256m
    volumes:
      - ./squid.conf:/etc/squid/squid.conf:ro
    networks:
      - nodered-net

  node-red:
    # ... existing keys ...
    environment:
      - HTTPS_PROXY=http://squid-egress:3128
      - HTTP_PROXY=http://squid-egress:3128
      - NO_PROXY=host.docker.internal,127.0.0.1,localhost
    networks:
      - nodered-net

networks:
  nodered-net:
    driver: bridge
```

`NO_PROXY=host.docker.internal` is critical — TAK Server traffic must not go through Squid.

Create `~/node-red/squid.conf`:

```squid
http_port 3128

acl allowed_hosts dstdomain .arcgis.com
acl allowed_hosts dstdomain .arcgisonline.com
acl allowed_hosts dstdomain .esri.com

http_access allow allowed_hosts
http_access deny all

acl SSL_ports port 443
acl Safe_ports port 80 443
http_access deny CONNECT !SSL_ports
http_access deny !Safe_ports

cache deny all
access_log /var/log/squid/access.log
```

```bash
cd ~/node-red && docker compose up -d
docker logs nodered-egress-proxy -f
```

**Trade-offs:** Squid sees `CONNECT host:443` — validates SNI hostname but does NOT MITM TLS. Some Node-RED contrib nodes ignore `HTTP_PROXY` env var — verify each one after enabling.

### Which approach to use

| Scenario | Use |
|---|---|
| Short fixed list of ArcGIS hosts, can tolerate manual re-apply on IP rotation | iptables |
| `*.arcgis.com` wildcard, hands-off operator deployment | Squid |
| Maximum defense-in-depth | Both (iptables deny-by-default + Squid hostname filtering of allowed traffic) |
| Audit/visibility only (no enforcement yet) | Squid in log-only mode (remove `http_access deny all`) |

### Verification (either approach)

```bash
# Must succeed — TAK Server on host
docker exec nodered curl -ksI --max-time 5 https://host.docker.internal:8443/Marti/api/version

# Must succeed if you allowlisted ArcGIS
docker exec nodered curl -sI --max-time 5 https://services.arcgis.com/

# Must FAIL — not allowlisted
docker exec nodered curl -sI --max-time 5 https://www.google.com/

# Must FAIL — cert exfil dry-run, confirms control works
docker exec nodered curl -sI --max-time 5 https://example.com/exfil
```

If "should fail" requests succeed, the rules aren't applied to the container's current IP — re-check `status` and the container's IP via `docker inspect nodered`.

---

## What this doc deliberately does NOT cover

- **Cert/passphrase rotation runbook** — the default `atakatak` passphrase is a known weakness if cert files leak. Rotate on operator schedule.
- **Per-feed-group certs** (e.g. `nodered-fire`, `nodered-weather`) — a single cert with the right groups is sufficient for current use. Add granularity later if your audit baseline requires per-feed identity.
- **Flat-file `nodered` user migration** — see `scripts/bootstrap-nodered-flatfile.sh` and `docs/SPIKE-flatfile-nodered.md`. Not yet validated on production.

---

## DataSync flow internals — ArcGIS → TAK engine

> Everything below is operational knowledge about how the DataSync engine works. It's required reading before touching `nodered/build-flows.js` or the Configurator.

### What the engine does

The Configurator (`GET /configurator`) is a 5-step wizard:
1. Paste ArcGIS Feature Service URL → auto-detect layers
2. Pick layer → fetch fields, sample data, geometry type
3. Configure fields — ID field, time field, source filter with distinct-value checkboxes + manual entry, auto-generated `WHERE` clause
4. Shape styling — stroke/fill color (ARGB), opacity, line thickness, line style, closed polygon, label toggle + field + centering, remarks field picker
5. Export — TTL hours, CoT type prefix, UID prefix, mission name, named save with card management

Configs are saved to Node-RED **flow context** with `localfilesystem` persistence — they survive container restarts.

The engine tab ("ArcGIS → TAK") runs a reconciliation loop every 5 minutes:

```
{feed}_sa_inject → {feed}_sa_build → {feed}_cot_to_xml → {feed}_tcp_out  (SA ident on startup + every 10min)

{feed}_inject → {feed}_load → {feed}_build_q → {feed}_http_ag → {feed}_parse
  ├→ {feed}_build_sub → {feed}_http_sub        (subscribe to mission)
  └→ {feed}_build_m → {feed}_http_m → {feed}_reconcile
       ├→ Port 0: ALL features streamed + PUT new UIDs only
       └→ Port 1: DELETE stale UIDs
```

Each Configurator feed gets its own engine tab with nodes prefixed `{feed_id}_`. TLS config (`tls_tak`) is shared globally.

**UID scheme**: `arcgis-{value_of_id_field}` — deterministic, identical every poll. Dedup by field (e.g. `mission` for fire perimeters): when `dedupField` + `timeField` are both set, only the newest feature per group key is kept — prevents stacked duplicate polygons for the same named fire.

### Reconciliation model

1. Fetch current features from ArcGIS (with `WHERE` + time filter using `DATE 'YYYY-MM-DD'` format)
2. Fetch current mission contents (`GET /Marti/api/missions/{name}`) — build set of UIDs already present
3. Diff:
   - **In ArcGIS, not in mission** → stream CoT + PUT UID
   - **In mission, not in ArcGIS** → DELETE UID
   - **In both** → re-stream CoT (keeps TAK Server cache fresh), no-op on DataSync
4. ArcGIS failure guard: if ArcGIS returns non-200, skip all deletes (prevents mass removal when feed is unreachable)

### API patterns that work

```
# Subscribe Node-RED to mission (once per cold start)
PUT /Marti/api/missions/{name}/subscription?uid={creatorUid}

# Elevate to MISSION_OWNER (5 seconds after subscribe)
PUT /Marti/api/missions/{name}/role?username={creatorUid}&clientUid={creatorUid}&role=MISSION_OWNER

# Add UID to mission (after CoT is streamed — see lesson 4)
PUT /Marti/api/missions/{name}/contents?creatorUid={user}
Body: {"uids":["<uid>"]}

# Remove UID from mission
DELETE /Marti/api/missions/{name}/contents?uid={uid}&creatorUid={user}

# Read mission contents (for reconciliation diff)
GET /Marti/api/missions/{name}
```

All Mission API calls require mTLS on port **8443**. CoT streams over TCP port **8089** (also mTLS via `tls_tak`).

### Global DataSync architecture

One TAK group (`DATASYNC-FEEDS`) acts as the global channel for all automated feeds:

| Cert / User | Group | Direction | Why |
|---|---|---|---|
| Node-RED (`admin`) | All groups | Admin — bypasses direction bug | Push data, read mission contents |
| All agency ATAK certs | `DATASYNC-FEEDS` | OUT (read) | See all feeds in Data Sync menu, read-only |

**Per-feed setup:**
1. Operator creates the DataSync mission in TAK Portal (admin is owner)
2. Mission tied to `DATASYNC-FEEDS` group, `defaultRole = MISSION_READONLY_SUBSCRIBER`
3. Node-RED subscribes automatically on first poll, then auto-elevates to `MISSION_OWNER`
4. Any user with `DATASYNC-FEEDS` OUT direction automatically sees the feed

**What makes Map Items vs Files**: `POST /Marti/sync/missionupload` (Enterprise Sync) puts data into the mission's `contents` array → renders as **Files** in ATAK. Map Items come from the `uids` array, populated only via TCP streaming + PUT UIDs. This was the critical discovery that took days to find.

### Hard-won lessons (live debugging — TAK Server 5.7)

Each of these cost hours to diagnose. Do not skip them before modifying flows.

**1. `creatorUid` must match the cert CN exactly**
The `creatorUid` in Mission API URLs must be the exact CN of the TLS cert in use. Mismatch → **403 Forbidden** on every PUT.

**2. Stream CoT via TCP _before_ the DataSync PUT (5-second delay)**
`PUT /Marti/api/missions/{name}/contents` with `{"uids":["uid"]}` returns **500** if TAK Server hasn't seen that UID yet. The CoT must be streamed first so `CotCacheHelper` registers it. The delay is set to **30 seconds** (increased from 5s) to handle large feeds (55+ polygon CoTs, many over 10KB) — 5s caused 500 errors on large polls. Self-heals on next poll, but 30s prevents the error entirely.

**3. Mission API returns empty body — use `ret: 'txt'`**
TAK Server returns `Content-Length: 0` on successful PUT/DELETE. Setting `ret: 'obj'` (parse as JSON) causes "JSON parse error" on every success. Always use `ret: 'txt'`.

**4. `paytoqs: 'body'` on http_action — recurring regression**
The `http_action` node (Mission API PUT/DELETE) MUST have `paytoqs: 'body'`. Default is `'ignore'` — the `{"uids":[...]}` payload is silently dropped and PUT registers nothing. Has regressed twice. Always verify after regenerating `flows.json`.

**5. Subscribe → MISSION_OWNER elevation (read-only missions)**
`PUT /subscription?uid=admin` assigns the mission's `defaultRole` to the subscriber — **even for admin**. If `defaultRole = MISSION_READONLY_SUBSCRIBER`, admin gets silently downgraded to read-only and all PUTs return 200 but UIDs don't stick.

Fix: 5 seconds after subscribe, call `PUT /Marti/api/missions/{name}/role?username=admin&clientUid=admin&role=MISSION_OWNER`. This overrides the defaultRole. Implemented in `eng_build_sub` function node. Confirmed working: mission created as `MISSION_READONLY_SUBSCRIBER`, admin writes successfully, ATAK field devices see data read-only.

**6. `<marti><dest>` tag — placement, case, and removal**
The tag that prevents broadcast and routes CoT to the mission:
- Must be **lowercase** `<marti>` (not `<Marti>`)
- Must be **inside** `<detail>`, before `</detail></event>`
- Mission name is case-sensitive, spaces included

Without this tag, CoT broadcasts map-wide. **However:** `<marti><dest mission="..."/>` in the CoT XML also triggers `StrictUidMissionMemebershipFilter` — if the TCP connection's sender isn't identified as a mission member, the event is silently dropped with `Illegal attempt to send mission event outside of a mission context`. The fix: **remove** the `<marti>` tag from streamed CoT and let the DataSync PUT (via HTTPS 8443) handle mission association separately.

**7. SA identification CoT required for TCP connection identity**
TAK Server associates the TCP connection with a UID only when the connecting client sends a self-identification CoT (`a-f-G-U-C`) as its first message. Without it, the connection is anonymous (`tls:XX`). Node-RED sends an SA CoT with `uid=creatorUid` on startup and every 10 minutes via `eng_sa_inject → eng_sa_build`. Required for `StreamingEndpointRewriteFilter` to recognize the connection.

**8. Custom XML serializer — bypass `node-red-contrib-tak`**
The `node-red-contrib-tak` encode node was not reliably delivering CoT to TAK Server. Replaced with a custom JSON-to-XML function node (`eng_cot_to_xml`) that builds the XML string and sends it as a `Buffer` directly to `eng_tcp_out`. This is the only approach that works reliably.

**9. TLS config: `cert`/`key` vs `certname`/`keyname`**
In Node-RED's `tls-config` node:
- `cert`, `key`, `ca` = **local file paths** (e.g. `/certs/admin.pem`). These enable "Use key and certificates from local files".
- `certname`, `keyname`, `caname` = **uploaded file display names** (labels next to the Upload button). NOT file paths.

Putting paths in `certname`/`keyname` causes the checkbox to stay unchecked and TLS silently fails.

Correct `tls_tak` definition in `build-flows.js`:
```javascript
{
  id: 'tls_tak', type: 'tls-config',
  name: 'TAK Mission API TLS',
  cert: '/certs/admin.pem', key: '/certs/admin.key', ca: '',
  certname: '', keyname: '', caname: '',
  servername: '', verifyservercert: false
}
```

**10. `flows_cred.json` and passphrase**
Deploying via `docker cp flows.json` or the Node-RED admin API does NOT update `flows_cred.json`. If credentials are wiped, the passphrase must be re-entered in the TLS config UI. `deploy.sh` handles this correctly — it backs up and restores credentials around the deploy cycle.

**11. Docker networking: Node-RED → TAK Server (host)**
TAK Server runs on the host, not in Docker. For TCP to reach port 8089:
- `docker-compose.yml` needs `extra_hosts: ["host.docker.internal:host-gateway"]`
- `eng_tcp_out` host = `host.docker.internal`, port = `8089`

**12. ArcGIS time filter: use `DATE` format, not epoch**
`WHERE poly_DateCurrent > 1730000000000` (epoch ms) returns 0 features on most ArcGIS services. Use ArcGIS SQL date format: `poly_DateCurrent >= DATE '2026-03-11'` (UTC calendar date computed from TTL).

**13. `POST /Marti/api/cot` does not exist on TAK Server 5.5+**
There is no HTTP endpoint for submitting CoT. TCP streaming on port 8089 is the only ingest path.

**14. TAK Server does not overwrite stored CoT via TCP streaming alone**
Sending new CoT with the same UID over TCP does NOT update the stored version. The only reliable update path: DELETE the UID from the mission, re-stream CoT, re-PUT. Reconciliation handles this automatically for features that change between polls.

**15. LDAP x509 group direction bug**
x509 certs that resolve groups via LDAP get **OUT-only** direction, even when the LDAP base group is configured for BOTH. This blocks Mission API writes (requires IN direction). Admin cert bypasses this because `ROLE_ADMIN` overrides group direction checks. Any future scoped cert must be a flat-file user (defined in `UserAuthenticationFile.xml`) to sidestep the LDAP resolution path entirely.

**16. Portal cert ZIP hash mismatch**
TAK Portal's "Download Integration Certs" ZIP sometimes contains stale or mismatched `.pem`/`.key` files. Always pull cert files directly from the server at `/opt/tak/certs/files/<name>.pem` / `.key` — those are the source of truth.

### Confirmed working state (2026-04-20)

The following architecture is the known-good baseline. Do not change without testing against a live TAK Server.

```
ArcGIS Feature Service
    ↓ HTTP GET every 5 min (DATE-format time filter, outSR=4326)
Node-RED: Parse → CoT JSON (callsign from labelField, ARGB color, dedup by dedupField)
    ↓
Custom XML serializer (FN_COT_TO_XML)
    ↓ No <marti><dest> tag in streamed CoT
TCP stream → TAK Server :8089 (TLS via admin.pem)
    ↓ 30-second delay for CotCacheHelper
PUT /Marti/api/missions/{name}/contents (HTTPS :8443, TLS via admin.pem)
    → registers UIDs as Map Items in the mission
DELETE /Marti/api/missions/{name}/contents for stale UIDs
```

**Mission settings:**

| Setting | Value |
|---|---|
| `defaultRole` | `MISSION_READONLY_SUBSCRIBER` |
| Integration cert | `admin` (CN=admin) |
| Cert paths | `cert: '/certs/admin.pem', key: '/certs/admin.key'` |
| Passphrase | `atakatak` |
| `creatorUid` | `admin` (set in Configurator) |

**Verified:** Map Items (not Files), no broadcast, human-readable callsigns, full reconciliation lifecycle, field users read-only, admin writes successfully.

### Multipart polygon / polyline support (v0.9.33)

ArcGIS features with multiple outer rings (e.g. wildfire main perimeter + spot-fire polygons) or multiple paths now emit one CoT event per part:

- **Outer-ring detection**: rings with signed area ≤ 0 (clockwise in Y-up geographic coordinates, per ArcGIS REST convention) are outer rings; CCW rings are interior holes and are skipped. If no rings pass the outer-ring test (non-standard winding), all rings are treated as outer rings (defensive fallback).
- **Degenerate rings skipped**: rings with fewer than 4 vertices are not emitted.
- **UID per part**: single-ring/path features keep their existing UID unchanged. Multipart features append `-r0`, `-r1`, … for polygon rings and `-p0`, `-p1`, … for polyline paths.
- **Hash per part**: multipart CoT events use a ring-level hash (`djb2(featureHash + '|r' + idx + '|' + ringGeoKey)`) so reconcile detects part-level geometry changes without re-streaming unaffected parts.
- **Callsign**: all parts of the same feature share the same callsign (from the base UID or configured label field); the UID distinguishes them in TAK.
- **Reconcile / DELETE**: if a feature loses a ring part between polls, the orphaned ring UID is DELETEd from the mission automatically.
- **Existing single-ring / single-path feeds**: behavior unchanged — no UID suffix, hash identical to pre-v0.9.33.

### Open items

- **Update detection**: currently re-streams all CoT every poll. Could hash geometry/attributes to skip unchanged features for efficiency.
- **ArcGIS token auth**: add optional token field to Configurator for secured (non-public) services.
- **`StreamingEndpointRewriteFilter` error**: cosmetic — logs `unable to find mission subscription for client CA AIR INTEL, CN=admin` but data still flows. Low priority.
- **Ghost mission package files on ATAK**: old Enterprise Sync files may persist on ATAK after switching to Map Items. May need manual clear of ATAK's local file cache.
