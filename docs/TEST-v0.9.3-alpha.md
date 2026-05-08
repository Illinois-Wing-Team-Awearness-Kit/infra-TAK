# Test Plan — v0.9.3-alpha

Test against a live tak-10 deployment running `dev` branch. Run each checklist item in order; mark ✅ pass / ❌ fail / ⚠ partial.

---

## Pre-flight

```bash
# Confirm correct branch and version
cd /home/takwerx/infra-TAK && git branch && git log -1 --oneline
# Should show: dev, commit 77dc030

# Confirm console is running v0.9.3
curl -sk https://localhost:5001/api/version | python3 -m json.tool | grep version
# Should show: "0.9.3-alpha"
```

---

## Bug Fix 1 — CloudTAK Reset Config

**Pre-condition:** CloudTAK installed and connected to TAK Server.

1. Navigate to Console → CloudTAK page
2. Click **Reset Config**
3. Confirm the button returns a success toast (not an error)
4. Confirm there is NO `relation "servers" does not exist` error in the response
5. Navigate to `map.<fqdn>` — should show the CloudTAK "not configured" / bootstrap wizard screen (black screen that never loads = fail)

**Verify directly:**
```bash
docker exec cloudtak-postgis-1 psql -U docker -d gis -c "SELECT id, auth FROM server WHERE id=1;"
# auth column should be: {}
```

> **Known issue from May 7 testing:** After reset, `map.test12.taktical.net` showed `about:blank` / black spinner instead of bootstrap wizard. Caddy `curl` to the URL also timed out. Investigate whether Caddy routing to CloudTAK is broken independent of the reset feature.

---

## Bug Fix 2 — cert-metadata.sh Ownership

**Pre-condition:** TAK Server installed, `/opt/tak/certs/cert-metadata.sh` exists.

1. Check current state:
```bash
stat /opt/tak/certs/cert-metadata.sh
# Should show: Uid: tak, Gid: tak, Access: (0600/-rw-------)
```
2. Confirm source-test passes:
```bash
sudo -u tak bash -c 'cd /opt/tak/certs && . ./cert-metadata.sh && test -n "$DIR" && echo OK'
# Should print: OK
```
3. To test the auto-fix: temporarily break ownership, then trigger the post-update hook manually:
```bash
# Break it
sudo chown root:root /opt/tak/certs/cert-metadata.sh
# Trigger the fix (the post-update hook runs on Update Now, or manually restart console and watch logs)
systemctl restart takwerx-console
journalctl -u takwerx-console -f | grep cert-metadata
# Should print: Post-update: cert-metadata.sh fixed: ownership→tak:tak
# And then:     Post-update: cert-metadata.sh source-test-as-tak OK
```
4. Re-run stat check — should be back to `tak:tak 600`
5. Optional: test TAK Portal → Integrations → Download Certs and confirm it returns a file (not HTTP 400)

---

## Feature A — CPU/RAM Refresh Button

1. Navigate to Console page
2. Click **"What's using CPU/RAM?"** on any host metric card — breakdown expands
3. Confirm a **Refresh** button appears at the top of the expanded section
4. Click Refresh — data re-fetches without the panel collapsing
5. Confirm the layout: Refresh button first, then process/RAM/disk tables below it

---

## Feature B — MediaMTX RTSP Fail2ban Jail

**Pre-condition:** MediaMTX installed as systemd service (`systemctl status mediamtx`), Fail2ban installed.

1. Navigate to Console → Fail2ban page
2. Confirm a **MediaMTX RTSP Jail** card is visible (only appears when mediamtx is detected)
3. The badge should show red "Disabled" initially
4. Toggle **Enable** — jail should activate
5. Check API:
```bash
curl -sk https://localhost:5001/api/fail2ban/mediamtx/status | python3 -m json.tool
# Should show: available: true, jail_enabled: true, mediamtx_installed: true
```
6. Confirm jail config files were written:
```bash
cat /etc/fail2ban/filter.d/mediamtx-rtsp.conf
cat /etc/fail2ban/jail.d/infratak-mediamtx-rtsp.conf
fail2ban-client status mediamtx-rtsp
```
7. Change thresholds (e.g. maxretry=5) → click **Save & Reload** → confirm toast + values persist on refresh
8. Test unban: if any IPs are banned, click **Unban** on one → confirm it disappears from list
9. Toggle **Disable** → badge goes red → jail config file removed:
```bash
ls /etc/fail2ban/jail.d/infratak-mediamtx-rtsp.conf
# Should: No such file or directory
```

**Post-update auto-install test:**
```bash
# Remove the jail file if it exists, then restart console
rm -f /etc/fail2ban/jail.d/infratak-mediamtx-rtsp.conf
systemctl restart takwerx-console
journalctl -u takwerx-console -f | grep -i mediamtx
# Should print: Post-update: MediaMTX RTSP Fail2ban jail installed (10 conns/30s → 1h ban)
```

---

## Feature C — Kernel Patch Banner

1. Navigate to Console page
2. Check if a yellow banner appears above the metrics bar
3. Test the API directly:
```bash
curl -sk https://localhost:5001/api/system/kernel-patch-status | python3 -m json.tool
# Should return: { running_kernel, upgradable, patched }
```
4. If banner is showing: click the **X / dismiss** button — banner disappears
5. Reload the page — if kernel is still unpatched, banner re-appears
6. If kernel IS patched: banner should not appear
7. Check localStorage key is set after dismiss:
   - Browser dev tools → Application → Local Storage → look for `kernel_banner_dismissed_<kernel_version>`

---

## Feature D — Authentik Domain Migration

### D1 — Domain Audit Panel

