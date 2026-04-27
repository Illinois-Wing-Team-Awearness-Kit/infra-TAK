# v0.7.5-alpha Release Notes

---

## ⚠️ Action Required: Resync LDAP to TAK Server

If you haven't already done this, **do it now.**

Go to **TAK Server page → Resync LDAP to TAK Server**.

This fixes password changes taking up to 24 hours to propagate to ATAK/iTAK devices. After Resync, new passwords take effect within 2 minutes. Applies to every existing deployment.

---

## Bug Fixes

### CloudTAK overlays and tiles fail with CORS error (duplicate `Access-Control-Allow-Origin`)

**Symptom:** KMZ overlays, PMTiles overlays, and any file displayed as a map layer fail to render in CloudTAK with "Website Error — Failed to fetch." Browser DevTools shows every tile request to `tiles.<domain>` as a CORS error despite the server returning HTTP 200.

**Root cause:** The generated Caddyfile included `header Access-Control-Allow-Origin *` on the `tiles.<domain>` and `video.<domain>` stanzas. Both the CloudTAK tile server (nginx) and MediaMTX already set this header themselves. Caddy added a second copy, producing `Access-Control-Allow-Origin: *, *` — two values — which browsers reject per the CORS spec.

**Fix:** Removed the three `header Access-Control-Allow-Origin *` directives from the Caddyfile template. Both backends supply the header themselves.

After updating, go to **Caddy SSL → Domains → Save & Reload Caddy** to regenerate your Caddyfile and pick up the fix.

| File | Change |
|------|--------|
| `app.py` | Removed `header Access-Control-Allow-Origin *` from `tiles.<domain>` and `video.<domain>` Caddyfile stanzas |

---

## New Feature: Service Domain Aliases

### Per-service "Alias" field in Caddy → Service Domains

Every service in the **Caddy SSL → Service Domains** editor now has an optional **Alias** column alongside the existing Domain column.

Set an alias when you are migrating a service to a new domain name and need the old URL to keep working during the transition. Caddy will issue a certificate for the alias and redirect every request from `alias.<domain>` → `canonical.<domain>` with an HTTP 301.

**Use cases:**
- Renaming a subdomain (e.g. `stream.example.com` → `mediamtx.example.com`) without breaking bookmarks or client configs
- Consolidating services from a legacy domain while users catch up
- Running A/B traffic during a staged migration

**How it works:**
1. Open **Caddy SSL** → expand **Service Domains**
2. Enter the old domain in the **Alias** column for the relevant service
3. Click **Save & Reload Caddy**
4. Caddy obtains a certificate for the alias and emits a `redir https://<canonical>{uri} permanent` stanza

| File | Change |
|------|--------|
| `app.py` | Added `Alias` column to Service Domains UI; `GET /api/caddy/domains` returns `alias` per service; `POST /api/caddy/domains` persists `{key}_domain_alias` in settings; `generate_caddyfile` emits 301-redirect stanza for each set alias |
