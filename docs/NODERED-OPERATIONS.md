# Node-RED Operations & Audit Hardening (Phase 3)

These items are **opportunistic** hardening — they don't block production use, but each closes a specific operational or audit gap. Apply them after Phases 0–2 are stable on your install.

---

## 1. Project mode + git-backed flows (audit attribution)

**Why:** by default, Node-RED's `flows.json` lives on a Docker volume with no version history. Operator A can change a flow, hit Deploy, and there's no record of what changed, who, or when. Authentik already authenticates editor access (so we know *who* opened the editor), but we don't have *what they changed* without enabling Project mode.

**What it gives you:**
- Every Deploy creates a git commit in a per-instance bare repo
- `git log` shows full change history with author + timestamp
- `git diff <sha>` shows exact node-by-node changes
- Combined with Authentik on the editor, every change has identity + diff

### Enable

In `~/node-red/settings.js`, add a `projects` block (factory function or static object — both work):

```js
projects: {
  enabled: true,
  workflow: {
    mode: 'manual'  // 'manual' for explicit commits, 'auto' for commit-on-deploy
  }
},
```

Restart Node-RED:
```bash
cd ~/node-red && docker compose up -d
```

In the editor, open the hamburger menu → Projects → Create Project. Walk through the wizard — Node-RED initializes a git repo at `/data/projects/<name>/`. Subsequent Deploys prompt for a commit message (manual mode) or auto-commit (auto mode).

### Author attribution via Authentik

Node-RED reads the deploy author from `process.env.NODE_RED_DEPLOY_USER` when set. Caddy can be configured to pass the Authentik username through. Add to your Caddyfile under the `nodered.<domain>` block:

```caddyfile
header_up X-Forwarded-User {http.reverse_proxy.header.X-Authentik-Username}
```

Then in Node-RED's settings.js, capture and forward to deploy commits:

```js
adminAuth: {
  type: 'credentials',
  // ... existing user list ...
},
exportGlobalContextKeys: false,
// Trust the username header set by Caddy (Caddy already sits behind Authentik)
httpAdminMiddleware: function(req, res, next) {
  if (req.headers['x-forwarded-user']) {
    process.env.NODE_RED_DEPLOY_USER = req.headers['x-forwarded-user'];
  }
  next();
}
```

After this, `git log` in `/data/projects/<name>` shows the Authentik user as the commit author for each deploy.

### Trade-offs

- Project mode adds a Project selection step the first time an operator opens the editor.
- Existing `flows.json` is migrated into the project on first enable — back up `~/node-red/data/flows.json` first.
- The auto-commit message in `auto` mode is generic. `manual` mode is more useful for audit but adds friction.

### Rollback

Disable in `settings.js`:
```js
projects: { enabled: false }
```
Restart. The project repo persists at `/data/projects/<name>` for retrieval if needed.

---

## 2. Pin contrib package versions

**Why:** `~/node-red/package.json` (or whatever Node-RED uses for its userDir packages) tracks operator-installed contrib nodes. By default, `npm install node-red-contrib-foo` writes a caret range like `^1.2.3`. That means future deploys could pull `1.99.99` or `1.x` patches that introduce breaking changes — or, worse, get supply-chain compromised. (Recent example: `event-stream` 2018, `ua-parser-js` 2021, `node-ipc` 2022.)

**What to do:**

After you've installed your contrib nodes (via the Palette manager or `npm`), edit `~/node-red/package.json` (the one inside the Docker volume — typically at `/var/lib/docker/volumes/node-red_node_red_data/_data/package.json`):

```bash
docker exec nodered cat /data/package.json
```

Replace any `^x.y.z` or `~x.y.z` with the exact version `x.y.z`:

```diff
  "dependencies": {
-   "node-red-contrib-tcp-client": "^1.0.5",
+   "node-red-contrib-tcp-client": "1.0.5",
-   "node-red-dashboard": "~3.6.0",
+   "node-red-dashboard": "3.6.4",
  }
```

Then have Node-RED re-resolve to the exact pins:

```bash
docker exec nodered sh -c 'cd /data && npm install --no-save'
docker restart nodered
```

### Audit before adding a new contrib

