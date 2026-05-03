# Testing Node-RED / Configurator deploys

Use this when you change **`nodered/`** (flows, configurator UI, `deploy.sh`) and want to **validate on a live box** after syncing **`dev`** and running **`nodered/deploy.sh`**.

**Critical:** The Configurator page is **not** read live from `configurator.html` on disk at request time. **`build-flows.js`** embeds that file into the Node-RED **template** node inside **`flows.json`**. Until you run **`./nodered/deploy.sh`** (or otherwise deploy merged flows into the **`nodered`** container), the browser keeps showing **whatever HTML was last baked into flows** — so new buttons (e.g. KML **Fetch**) will **not** appear after only `git pull`.

This is **not** the **Update Now** pre-release protocol. That lives in **[TESTING-UPDATES.md](TESTING-UPDATES.md)** (console updater, tags, fake `VERSION`, etc.).

---

## Typical flow (SSH on the console host)

Point the shell at the infra-TAK working copy the **takwerx-console** service uses:

```bash
cd "$(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service)"
```

If **`grep -oP`** is not available, use:

```bash
cd "$(grep WorkingDirectory /etc/systemd/system/takwerx-console.service | cut -d= -f2)"
```

Sync **`dev`** and deploy **without** re-pulling git inside the script (you already have the branch you want):

```bash
git fetch origin dev
git checkout -B dev origin/dev
./nodered/deploy.sh --no-pull
```

**`--no-pull`:** Skips the **`git pull`** at the start of **`deploy.sh`**. Use when you just checked out **`origin/dev`** and do not want the script to merge remote tracking state again.

**Omit `--no-pull`** when you want the script’s default **`git pull`** from the repo root (same directory).

---

## ⛔ Never do a raw `docker cp` of flows.json

```bash
# WRONG — wipes all dynamic engine tabs (user feeds)
docker cp nodered/flows.json nodered:/data/flows.json
```

Always use `deploy.sh`. It backs up global context, merges preserved engine tabs, restores credentials, then installs. A raw copy skips all of that and destroys every feed the operator configured. Recovery requires re-Saving each config in the Configurator to rebuild the engine tabs.

## What `deploy.sh` does (high level)

- Copies **`build-flows.js`** / **`configurator.html`** into the **`nodered`** container and runs **`node build-flows.js`** to regenerate **`flows.json`** / **`template-functions.json`**.
- Merges shipped flows with **preserved** dynamic engine tabs and user flows.
- Backs up and restores Node-RED **global / flow context** so Configurator configs are not wiped.
- Stops the container, writes **`/data/flows.json`**, restores credentials, starts the container.

Details and TLS notes: **[NODERED-DEPLOY.md](NODERED-DEPLOY.md)**.

---

## Smoke tests after deploy

### 1. Container is up

```bash
docker ps --filter name=nodered
```

Expect **`nodered`** **running**. If it exits, **`docker logs nodered --tail 150`**.

### 2. Configurator UI (browser)

- Prefer the URL you normally use (often **`https://nodered.<fqdn>/configurator`** behind Authentik, or **`http://<host>:1880/configurator`** if hitting Node-RED directly).
- Hard refresh once (**Ctrl+Shift+R** / **Cmd+Shift+R**) so the template node serves fresh HTML.
- Spot-check flows you care about (e.g. **ArcGIS → Fetch service**, **KML → Fetch fields** and sample table, **Save** on a throwaway config name).

### 3. HTTP API (quick)

From the **same host** (or any machine that can reach Node-RED’s HTTP port):

```bash
curl -sS -X POST "http://127.0.0.1:1880/arcgis-tak/kml/fetch" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://atak.s3.us-west-1.amazonaws.com/FIRIS_inputs.kml"}' | head -c 800
echo
```

You should see JSON with **`"ok":true`**, **`keys`**, **`samples`** (or an **`error`** string if the URL is bad). Adjust host/port if your compose maps something other than **`127.0.0.1:1880`**.

Other useful paths (same base URL): **`GET /arcgis-tak/config/load`**, **`POST /arcgis-tak/arcgis/service`** with a feature service URL.

### 4. Node-RED editor

Open the editor → click **Deploy** if you changed nodes manually (normally not required for script-only deploys). Watch the **debug** sidebar for errors on the next inject/poll cycle.

### 5. Saved configs still present

Configurator **saved config** list should still show existing entries (context restored). If the list is empty after a bad manual **`docker cp`**, see recovery notes in **[NODERED-DEPLOY.md](NODERED-DEPLOY.md)**.

---

## Regenerate flows locally (optional, dev machine)

Before committing, from repo root:

```bash
node nodered/build-flows.js
```

That refreshes **`nodered/flows.json`** and **`nodered/template-functions.json`** from **`build-flows.js`** / **`configurator.html`**.

---

## Related docs

| Doc | Use |
|-----|-----|
| [TESTING-UPDATES.md](TESTING-UPDATES.md) | **Update Now** button, tags, pre-release |
| [NODERED-DEPLOY.md](NODERED-DEPLOY.md) | TLS / TCP cheat sheet, manual vs script deploy |
| [PULL-AND-RESTART.md](PULL-AND-RESTART.md) | Shallow clone / branch fetch issues |
| [COMMANDS.md](COMMANDS.md) | Selective merge **dev → main**, tagging |
