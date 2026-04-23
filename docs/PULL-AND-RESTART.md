# Pull and restart on VPS

**Broken version / Update Now / git errors:** use one place — [README → Universal recovery (SSH)](../README.md#universal-recovery-ssh) (copy-paste block). This doc adds **dev**/**main**-by-branch steps and **shallow-clone** fixes.

Run each command separately (one line at a time). Do not combine commands.

## Find the correct directory first

The service might not run from `/root/infra-TAK`. **Always check first:**

```bash
grep WorkingDirectory /etc/systemd/system/takwerx-console.service
```

Use whatever path that returns. Example output:
```
WorkingDirectory=/root/infra-TAK/infra-TAK
```

## Pull latest dev and restart

```bash
cd $(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service)
git fetch origin dev
git checkout -B dev origin/dev
sudo systemctl restart takwerx-console
```

## After pulling dev: redeploy Node-RED (Configurator / new flow tabs)

Changes under `nodered/` (new tabs such as **PulsePoint**, `build-flows.js`, `configurator.html`) only affect the running **Docker** stack after you redeploy. Use the **same repo directory** as above:

```bash
cd $(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service)
bash nodered/deploy.sh
```

What **`nodered/deploy.sh`** does: runs **`git pull`** on your current branch (unless you pass **`--no-pull`**), runs **`build-flows.js`** inside the **`nodered`** container, copies **`flows.json`** and static assets, tries to preserve TLS/TCP settings from the running container, then **`docker restart nodered`**.

If you **just** finished a manual `git fetch` / `git checkout` and do not want another pull:

```bash
cd $(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service)
bash nodered/deploy.sh --no-pull
```

Then:

1. Open the **Node-RED** editor → click **Deploy** once if new tabs appear (TLS/TCP checklist: **[NODERED-DEPLOY.md](NODERED-DEPLOY.md)**).
2. Open the **Configurator** (default: **`http://<host>:1880/configurator`**, or your Authentik/Caddy URL) to test feeds. If something is missing after a restart, **TAK Settings** → **Save** can restore flow context (see deploy script header comments).

**No `grep -oP`?** `cd` to the path from `grep WorkingDirectory /etc/systemd/system/takwerx-console.service`, then run `bash nodered/deploy.sh`.

**Maintainers:** full deploy smoke steps — **[TESTING-NODERED-DEPLOYS.md](TESTING-NODERED-DEPLOYS.md)**.

## Pull latest main (stable) and restart

Prefer fetching **canonical** **`main`** (official repo). **`git fetch origin main`** only works if **`git remote -v`** points at **`takwerx/infra-TAK`**; otherwise **`origin/main`** can stay years out of date.

```bash
cd $(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service)
git fetch https://github.com/takwerx/infra-TAK.git main
git checkout --force -B main FETCH_HEAD
grep '^VERSION' app.py
sudo systemctl restart takwerx-console
```

If **`origin`** is already correct, **`git fetch origin main`** and **`git checkout -B main origin/main`** is equivalent.

## Shallow clone fix (one-time)

If `git fetch` fails with `'origin/dev' is not a commit`, run this once:

```bash
git remote set-branches origin '*'
```

Then retry the pull commands above. This only happens on VPS installs that used `--depth 1`.

## `checkout` / `git pull` blocked: local changes to `nodered/flows.json`

After **`nodered/deploy.sh`** (or copying flows from the container), **`git status`** may show **`nodered/flows.json`** modified. **`git checkout dev`** or **`git pull`** then aborts with *would be overwritten*.

If you do **not** need to keep those working-tree edits (you will rebuild flows on deploy anyway):

```bash
cd $(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service)
git restore nodered/flows.json
```

Older Git: **`git checkout -- nodered/flows.json`**. Then retry **Pull latest dev** (or **`git pull`**) and **`bash nodered/deploy.sh`**.

To keep a copy first: **`cp nodered/flows.json /tmp/flows.json.bak`** then restore as above.

## Upgrading to v0.2.0+

v0.2.0 switches from Flask dev server to gunicorn (production server). After pulling, run `start.sh` once to upgrade the service:

```bash
cd $(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service)
sudo ./start.sh
```

This installs gunicorn and updates the systemd service. After that, normal `git pull` + `systemctl restart` works as usual.

More: `docs/COMMANDS.md`

---

## MediaMTX web editor: FQDN won’t load after update

If you updated the MediaMTX web editor and **https://stream.&lt;your-fqdn&gt;** (or your stream subdomain) no longer loads (502, connection refused, or blank):

### Fix from infra-TAK

In infra-TAK, open **MediaMTX**, then click **🔧 Patch web editor**. This patches and restarts the web editor on the same host (or remote) that MediaMTX uses. No CLI needed.

If the Web Console still doesn't load, use **Web editor logs** on the MediaMTX page to see why the service is failing.


---

**1. Check whether the editor service is running**

```bash
systemctl status mediamtx-webeditor
```

If it’s **failed** or **inactive**:

**2. See why it failed**

```bash
journalctl -u mediamtx-webeditor -n 60 --no-pager
```

Common causes after an editor update:

- **Python error** in the new `mediamtx_config_editor.py` (e.g. missing import or syntax change). The last lines of the log usually show the traceback.
- **Port already in use** — something else bound to 5080.

**3. Try starting it by hand (to see errors)**

```bash
cd /opt/mediamtx-webeditor
PORT=5080 python3 mediamtx_config_editor.py
```

If it exits immediately, the traceback in the terminal is the cause. Fix the script or dependencies, then:

```bash
sudo systemctl start mediamtx-webeditor
```

**4. If the service is running but FQDN still doesn’t load**

- **Regenerate Caddy and reload:** In infra-TAK, open **Caddy**, re-save your domain (or click Save so the Caddyfile is rewritten and Caddy reloads). Then try https://stream.&lt;fqdn&gt; again.
- **Check Caddy:** `systemctl status caddy` and `journalctl -u caddy -n 30 --no-pager` for proxy errors.
- **Check direct access:** On the server, `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5080/` should return 200 if the editor is up. If that works but the FQDN doesn’t, the issue is Caddy/proxy or DNS, not the editor.