Before installing any new contrib via the Palette manager, check:

1. **Maintainer reputation:** is it the official `@node-red` org, a well-known author, or a one-off contributor? A drive-by package is the highest supply-chain risk.
2. **Recent download counts:** `npm info node-red-contrib-foo` — packages with sudden recent download spikes after long inactivity are often takeover targets.
3. **Source repo:** clicking through to GitHub, does it match what the package claims to do? Are the recent commits sane?

For high-assurance deployments, prefer auditing the contrib's source and vendoring it (copy into your own repo, install via local path) rather than pulling from npm directly.

### Lock down the npm install path

If you can avoid installing new contribs at all (i.e. lock `package.json` and run `npm install --no-save` only), enable a stricter compose setting:

```yaml
services:
  node-red:
    environment:
      - NPM_CONFIG_AUDIT=true
      - NPM_CONFIG_FUND=false
```

Then check `docker exec nodered npm audit` after each install — surfaces any known vulnerabilities in the dependency tree.

---

## 3. Verify Docker socket is NOT mounted

**Why:** if `/var/run/docker.sock` is bind-mounted into the Node-RED container, a compromised Node-RED has full Docker control on the host: spawn privileged containers, read any cert from any container's volumes, escape to the host. This is the single largest "container hygiene" mistake.

**Check:**

```bash
docker inspect nodered | grep -i 'docker.sock'
```

**Expected output:** empty. If you see anything containing `docker.sock`, the socket is mounted — investigate why and remove it from `~/node-red/docker-compose.yml`. None of infra-TAK's flows or contribs require Docker control from inside Node-RED.

### Recurring check

Add to a daily ops checklist (or wire into your monitoring):

```bash
# /root/check-nodered-hygiene.sh
docker inspect nodered 2>/dev/null \
  | grep -E 'docker\.sock|\/var\/run\/docker' \
  && echo "ALERT: Node-RED has docker.sock mounted" >&2
```

If you have centralized alerting, scrape this output.

---

## 4. Resource quotas (defense against runaway flows)

`mem_limit: 2g` is set in compose (Phase 2). For belt-and-suspenders, also enforce via Docker daemon defaults in `/etc/docker/daemon.json`:

```json
{
  "default-ulimits": {
    "nofile": { "Name": "nofile", "Hard": 4096, "Soft": 1024 }
  },
  "default-shm-size": "64M"
}
```

This puts a ceiling on file-descriptor exhaustion attacks and prevents shared-memory abuse.

---

## 5. Centralized logging integration

When the SIEM/log store is set up, ensure the following streams are captured:

| Source | Path / command | What it tells you |
|---|---|---|
| Node-RED runtime | `docker logs -f nodered` | All `node.warn` and runtime errors, including the cert-identity tag (`cert=nodered`/`cert=admin`) added in Phase 1A |
| TAK Server | `/opt/tak/logs/takserver-messaging.log` and `takserver-api.log` | Mission API requests by `creatorUid`, group resolution, x509 cert CNs |
| Caddy (editor) | `journalctl -u caddy` | Authentik header values for editor access (who opened Node-RED's editor) |
| Authentik audit | Authentik admin → Events | Login + group changes for operator identities |

Cross-correlate by:
- Timestamp window
- `<__nodered flow="...">` attribute (added in Phase 1B) — identifies which Configurator feed produced a given CoT
- `creatorUid` query parameter on Mission API calls
- Cert CN visible in TAK Server's TLS handshake logs

---

## What this doc deliberately does NOT cover

- **Cert/passphrase rotation runbook** — left to operator schedule per [the May 2026 hardening plan](../$HOME/.cursor/plans/nodered_hardening_flatfile_*.plan.md). The default `atakatak` passphrase is a known weakness if cert files leak; rotate when convenient.
- **Per-feed-group integration certs** (e.g. `nodered-fire`, `nodered-weather`). A single `nodered` flat-file user with the right groups is sufficient. Add granularity later if your audit baseline requires per-feed identity.
- **Project-mode Authentik wiring details** — the Caddy `header_up` pattern shown above is illustrative; integrate with your existing Authentik forwardAuth middleware as needed.
