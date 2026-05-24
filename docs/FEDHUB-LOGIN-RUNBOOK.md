# Federation Hub + Authentik — login runbook

This documents what actually mattered when FedHub “worked” vs when it looked broken. Use it after fresh deploy or when login loops.

## 0) Architecture (current)

- **Caddy** terminates TLS at `https://fedhub.<fqdn>`, runs **Authentik `forward_auth`** when Authentik is installed, then **reverse_proxies** to the Fed Hub host (default HTTP **8080** when OAuth is on).
- **Two Authentik pieces** (both intentional):
  - **Proxy provider + app** (`federation-hub`) — Caddy `forward_auth`. After you log in via `tak.<fqdn>` and open Fed Hub from **Authentik → My applications**, your session is already valid at the edge — **often no Fed Hub “Keycloak” screen**.
  - **OAuth2 provider + app** (`fedhub`) — Fed Hub’s built-in OIDC for direct visits / API callbacks (`/api/oauth/login/redirect`).

## 1) What you are looking at

- FedHub’s web UI may show a **“Keycloak”** login. That is **FedHub’s OIDC UI** (vendor naming). It does **not** mean Keycloak is installed.
- Your IdP is **Authentik**. The flow should end up at an Authentik **authorize** URL after you use that UI.

## 2) One command to verify OAuth wiring (run on console VPS)

```bash
curl -skD - "https://fedhub.test8.taktical.net/api/oauth/login/auth?force=true" -o /dev/null | grep -iE '^HTTP/|^location:'
```

**Good:**

- `HTTP/2 302` (or `HTTP/1.1 302`)
- `location:` contains `application/o/authorize`
- `redirect_uri=` matches what FedHub has in `federation-hub-ui.yml` (usually `https://fedhub.<fqdn>/api/oauth/login/redirect`)

**Bad:**

- `4xx` / no `Location`
- `redirect_uri` in the Location header does **not** match FedHub config or Authentik provider redirect URIs → fix Authentik OAuth2 provider **redirect_uris** and FedHub `keycloak*RedirectUri` lines together.

## 3) Authentik OAuth2 provider redirect URIs (FedHub app)

For public HTTPS on 443, **strict** Authentik matching should include (code now sets these on create/patch):

- `https://fedhub.<fqdn>/api/oauth/login/redirect`
- `https://fedhub.<fqdn>:443/api/oauth/login/redirect`
- Legacy: `https://fedhub.<fqdn>/login/redirect` and `:443` variant (older Fed Hub builds)

(Older wrong value was `/login/redirect` only without `/api/oauth/` — that breaks the callback.)

## 4) FedHub `federation-hub-ui.yml` essentials (remote host)

Typical required keys when using Authentik as OIDC:

- `allowOauth: true`
- `keycloakServerName: https://tak.<fqdn>` (or your Authentik public host — must match cert you pin)
- `keycloakTlsCertFile: /opt/tak/certs/keycloak.der` (DER of **that** host’s TLS cert)
- `keycloakClientId` / `keycloakSecret` from Authentik OAuth2 provider
- `keycloakRedirectUri` and `keycloakrRedirectUri` → `https://fedhub.<fqdn>/api/oauth/login/redirect`
- `keycloakConfigurationEndpoint` → `https://tak.<fqdn>/application/o/fedhub/.well-known/openid-configuration` (slug must match provider slug, usually `fedhub`)

**YAML gotcha:** Before appending lines with `tee -a`, ensure the file ends with a newline or keys can concatenate on one line and Spring will fail to parse YAML.

**Directory gotcha:** `/opt/tak/certs` must exist before writing `keycloak.der` (fresh VPS often has no `/opt/tak/certs`).

## 5) Caddy upstream port

- `web_ui_port` in console settings must match a port FedHub is **actually listening on** (`ss -tlnp` on FedHub host).
- Changing upstream without confirming listeners causes **502**.

### 502 Bad Gateway (Fed Hub) — from prior debugging

A **502** means Caddy got the browser request but **could not get a good response from the Fed Hub upstream** (or, rarely, the `forward_auth` subrequest failed in a way that surfaces as 502). It is **not** “Authentik is wrong” by itself.

**On console VPS (where Caddy runs):**

```bash
grep -A 25 "fedhub." /etc/caddy/Caddyfile
curl -sS -o /dev/null -w '%{http_code}\n' --connect-timeout 5 http://FEDHUB_IP:8080/
journalctl -u caddy --no-pager -n 40
```

Replace `FEDHUB_IP` with the IP in the `reverse_proxy` line. You want `200` or redirect, not `000`.

**On Fed Hub VPS:**

```bash
sudo systemctl restart federation-hub
sleep 70
ss -tlnp | grep -E ':(8080|9100|8446)\b'
```

With **`allowOauth: true`**, **8080** should listen for the HTTP UI Caddy normally uses. If only **9100** is up, either wait for full startup or set console **Hub web UI port** to **9100** and ensure Caddy uses **`https://` + `tls_insecure_skip_verify`** for that upstream (9100 is TLS).

**After changing `settings.json` / Fed Hub port in UI:** regenerate Caddyfile and reload Caddy (Caddy page **Restart** in the console, or `generate_caddyfile(load_settings())` + `systemctl reload caddy`).

## 6) FedHub UI `:0` / `ERR_UNSAFE_PORT` (browser)

**Symptoms (DevTools console):** `net::ERR_UNSAFE_PORT` on URLs like `https://fedhub.<fqdn>:0/api/federations`, `:0/api/saveFederationPolicy`, `:0/api/getKnownCaGroups`, `:0/api/addNewGroupCa`; `Failed getting workflow names`; `setActiveEditingPolicy undefined`; generic “error uploading” for CA. **This is not** PDF vs web guide order — the UI never reaches the API until the port is fixed.

**CORS noise:** You may also see XHR to `https://fedhub.../api/getBrokerMetrics` **redirect** to `https://tak.../application/o/authorize/...` and then “blocked by CORS” — that is often a **secondary** effect when the app is half-broken or unauthenticated for API calls; fix `:0` first, then re-test after a normal Authentik session on `fedhub.<fqdn>`.

If the SPA calls `https://fedhub.<fqdn>:0/api/...`, set localStorage once on `https://fedhub.<fqdn>/login` (or any `fedhub.<fqdn>` page — DevTools console), then reload:

```js
localStorage.clear();
localStorage.setItem("api_port", "443");
localStorage.setItem("configuration", JSON.stringify({
  roger_federation: { server: { protocol: "https:", name: "fedhub.<fqdn>", port: 443, basePath: "/api/" } }
}));
location.reload();
```

Replace `fedhub.<fqdn>` with your real hostname.

## 7) Claim test mode vs admin group mode

**Stable test (narrow):**

- `keycloakClaimName: preferred_username`
- `keycloakAdminClaimValue: webadmin`

**Production (group-based):**

- `keycloakClaimName: groups`
- `keycloakAdminClaimValue: authentik Admins`

Switch only after OIDC redirect and session are proven with the test mode.

## 8) Authentik apps

- **Proxy** app (slug `federation-hub`) — used by Caddy `forward_auth` on `fedhub.<fqdn>`.
- **OAuth2** app (slug `fedhub`) — used by Fed Hub’s OIDC client and redirect URIs.

Both tiles named “Federation Hub” can appear; they serve different layers. Do not delete one without knowing which path you rely on.

## 9) Reliable user habit (reduces double prompts)

Open `https://tak.<fqdn>` (or your Authentik session primer) **before** `https://fedhub.<fqdn>` so the IdP session already exists.

---

If this runbook drifts from code, update **both** — the goal is one place operators can follow without guessing.
