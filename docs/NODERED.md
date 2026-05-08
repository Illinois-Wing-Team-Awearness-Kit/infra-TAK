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
