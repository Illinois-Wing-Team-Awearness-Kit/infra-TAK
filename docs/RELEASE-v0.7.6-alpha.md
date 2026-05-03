# v0.7.6-alpha Release Notes

---

## ⚠️ Action Required: Resync LDAP to TAK Server

If you haven't already done this, **do it now.**

Go to **TAK Server page → Resync LDAP to TAK Server**.

This fixes password changes taking up to 24 hours to propagate to ATAK/iTAK devices. After Resync, new passwords take effect within 2 minutes. Applies to every existing deployment.

---

## Bug Fixes

### Service domain change not reflected in Authentik proxy provider

**Symptom:** After changing a service's canonical domain in **Caddy → Service Domains** and running **Authentik → Update Config & Reconnect**, the Authentik proxy provider's **External host** field was not updated. The outpost kept announcing the old hostname and returned HTTP 404 on `/outpost.goauthentik.io/auth/caddy` for requests arriving on the new domain. Manual fix required: go to Authentik Admin → Providers → edit External host manually.

**Root cause:** When the infra-TAK proxy provider already existed in Authentik, `Update Config & Reconnect` detected it and moved on without PATCHing `external_host` to the new canonical domain. The Node-RED and Federation Hub providers already had this PATCH step — the infra-TAK Console provider was missing it.

**Fix:** When the proxy provider already exists, it is now PATCHed with the current canonical domain and cookie domain, matching the behaviour of the Node-RED and Fed Hub providers.

**After updating:** Change your service domain in **Caddy → Service Domains → Save & Reload Caddy**, then run **Authentik → Update Config & Reconnect** once. The proxy provider will be updated automatically — no manual edit in Authentik Admin required.

| File | Change |
|------|--------|
| `app.py` | PATCH `external_host` + `cookie_domain` on existing infra-TAK Authentik proxy provider during `Update Config & Reconnect` |
