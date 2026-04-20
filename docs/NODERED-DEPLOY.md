# Node-RED Deploy Cheat Sheet

**Validate a full `dev` deploy + smoke tests:** **[docs/TESTING-NODERED-DEPLOYS.md](TESTING-NODERED-DEPLOYS.md)** (checkout `dev`, `./nodered/deploy.sh --no-pull`, curl + browser checks).

After every `docker cp flows.json` + `docker restart nodered`, you need to reconfigure these settings in the Node-RED editor. Copy-paste from below.

Node-RED uses **two separate TLS configs** to isolate streaming from the Mission API:
- **TAK Mission API TLS** (admin cert) — for PUT/DELETE/GET on port 8443
- **TAK Stream TLS** (restricted cert) — for CoT TCP streaming on port 7001

---

## 1. TLS Config: Mission API (admin cert — 8443)

Double-click any HTTP Request node → click the pencil icon next to TLS → select **TAK Mission API TLS**.

**Certificate:**
```
/certs/admin.pem
```

**Private Key:**
```
/certs/admin.key
```

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

Then do steps 1-4 above in the Node-RED editor.
