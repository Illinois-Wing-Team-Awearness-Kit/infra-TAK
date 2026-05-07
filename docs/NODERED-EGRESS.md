# Node-RED Egress Allowlist (optional, opt-in)

## Why this matters

Node-RED's container has access to a TAK Server admin cert (`/certs/admin.pem`/`.key` — and from Phase 1A onward, optionally `/certs/nodered.pem`). The `:ro` mount prevents tampering but **does not prevent exfiltration** — any flow with code-execution can `fs.readFileSync('/certs/admin.key')` and POST it to an external host.

The single highest-leverage runtime control against this is **egress filtering**: restrict outbound network from the Node-RED container to only the destinations it legitimately needs to reach. After that's in place, even a fully compromised Node-RED runtime cannot ship a stolen cert anywhere.

## Two approaches

| | Layer-3 iptables (`scripts/nodered-egress-firewall.sh`) | Layer-7 Squid sidecar |
|--|--|--|
| Filters by | IP address | hostname |
| Handles ArcGIS hostnames? | Only if hostnames resolve to a stable IP set | Yes — wildcard match on `*.arcgis.com` |
| Handles dynamic CDN IPs? | Poorly (rules go stale on DNS rotation) | Yes |
| Operational complexity | Low (one script + cron) | Medium (sidecar container + Node-RED config) |
| Strictest variant for fully-controlled feeds | Best | Best |
| Best for hands-off operator deployments | OK | Better |

If your Node-RED only talks to TAK Server + a known short list of services, **iptables is fine**. If you want to allow `*.arcgis.com` or other CDN-hosted services, **Squid** is the better fit.

---

## Approach 1 — iptables (strict, simple)

The shipped script `scripts/nodered-egress-firewall.sh` allows:

- TCP to host gateway on TAK Server ports `8443` and `8089` (Mission API + CoT streaming)
- DNS (`53`) and NTP (`123`)
- Anything else outbound from Node-RED is **dropped**

ArcGIS feeds work *only if* you populate the `ALLOW_DESTS` array at the top of the script with the specific feature-service hostnames you use. The script resolves them at apply-time and adds individual IP rules. This means you must re-run `apply` if the upstream rotates IPs.

### Apply

```bash
# Edit ALLOW_DESTS at the top of the script first if you need ArcGIS access:
sudo nano /home/takwerx/infra-TAK/scripts/nodered-egress-firewall.sh
# e.g. ALLOW_DESTS=("services.arcgis.com" "services3.arcgis.com" "services1.arcgis.com")

sudo bash /home/takwerx/infra-TAK/scripts/nodered-egress-firewall.sh dryrun  # preview
sudo bash /home/takwerx/infra-TAK/scripts/nodered-egress-firewall.sh apply   # install
sudo bash /home/takwerx/infra-TAK/scripts/nodered-egress-firewall.sh status  # verify
```

### Persistence across reboots

The rules apply to the container's *current* IP. If you restart Node-RED, the IP usually stays the same (Docker prefers stable IPs for named containers on a default bridge), but `apply` should be re-run on boot to be safe. Add to `/etc/cron.d/nodered-egress`:

```cron
@reboot root sleep 60 && bash /home/takwerx/infra-TAK/scripts/nodered-egress-firewall.sh apply >> /var/log/nodered-egress.log 2>&1
```

The 60-second delay gives Docker time to bring the container up first.

### Remove

```bash
sudo bash /home/takwerx/infra-TAK/scripts/nodered-egress-firewall.sh remove
```

This restores unrestricted egress.

### Trade-offs

- ArcGIS hostname rotation breaks reconcile until you re-run `apply` (manifests as 0 features fetched, ArcGIS HTTP request failures in Node-RED logs).
- Doesn't filter by URL path. If `services.arcgis.com` is in your allowlist, the container can reach *any* path on it — fine for trusted ArcGIS, but worth noting.
- Doesn't filter by Layer-7 protocol. TCP/443 to the allowed IP is open to any client behavior.

---

## Approach 2 — Squid sidecar (hostname-aware)

Run Squid as a second container on the same Docker network as Node-RED. Configure Squid with an allowlist of hostnames. Force Node-RED's outbound traffic through Squid via `HTTPS_PROXY` env var.

### Compose addition

