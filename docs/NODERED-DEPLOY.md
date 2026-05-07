# Node-RED Deploy Cheat Sheet

**Validate a full `dev` deploy + smoke tests:** **[docs/TESTING-NODERED-DEPLOYS.md](TESTING-NODERED-DEPLOYS.md)** (checkout `dev`, `./nodered/deploy.sh --no-pull`, curl + browser checks).

After every `docker cp flows.json` + `docker restart nodered`, you need to reconfigure these settings in the Node-RED editor. Copy-paste from below.

Node-RED uses **two separate TLS configs** to isolate streaming from the Mission API:
- **TAK Mission API TLS** — for PUT/DELETE/GET on port 8443. Cert depends on Phase 1A migration state (see below).
- **TAK Stream TLS** (restricted cert) — for CoT TCP streaming on port 7001

**Cert priority for Mission API** (set automatically by `nodered/deploy.sh`):
1. `/certs/nodered.pem` + `/certs/nodered.key` — preferred. Used when Phase 1A flat-file migration is complete (see `docs/SPIKE-flatfile-nodered.md`). Least-privilege; nodered owns the missions it creates.
2. `/certs/admin.pem` + `/certs/admin.key` — fallback. Pre-Phase-1A behavior. Used on installs where the spike has not been run, or that decided to stay on admin.

If you previously had `/certs/admin.pem` configured and have just completed Phase 1A, restart Node-RED to pick up the new cert: `docker restart nodered`. The deploy script will auto-fill `nodered.pem` on the next deploy if the field is empty.

---

## 1. TLS Config: Mission API (8443)

Double-click any HTTP Request node → click the pencil icon next to TLS → select **TAK Mission API TLS**.

**Certificate** (use whichever exists in `/certs/` — `deploy.sh` prefers `nodered.pem`):
```
/certs/nodered.pem        # Phase 1A (preferred)
/certs/admin.pem          # fallback
```

**Private Key** (matched to the cert above):
```
/certs/nodered.key        # Phase 1A
/certs/admin.key          # fallback
```

**Passphrase:** `atakatak` (default — both certs use the same default password unless rotated).

Leave CA blank. Uncheck "Verify server certificate".

---

## 2. TLS Config: TCP Streaming (restricted cert — 7001)

Double-click the "CoT stream to TAK" TCP out node → click the pencil icon next to TLS → select **TAK Stream TLS**.

**Certificate:**
```
/certs/nodered-global-datasyncfeed.pem
```

**Private Key:**
```
/certs/nodered-global-datasyncfeed.key
```

Leave CA blank. Uncheck "Verify server certificate".

This cert must be in the streaming input's filter group (e.g. DATA-FEED) but **not** in a group that field users match — prevents CoT from leaking to devices that haven't subscribed to the Data Sync mission.

---

## 3. TCP Out Node (CoT stream to TAK)

Double-click the "CoT stream to TAK" tcp out node.

**Host:**
```
host.docker.internal
```

**Port:**
```
7001
```

**Type:** Connect to  
**TLS:** TAK Stream TLS (restricted cert from step 2)

---

## 4. Re-save Configurator Settings

Go to:
```
http://<server-ip>:1880/configurator
```

1. Open **TAK Settings** and hit **Save**
2. Open the feed config (e.g. CA AIR INTEL) and hit **Save**

This restores flow context that gets wiped on container restart.

---

## 5. Deploy

Hit the **Deploy** button in the Node-RED editor.

Wait ~30 seconds for the auto-poll to fire. Check the debug sidebar for:
- `SA ident sent for uid: admin`
- `CA AIR INTEL: 10 CoT events built from 10 features`
- `CA AIR INTEL reconcile: 10 streamed, 0 PUT, 0 DELETE...`

---

## Full server-side deploy (SSH)

### Recommended: use deploy script (preserves TLS + TCP config)

```bash
cd ~/infra-TAK && bash nodered/deploy.sh
```

The script does: `git pull` → `build-flows.js` → reads flow context for `creatorUid` → auto-populates TLS cert paths (`/certs/{creatorUid}.pem/.key`) → preserves any existing TLS/TCP overrides → `docker cp` → `docker restart`.

**After the first time** you configure TAK Settings in the configurator (which sets `creatorUid`), every subsequent deploy auto-configures TLS. No manual steps.

Configurator configs (flow context) survive restarts on the Docker volume.

### Manual deploy (first-time or if script fails)

```bash
cd ~/infra-TAK && git pull && node nodered/build-flows.js && docker cp nodered/flows.json nodered:/data/flows.json && docker restart nodered
```

> **WARNING:** the manual deploy bypasses the deploy script's context backup/restore and the cert auto-fill. Prefer `bash nodered/deploy.sh` whenever possible. See `.cursorrules` for the rationale (this rule is non-negotiable for production hosts).

Then do steps 1-4 above in the Node-RED editor.

---

## Phase 1A migration: switch from admin.pem to nodered.pem

Run **only after** completing the Phase 0 spike (see `docs/SPIKE-flatfile-nodered.md`) and confirming T1+T3 pass. Otherwise stay on `admin.pem`.

```bash
# 1. Bootstrap the flat-file 'nodered' user + cert + restart TAK Server.
sudo bash ~/infra-TAK/scripts/bootstrap-nodered-flatfile.sh apply

# 2. Re-deploy Node-RED — deploy.sh auto-detects /certs/nodered.pem and switches tls_tak to it.
cd ~/infra-TAK && bash nodered/deploy.sh

# 3. In the Node-RED editor, open TAK Mission API TLS and confirm:
#       Certificate: /certs/nodered.pem
#       Private Key: /certs/nodered.key
#       Passphrase: atakatak
#    Hit Deploy.

# 4. In the Configurator (https://nodered.<your-fqdn>/configurator), open each existing feed,
#    set the TAK group (default: DATASYNC-FEEDS), and hit "Create / verify in TAK".
#    For NEW missions: nodered becomes the owner and no role-elevation hack is needed.
#    For EXISTING missions: ownership stays with the original creator (admin) — those missions
#    continue to work via the elevation hack which now uses /certs/nodered.pem if present.
```

To roll back: `sudo bash scripts/bootstrap-nodered-flatfile.sh remove`, then `bash nodered/deploy.sh` (will revert to `/certs/admin.pem` since `nodered.pem` is gone).

## Optional: enable Node-RED's built-in admin auth (defense in depth)

Caddy + Authentik already protect the editor for external access. If you also want Node-RED's local auth as a defense-in-depth layer (useful for protecting against host-shell or SSH port-forward access), populate `~/node-red/.env` on the deploy host:

```bash
# Generate password hash:
docker exec nodered npx --yes node-red-admin@latest hash-pw
# Enter the password; copy the resulting bcrypt hash.

# Generate credential secret:
openssl rand -hex 32

# Edit ~/node-red/.env (the file is auto-created by deploy.sh; chmod 600):
NR_ADMIN_USER=admin
NR_ADMIN_PASSWORD_HASH=$2b$08$...the-hash-from-hash-pw...
NR_CREDENTIAL_SECRET=...the-hex-from-openssl...

# Restart Node-RED to pick up the env:
cd ~/node-red && docker compose up -d
```

`NR_CREDENTIAL_SECRET` enables stable encryption of stored credentials in flows.json. Without it, Node-RED auto-generates a per-restart key, which means deployed credentials get wiped on restart. Recommended for production.
