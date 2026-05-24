# Release v0.7.0-alpha

## What's new

### IPAWS — Timer-based KML cache (server-side pre-build)

The IPAWS KML feed is now **pre-built on a server-side timer** rather than built on every client request.

**Before (v0.6.9-alpha):** Every ATAK device polling `/ipaws/alerts.kml` triggered a live NWS API call. With 50 devices polling every 5 minutes that was 10 NWS API calls/minute and 50 separate KML builds.

**Now:** Node-RED builds the KML once per configured interval and caches it in global context. Any number of ATAK clients hitting the endpoint get the cached KML back instantly — zero NWS API calls from client requests.

**How it works:**
- A 60-second inject timer fires every minute
- `Check poll interval` function gates actual fetches: only proceeds if `cfg.pollInterval` minutes have elapsed since the last fetch
- On fetch: NWS API → zone polygon cache → KML built and stored in `ipaws_kml_cache` global context
- `GET /ipaws/alerts.kml` reads from the cache and returns instantly — no upstream call

**Configurator:** New **"NWS poll interval"** dropdown (1 / 2 / 5 / 10 / 15 / 30 min, default 1 min) in the IPAWS panel. ATAK clients can be set to any refresh interval — the server never makes more than one NWS call per configured cycle. Saved config card now shows `Poll: Xmin`.

**On startup:** Node-RED fires an immediate first build 2 seconds after deploy so the cache is populated before the first ATAK poll.

**On config save:** Resetting `ipaws_last_fetch` to 0 ensures the next timer tick triggers an immediate re-fetch with the new settings (severity, state filter, or poll interval changes take effect within 60 seconds).

---

### Deploy fix — always fetch before checkout

The `git fetch origin --tags` step is now documented as required before `git checkout -B dev origin/dev` in deployment commands. Without the fetch, the server uses a stale local tracking ref and deploys old code silently. Added note to `docs/COMMANDS.md`.

---

## Upgrade steps

Update Now handles everything automatically.

- No Configurator action required — existing activated IPAWS config carries over and defaults to 1-minute polling
- The IPAWS panel will show the new "NWS poll interval" dropdown on next page load
- Node-RED debug panel shows `IPAWS: cache updated — N alerts at <timestamp>` once per poll cycle instead of once per client request

---

## What upgraders get

| Scenario | What happens |
|---|---|
| IPAWS not activated | No change — flow is dormant |
| IPAWS activated, any number of ATAK clients | 1 NWS API call per minute (or configured interval) regardless of client count |
| Config saved in Configurator | New settings take effect within 60 seconds (next timer tick) |
| New install | IPAWS inactive by default; activate from Configurator |