Append this to your `~/node-red/docker-compose.yml` (alongside the existing `node-red:` service):

```yaml
services:
  squid-egress:
    image: ubuntu/squid:latest
    container_name: nodered-egress-proxy
    restart: unless-stopped
    user: "13:13"
    cap_drop:
      - ALL
    cap_add:
      - SETUID
      - SETGID
    security_opt:
      - no-new-privileges:true
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

`NO_PROXY=host.docker.internal,127.0.0.1` is critical — TAK Server traffic must NOT go through Squid (it's on the host, accessed directly).

### `squid.conf` (allowlist example)

Create `~/node-red/squid.conf`:

```squid
http_port 3128

# Allowlist — add the ArcGIS hosts you actually use
acl allowed_hosts dstdomain .arcgis.com
acl allowed_hosts dstdomain .arcgisonline.com
acl allowed_hosts dstdomain .esri.com
# Add more as needed

http_access allow allowed_hosts
http_access deny all

# CONNECT (HTTPS) on standard ports only
acl SSL_ports port 443
acl Safe_ports port 80
acl Safe_ports port 443
http_access deny CONNECT !SSL_ports
http_access deny !Safe_ports

# No caching — we don't need it, and it complicates audit
cache deny all

# Logging — useful for verifying which hosts Node-RED is reaching
access_log /var/log/squid/access.log
```

### Apply

```bash
cd ~/node-red
docker compose up -d
docker logs nodered-egress-proxy -f  # watch Squid output
```

In Node-RED, any HTTP Request node calling an external HTTPS URL will now go through Squid. Allowed hosts pass; everything else gets `403 Forbidden` from Squid (visible in Node-RED debug as a 403 response).

### Trade-offs

- Adds a second container to the deploy (more moving parts).
- Squid sees all outbound HTTPS as `CONNECT host:443` and validates the SNI/CONNECT host. It does NOT MITM the TLS — Node-RED still does end-to-end TLS to the destination. So Squid sees the hostname but not URL paths or response bodies (which is what we want).
- HTTP_PROXY is honored by Node.js's `https.request()` only when `tunnel: false` *and* the appropriate proxy support is enabled. Verify with a test request after enabling — some Node-RED contrib nodes ignore the env var.

---

## Verification (either approach)

After enabling egress filtering, run these from inside the Node-RED container to verify it's working:

```bash
# Should succeed (TAK Server on host)
docker exec nodered curl -ksI --max-time 5 https://host.docker.internal:8443/Marti/api/version

# Should succeed if you allowlisted ArcGIS
docker exec nodered curl -sI --max-time 5 https://services.arcgis.com/

# Should FAIL (not allowlisted)
docker exec nodered curl -sI --max-time 5 https://www.google.com/

# Should FAIL (cert exfil dry-run — confirms control works)
docker exec nodered curl -sI --max-time 5 https://example.com/exfil
```

If the "should fail" requests succeed, the rules aren't applied to the container's IP correctly. Re-check `status` and the container's actual IP via `docker inspect nodered`.

---

## Decision matrix

| You want | Use |
|----------|-----|
| Strict, single-server, fully-controlled feeds, accept manual re-apply on IP rotation | iptables |
| Multiple ArcGIS hosts via `*.arcgis.com` wildcard, hands-off operator UX | Squid |
| Both layers (defense in depth) | iptables for "deny by default", Squid for hostname filtering of allowed traffic |
| Just visibility / audit (no enforcement yet) | Squid in `cache deny all` mode without `http_access deny all` — logs everything but allows all |

## Related controls (already shipped — Phase 2 baseline)

These are in place by default after the Phase 2 hardening landed; egress filtering complements them:

- Image pinned to `nodered/node-red:4.0` (no `:latest` drift)
- `cap_drop: [ALL]` + `no-new-privileges:true` + `user: 1000:1000`
- `mem_limit: 2g`
- Editor port bound to `127.0.0.1:1880` (Caddy + Authentik front it externally)
- Optional adminAuth via `~/node-red/.env` (NR_ADMIN_USER/NR_ADMIN_PASSWORD_HASH)
- Scoped cert mounts (per-file, not whole `/opt/tak/certs/files` tree) — applies on fresh deploys

Egress filtering is the missing piece that closes the cert-exfil scenario. Apply it once you've validated your feeds with the allowlist set correctly.