1. Navigate to Console → Authentik page (Authentik must be running)
2. Scroll to the **Domain Migration Audit** card
3. Click **Run Audit**
4. Expected result if domain is clean: green "All clear — no stale domain references found"
5. Expected result if stale: table of location / field / current value with a "Sync Domain Now" button

**Verify the API directly:**
```bash
curl -sk https://localhost:5001/api/authentik/domain-audit | python3 -m json.tool
# Should return: { ok: true, fqdn, stale: [...], clean: true/false }
```

### D2 — Pre-flight Warning on Domain Change

1. Navigate to Console → Caddy SSL page
2. Change the domain input to any different value (don't need to save for real — you can cancel)
3. Click **Save & Reload**
4. A `confirm()` dialog should appear listing old domain, new domain, and warning about Authentik restart
5. Click Cancel — nothing should change

### D3 — Full Domain Sync (optional — only if you have a staging domain to test with)

1. Back up current settings first
2. Change domain, confirm the pre-flight dialog, proceed
3. Watch console logs: `journalctl -u takwerx-console -f | grep domain-sync`
4. After ~60s run the Domain Audit — should come back clean
5. Confirm Authentik login still works at the new domain

---

## Feature E — Caddy Custom Blocks Hint

1. Navigate to Console → Caddy SSL page
2. Scroll below the Caddyfile viewer
3. Confirm a hint block is visible explaining the `# --- User-added blocks (do not remove) ---` marker
4. Confirm `docs/COMMANDS.md` has the "Caddy — adding custom vhosts / rules" section:
```bash
grep -A5 "custom vhosts" /home/takwerx/infra-TAK/docs/COMMANDS.md
```

---

## Feature F — Container Hardening Audit

**Pre-condition:** Authentik running.

1. Trigger a console restart (simulates post-update run):
```bash
systemctl restart takwerx-console
journalctl -u takwerx-console -f | grep -E "CapDrop|harden|inspect"
```
2. Should see lines like:
```
Post-update: authentik-server-1 CapDrop: ALL — OK
Post-update: authentik-ldap-1 CapDrop: ALL — OK
```
3. Verify directly:
```bash
docker inspect authentik-server-1 | grep -A3 CapDrop
docker inspect authentik-ldap-1   | grep -A3 CapDrop
# Both should show: "CapDrop": ["ALL"]
```

---

## Known Issues / Deferred

| Issue | Status | Notes |
|---|---|---|
| CloudTAK `map.<fqdn>` returns `about:blank` after reset | Investigating | `curl` to the URL also timed out on May 7. Likely a Caddy routing issue pre-existing the reset — check Caddy config for `map.test12.taktical.net` block |
| MediaMTX card visibility | Needs confirm | Card should only appear when `mediamtx.service` is detected — verify on a host without MediaMTX that the card is hidden |
| Domain sync full round-trip | Not tested | D3 above requires a staging domain — skip on production |

---

## CloudTAK `about:blank` — Live Debug Session (May 7, 2026)

### What was confirmed working

```
# Port 5000 IS listening — docker-proxy bound correctly
LISTEN 0  4096  0.0.0.0:5000  0.0.0.0:*  users:(("docker-proxy",pid=4001368,fd=8))
LISTEN 0  4096     [::]:5000     [::]:*   users:(("docker-proxy",pid=4001375,fd=8))

# cloudtak-api-1 is up
3794eb1f0005  cloudtak-api  Up 7 minutes  80/tcp, 0.0.0.0:5000->5000/tcp
```

Caddy block for `map.test12.taktical.net` IS present and correct:
```
map.test12.taktical.net {
    reverse_proxy 127.0.0.1:5000 {
        flush_interval -1
        transport http {
            read_timeout 120s
            write_timeout 120s
```

### What is broken

`curl -si --connect-timeout 5 http://localhost:5000/ | head -5` — **returned nothing / timed out**

The container is bound on port 5000, Caddy is configured correctly, but `http://localhost:5000/` returns no response. CloudTAK API is listening at the network layer but not serving HTTP — likely the Node.js process inside the container is hung or crashed without the container exiting.

`curl -si https://map.test12.taktical.net/ 2>&1` — **timed out** (same root cause — Caddy can't get a response from the upstream)

### What the browser sees

`about:blank` + black spinner — Caddy connects but the upstream never responds, so the browser gets nothing to render.

### Diagnosis steps for tomorrow

```bash
# Is the Node process actually running inside the container?
docker exec cloudtak-api-1 ps aux

# Full API logs — look for crash / unhandled exception after the auth reset
docker logs cloudtak-api-1 --tail 100 2>&1

# Does the API respond at all on its internal port?
docker exec cloudtak-api-1 curl -si http://localhost:5000/ | head -10

# Try a hard restart of just the API container
cd ~/cloudtak && docker compose restart api
# Then immediately test:
curl -si --connect-timeout 5 http://localhost:5000/ | head -5
```

### Hypothesis

The auth reset (`UPDATE server SET auth = '{}'::jsonb`) ran at the DB layer. The API container was restarted (`Up 7 minutes` vs `8 hours` for other containers). It's possible the CloudTAK API process started, loaded routes (all `ok - loaded routes/...` lines were present in earlier log output), but then hung or errored when it tried to initialize its TAK Server connection against empty auth — and the hang is inside the HTTP server setup, so port 5000 is bound (docker-proxy) but the Node HTTP listener never started.

The SQL fix itself is confirmed correct. This is a CloudTAK application behavior issue on empty auth — may need the bootstrap wizard to be hit to unblock it, or the API may need the `server` row to have a valid URL even with empty auth.
