# Production Infrastructure Schema
**infra-TAK — prod.ilwg.us**
_Last updated: 2026-05-29_

---

## Overview

Three-server split over ZeroTier private mesh network. Only Server 1 has a public IP. Servers 2 and 3 are ZeroTier-only — no public ports, all external access proxied through Caddy on Server 1.

---

## Network Topology

```
                        INTERNET
                            │
                    ┌───────▼────────┐
                    │   Server 1     │  ← Public IP (prod.ilwg.us)
                    │  infratak      │
                    │  Caddy (443)   │
                    │  TAKServer     │
                    │  TAK Portal    │
                    │  Email Relay   │
                    │  infra-TAK     │
                    │  console       │
                    └───────┬────────┘
                            │ ZeroTier mesh (172.30.x.x)
              ┌─────────────┴──────────────┐
              │                            │
    ┌─────────▼──────┐           ┌─────────▼──────┐
    │   Server 2     │           │   Server 3     │
    │  172.30.30.4   │           │  (ZT IP TBD)   │
    │  Authentik     │           │  CloudTAK      │
    │  (no public    │           │  (no public    │
    │   ports)       │           │   ports)       │
    └────────────────┘           └────────────────┘
```

---

## Server 1 — TAK + Console

| Property | Value |
|----------|-------|
| Role | Primary / public-facing |
| Public IP | prod.ilwg.us |
| ZeroTier IP | 172.30.36.217 |
| OS | Ubuntu 22.04 / 24.04 |
| Console | https://infratak.prod.ilwg.us (port 5001 internal) |
| SSL | Let's Encrypt via Caddy |

### Services

| Service | Type | Listen | Notes |
|---------|------|--------|-------|
| Caddy | Native binary | 0.0.0.0:80, 0.0.0.0:443 | Reverse proxy + Let's Encrypt |
| TAK Server | systemd | 8089/tcp, 8443/tcp, 8446/tcp | Core MARTI |
| TAK Portal | Docker | 127.0.0.1 only | Caddy proxies externally |
| infra-TAK console | systemd (gunicorn) | 0.0.0.0:5001 | Management UI |
| Email Relay | Docker | 127.0.0.1 only | SMTP outbound |
| Guard Dog | systemd timers | — | Health monitoring |

### Public Ports (firewall open)

| Port | Protocol | Service |
|------|----------|---------|
| 80 | TCP | Caddy (HTTP→HTTPS redirect) |
| 443 | TCP | Caddy (HTTPS — all web traffic) |
| 8089 | TCP | TAKServer CoT/federation |
| 8443 | TCP | TAKServer WebGUI + cert auth |
| 8446 | TCP | TAKServer cert enrollment |
| 5001 | TCP | infra-TAK console (backdoor, pre-Caddy) |

### Internal-only Ports (localhost / Docker network)

| Port | Service |
|------|---------|
| 9090 | Authentik API (accessed via ZeroTier to Server 2) |
| 389 | LDAP outpost (on Server 2, reached via ZeroTier) |

---

## Server 2 — Authentik (Identity Provider)

| Property | Value |
|----------|-------|
| Role | Authentication / SSO / LDAP |
| Public IP | None |
| ZeroTier IP | 172.30.30.4 |
| OS | Ubuntu 22.04 / 24.04 |
| Managed by | Server 1 console via SSH over ZeroTier |

### Services

| Container | Port (internal) | Notes |
|-----------|----------------|-------|
| authentik-server-1 | 9000, 9443 | Main Authentik server |
| authentik-worker-1 | — | Background tasks / blueprints |
| authentik-postgresql-1 | 5432 | Authentik database |
| authentik-redis-1 | 6379 | Cache / session store |
| authentik-ldap-1 | 389, 6636 (LDAPS) | LDAP outpost for TAK Server |

### No public ports open on Server 2
All access is either:
- Server 1 → Server 2 ZeroTier IP:9090 (console API calls, Caddy forward-auth)
- Server 1 → Server 2 ZeroTier IP:389 (TAKServer LDAP auth)
- Admin browser → `tak.prod.ilwg.us` → Caddy → Server 2:9090 (proxied)

### LDAP Configuration

| Setting | Value |
|---------|-------|
| Base DN | DC=takldap |
| Service Account | adm_ldapservice |
| LDAP Port | 389 (Docker outpost) |
| Upstream from TAKServer | 172.30.30.4:389 |

---

## Server 3 — CloudTAK (Browser TAK Client)

| Property | Value |
|----------|-------|
| Role | Browser-based TAK client |
| Public IP | None |
| ZeroTier IP | TBD (pending deployment) |
| OS | Ubuntu 22.04 / 24.04 |
| Managed by | Server 1 console via SSH over ZeroTier |

### Services

| Container | Port (internal) | Notes |
|-----------|----------------|-------|
| cloudtak-api | 5000 | CloudTAK API server |
| cloudtak-media | 9997 | Media/video service |
| cloudtak-tiles | — | Tile serving |

### TAKServer Connectivity (Server 3 → Server 1)

| Connection | Address | Notes |
|------------|---------|-------|
| CoT ingest | 172.30.36.217:8089 | Via ZeroTier |
| Marti API | 172.30.36.217:8443 | Via ZeroTier |

---

## DNS — prod.ilwg.us

All subdomains point to Server 1's public IP. Caddy handles SSL and proxies internally.

