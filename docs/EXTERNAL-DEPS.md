# External Dependencies — infra-TAK

This doc covers the external repos and libraries infra-TAK depends on, what we use them for, and the non-obvious integration patterns that aren't in their own docs. **Don't copy their code here — link out.** Their code changes; our usage patterns are what's worth capturing.

---

## TAK Server (Tak.gov / PAR)

**Repo:** [tak.gov](https://tak.gov) — requires account  
**Version tracking:** TAK Server ships as a `.deb`. infra-TAK uploads and installs it.

### What we use
- REST API (`/Marti/api/`) for missions, subscriptions, roles, certificates
- TCP CoT streaming (port 8089, TLS)
- Certificate enrollment (8446 with `clientAuth="false"`, 8089 with mTLS)
- `CoreConfig.xml` for LDAP auth, federation, streaming config
- `UserAuthenticationFile.xml` for flat-file fallback
- `makeCert.sh` / `makeRootCa.sh` for certificate generation
- `certmod` for flat-file group assignment

### Non-obvious patterns
- `PUT /Marti/api/missions/{name}/subscription?uid=X` assigns `defaultRole` to the subscriber **regardless of who they are** — even admin. Always follow with `PUT /role?username=X&role=MISSION_OWNER` if you need write access to a read-only mission.
- `PUT /Marti/api/missions/{name}/contents` requires the UID to exist in TAK Server's CoT cache first (streamed via TCP). Stream CoT → wait 5-30s → PUT UID.
- `PUT /missions/{name}/role` requires the `group` parameter to match the mission's existing group or TAK returns `FORBIDDEN: Illegal attempt to change Mission groups!`.
- `x509useGroupCacheRequiresExtKeyUsage="false"` in the `<auth>` block enables channels for certs issued via `makeCert.sh` (which lack the EKU extension added during enrollment).
- The LDAP `updateinterval` in CoreConfig controls how often TAK Server queries Authentik LDAP for group membership (we use 30s).

### OpenAPI spec
`docs/TAK_Server_OpenAPI_v0.json` — local copy of the mission/subscription API spec.

---

## Authentik (goauthentik.io)

**Repo:** [github.com/goauthentik/authentik](https://github.com/goauthentik/authentik)  
**Docs:** [goauthentik.io/docs](https://goauthentik.io/docs)

### What we use
- LDAP outpost (port 389) — TAK Server binds to this for user auth and group lookup
- OIDC provider — CloudTAK, FedHub SSO
- REST API (`/api/v3/`) — create users, groups, set passwords, manage tokens
- Docker Compose deployment

### Non-obvious patterns
- LDAP `bind_mode: cached` and `search_mode: cached` — Authentik caches the LDAP directory in memory. Cache refresh interval is configurable (we use default). This means group changes in Authentik take up to the cache TTL to propagate to TAK Server.
- Service account `adm_ldapservice` must exist in Authentik and have the LDAP token saved in infra-TAK settings — used by TAK Server to bind and query.
- `webadmin` user in TAK Server flat-file shadows any Authentik LDAP user with the same name. Don't create a user named `webadmin` in Authentik.
- After `docker restart authentik-worker`, Authentik must re-sync blueprints before LDAP queries work. infra-TAK's startup sequence accounts for this.

**Deep-dive:** [HANDOFF-LDAP-AUTHENTIK.md](HANDOFF-LDAP-AUTHENTIK.md)

---

## CloudTAK (dfpc-coe / Colorado OEDIT)

**Repo:** [github.com/dfpc-coe/CloudTAK](https://github.com/dfpc-coe/CloudTAK)  
**Node SDK:** [github.com/dfpc-coe/node-tak](https://github.com/dfpc-coe/node-tak)  
**Contact:** Colorado OEDIT / dfpc-coe team

### What we use
- CloudTAK as browser-based TAK client (deployed via Docker)
- `node-tak` SDK patterns for understanding TAK Server API auth (`APIAuthCertificate`)
- CoT data model reference

### Non-obvious patterns
- CloudTAK requires a valid TAK Server cert in the browser's trust store or cert-based login fails.
- `TAKAPI.init()` + `APIAuthCertificate` is the correct pattern for cert-based TAK Server API calls — useful reference for understanding how infra-TAK's Node-RED flows authenticate.

---

## Node-RED (OpenJS Foundation)

**Repo:** [github.com/node-red/node-red](https://github.com/node-red/node-red)  
**Docs:** [nodered.org/docs](https://nodered.org/docs)

### What we use
- Flow-based automation behind Authentik forward auth
- HTTP request nodes for TAK Server mission API
- TCP out nodes for CoT streaming
- Function nodes for data transformation
- Context store (global/flow) for Configurator state

### Non-obvious patterns
- Function nodes cannot use `require()` by default. Use the `libs` property in the node definition to import modules: `libs: [{ var: '_nodeHttps', module: 'https' }]`.
- The `_subscribed` global context cache gates subscribe calls — clear with `DELETE http://localhost:1880/context/global/_subscribed` to force re-subscribe.
- Flow context (`_featureHashes`) persists the reconcile hash cache. Key: `_featureHashes` (or `_featureHashes_{layerPrefix}` for multi-layer). Clear with `DELETE http://localhost:1880/context/flow/{tabId}/_featureHashes`.
- `deploy.sh` stops the container before writing `flows.json` — never `docker cp` a flows.json into a running container (risk of persisting empty global context).

**Deep-dive:** [GIS-TAK-DATASYNC-HANDOFF.md](GIS-TAK-DATASYNC-HANDOFF.md), [NODERED-DEPLOY.md](NODERED-DEPLOY.md)

---

## PyTAK (Greg Albrecht / ampledata)

**Repo:** [github.com/ampledata/pytak](https://github.com/ampledata/pytak)  
**PyPI:** `pip install pytak`

### What we use
- Reference for CoT XML structure and field definitions
- CoT event types (`a-f-G-U-C`, `b-m-p-w`, etc.)
- TAK protocol constants

### Non-obvious patterns
- PyTAK's `COT_STALE` default is 300s. TAK Server displays items as stale after this. infra-TAK Node-RED flows set stale based on the feed's TTL config.
- CoT `uid` must be globally unique and stable across polls for DataSync reconciliation to work correctly.

---

## Caddy (caddyserver.com)

**Repo:** [github.com/caddyserver/caddy](https://github.com/caddyserver/caddy)  
**Docs:** [caddyserver.com/docs](https://caddyserver.com/docs)

### What we use
- Reverse proxy for all web services (TAK Portal, Authentik, CloudTAK, Node-RED, MediaMTX editor)
- Automatic Let's Encrypt TLS
- Forward auth to Authentik for protected services

### Non-obvious patterns
- After infra-TAK rewrites the Caddyfile, run `systemctl reload caddy` (not restart) to apply without downtime.
- Forward auth directive order matters — `forward_auth` must come before `reverse_proxy` in the same site block.

---

## MediaMTX (bluenviron)

**Repo:** [github.com/bluenviron/mediamtx](https://github.com/bluenviron/mediamtx)

### What we use
- RTSP/HLS/WebRTC video streaming for TAK video feeds
- Web editor for stream path configuration

### Non-obvious patterns
- After infra-TAK patches the web editor, `systemctl restart mediamtx-webeditor` is needed (not just MediaMTX itself).
- KU-band satellite link simulation: [KU-BAND-SIMULATOR.md](KU-BAND-SIMULATOR.md)

---

## TAK Federation Hub (TAK.gov / PAR)

**Docs:** [tak.gov](https://tak.gov) — requires account

### What we use
- Federation between TAK Server instances
- infra-TAK manages cert generation, YAML config, and SSO via Authentik OIDC

### Non-obvious patterns
- FedHub SSH operations require **passwordless sudo** on the target host. Configure with: `echo "$(whoami) ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/infra-tak-nopasswd`
- `truststore-{INT_CA_NAME}` in `federation-hub-ui.yml` must match the intermediate CA name used during cert generation exactly.

**Deep-dive:** [FED-HUB.md](FED-HUB.md), [FEDHUB-LOGIN-RUNBOOK.md](FEDHUB-LOGIN-RUNBOOK.md)