| Subdomain | FQDN | Proxies To | Purpose |
|-----------|------|------------|---------|
| infratak | infratak.prod.ilwg.us | 127.0.0.1:5001 | infra-TAK management console |
| tak | tak.prod.ilwg.us | 172.30.30.4:9090 | Authentik login / SSO portal |
| takserver | takserver.prod.ilwg.us | 127.0.0.1:8443 | TAKServer WebGUI |
| takportal | takportal.prod.ilwg.us | 127.0.0.1 (Docker) | TAK Portal user/cert management |
| map | map.prod.ilwg.us | Server 3 ZT IP | CloudTAK browser client |
| tiles.map | tiles.map.prod.ilwg.us | Server 3 ZT IP | CloudTAK tile server |
| video | video.prod.ilwg.us | Server 3 ZT IP | CloudTAK video |
| stream | stream.prod.ilwg.us | 127.0.0.1 (Docker) | MediaMTX video streams |
| nodered | nodered.prod.ilwg.us | 127.0.0.1:1880 | Node-RED data integration |

---

## Authentication Flow (Forward-Auth via Caddy)

```
User Browser
    │
    │  GET https://takportal.prod.ilwg.us
    ▼
Caddy (Server 1)
    │
    │  forward_auth 172.30.30.4:9090/outpost.goauthentik.io/auth/caddy
    ▼
Authentik (Server 2, ZeroTier)
    │
    ├─ Session valid? → Caddy proxies request to TAK Portal
    │
    └─ No session?   → 302 redirect to tak.prod.ilwg.us/if/flow/...
                            │
                            ▼
                       Browser hits tak.prod.ilwg.us
                       (Caddy → Authentik, proxied)
                            │
                            ▼
                       User logs in
                            │
                            ▼
                       Redirect back to original URL
```

---

## TAKServer LDAP Authentication

```
TAK Client (ATAK/WinTAK)
    │  cert auth on 8089/8446
    ▼
TAKServer (Server 1)
    │
    │  LDAP bind  →  172.30.30.4:389
    ▼
Authentik LDAP Outpost (Server 2)
    │  DC=takldap
    │  adm_ldapservice account
    ▼
Authentik user directory
```

---

## Guard Dog — Health Monitoring

### Server 1 (runs locally)

| Timer | Watches | Action on Failure |
|-------|---------|-------------------|
| tak8089guard.timer | TAKServer port 8089 | Restart TAKServer after 3 failures |
| takoomguard.timer | OOM kill events | Alert + log |
| takdiskguard.timer | Disk usage | Alert at threshold |
| takdbguard.timer | TAKServer DB (CotDB) | Restart/vacuum |
| taknetworkguard.timer | Network interfaces | Alert |
| takprocessguard.timer | TAKServer process | Restart |
| taktakportalguard.timer | TAK Portal container | Restart |

### Server 2 (runs locally on Authentik host)

| Timer | Watches | Action on Failure |
|-------|---------|-------------------|
| takauthentikguard.timer | Authentik HTTP (:9090) | Full stack restart after 3 failures (15min cooldown) |
| takauthentikguard.timer | LDAP outpost (:389) | Recreate LDAP container only (if HTTP healthy) |

### Server 3 (runs locally on CloudTAK host)

| Timer | Watches | Action on Failure |
|-------|---------|-------------------|
| takcloudtakguard.timer | cloudtak-api container | Restart after 3 failures (15min cooldown) |

---

## ZeroTier Network

| Device | ZeroTier Node ID | ZeroTier IP | Role |
|--------|-----------------|-------------|------|
| Server 1 | TBD | 172.30.36.217 | TAK + Console |
| Server 2 | TBD | 172.30.30.4 | Authentik |
| Server 3 | TBD | TBD | CloudTAK |

Network ID: _(fill in from ZeroTier Central)_

---

## Deployment Order

1. **Server 1** — `sudo ./start.sh` → configure FQDN in Caddy module → TAK Server → TAK Portal
2. **Server 2** — `sudo ./start.sh --role authentik` → join ZeroTier → approve in ZeroTier Central → deploy Authentik from Server 1 console → connect TAK Server to LDAP
3. **Server 3** — `sudo ./start.sh --role cloudtak` → join ZeroTier → approve in ZeroTier Central → deploy CloudTAK from Server 1 console

---

## Secrets & Credentials Location

| Secret | Location | Notes |
|--------|----------|-------|
| infra-TAK admin password | Server 1: `~/.config/auth.json` | Hashed (Werkzeug) |
| infra-TAK settings | Server 1: `~/.config/settings.json` | FQDN, IPs, module config |
| Authentik admin password | Server 1 console → Authentik page | akadmin user |
| Authentik bootstrap token | Server 2: `~/authentik/.env` | AUTHENTIK_BOOTSTRAP_TOKEN |
| TAKServer DB password | Server 1: TAKServer config | Used by Portal + Server |
| SSH key (S1 → S2) | Server 1: `~/.ssh/infra-tak-authentik` | Auto-generated by console |
| SSH key (S1 → S3) | Server 1: `~/.ssh/infra-tak-cloudtak` | Auto-generated by console |
| Let's Encrypt certs | Server 1: Caddy auto-managed | Auto-renewed |
| TAK client certs | Server 1: TAKServer PKI | Managed via TAK Portal |
