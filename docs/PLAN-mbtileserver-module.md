# Planning: MBTiles Server Module

**Status:** Future / Not Started  
**Target release:** TBD (post v0.7.x-alpha)  
**Proposed module name:** `mbtileserver`

---

## What it is

[`mbtileserver`](https://github.com/consbio/mbtileserver) is a lightweight, single-binary Go tile server. Drop MBTiles or PMTiles files into a folder and it instantly serves them as:

- **XYZ tile URLs** (`/services/{tileset}/{z}/{x}/{y}.{ext}`) — consumed by ATAK/WinTAK/iTAK as a Network Map Source
- **WMTS** (Web Map Tile Service) — standard geospatial service protocol
- **TileJSON** (Mapbox format)
- A simple web viewer for preview

This fills the gap CloudTAK's tile server doesn't cover: **serving pre-rendered base maps and overlays to TAK clients**, not just the CloudTAK browser UI.

---

## Use cases

| Use case | Notes |
|----------|-------|
| Offline / disconnected ops base map | Load CONUS or state MBTiles (~2–30 GB), ATAK gets satellite or topo offline |
| Custom overlay tiles | Fire perimeters, jurisdictional boundaries, flight-risk zones — pre-rendered as tiles |
| Low-bandwidth operations | Pre-cached tiles eliminate repeat map data download over LTE |
| ATAK Network Map Source | Operators point ATAK at `https://tiles.<domain>/services/{tileset}/map` |
| WinTAK / iTAK map layers | Same tile URL works across all TAK clients |

---

## Resource requirements

| Resource | Estimate |
|----------|----------|
| CPU | Near-zero (static file reads) |
| RAM | 256 MB – 1 GB (tile cache) |
| Disk | Your MBTiles files — state ~500 MB, CONUS ~5–30 GB, world ~80 GB+ |
| Network | Light — each map pan fires 10–20 tile requests, small PNG/PBF payloads |

Can run on the same VPS as the TAK stack without meaningful impact.

---

## Architecture

```
ATAK / WinTAK / iTAK
         │
         ▼
  Caddy (tiles.<domain>)        ← new Caddy stanza: mbtileserver_tiles (or dedicated subdomain)
         │
         ▼
  mbtileserver (Docker)         ← port 8500 internal
         │
         ▼
  /data/mbtiles/*.mbtiles       ← user uploads tiles here (SFTP, scp, or future UI)
```

---

## Implementation plan

### 1. Docker Compose service

- Image: `ghcr.io/consbio/mbtileserver:latest`
- Volume mount: `~/mbtileserver/tiles:/tilesets`
- Port: `8500` (internal only, Caddy terminates TLS)
- Tile directory auto-scanned on startup — no config reload needed when files are added

### 2. infra-TAK module (`modules/mbtileserver/`)

Following existing module pattern (same as `mediamtx`, `cloudtak`, etc.):

- `install.sh` — creates dirs, writes `docker-compose.yml`, starts container
- `uninstall.sh` — stops and removes container + data option
- `detect()` — checks if container exists / running
- Status card on dashboard (container status, tile count, disk usage)

### 3. Caddy integration

- New `SERVICE_DOMAIN_DEFAULTS` entry: `'mbtileserver': 'tiles'` (note: conflicts with `cloudtak_tiles` — may need `'mbtileserver': 'mbtiles'` or let user configure it)
- If CloudTAK is also installed, default to `mbtiles.<fqdn>` to avoid collision
- Caddy stanza: simple `reverse_proxy 127.0.0.1:8500`
- CORS: mbtileserver sets its own `Access-Control-Allow-Origin: *` — **do not** add a second one in Caddy (same lesson as v0.7.5-alpha CloudTAK fix)

### 4. UI — Tile Management page

- List installed tilesets (from `/api/mbtileserver/tilesets`)
- Show tileset name, format (raster/vector), zoom range, size on disk
- Copy-paste XYZ URL and WMTS URL for each tileset (ready to paste into ATAK)
- Upload MBTiles file via browser (chunked — files can be GB)
- Delete tileset
- Link to ATAK Network Map Source instructions

### 5. ATAK instructions (docs + UI tooltip)

Point ATAK to: `https://mbtiles.<domain>/services/{tileset}/map`  
Settings → Maps → Network Map Source → enter that URL.

---

## Open questions

- **Subdomain collision:** `tiles.` is taken by CloudTAK. Default to `mbtiles.` unless CloudTAK isn't installed?
- **Upload size:** Browser upload for multi-GB files needs streaming/chunked handler in `app.py`. Alternative: document `scp` / SFTP into `~/mbtileserver/tiles/` and just show the file list in UI.
- **Authentik protection:** Should the tile URL require auth? For field ops the URL is often unauthenticated (ATAK doesn't do OAuth). Could optionally add IP-allowlist via Caddy.
- **PMTiles support:** `mbtileserver` v0.10+ supports PMTiles natively. Worth testing — smaller files, range-request serving, no server needed for static hosting.
- **Vector tile fonts/glyphs:** If vector tiles need fonts for labels, `mbtileserver` can serve a fonts directory. May need a separate glyphs volume.

---

## Files to create/modify

| File | Change |
|------|--------|
| `modules/mbtileserver/install.sh` | New — Docker Compose deploy |
| `modules/mbtileserver/uninstall.sh` | New |
| `app.py` | Add detect, status card, Tile Management page, Caddy stanza |
| `docs/COMMANDS.md` | Add mbtileserver to selective merge list when shipped |
| `docs/RELEASE-vX.X.X-alpha.md` | Release notes when shipped |

---

## Reference

- Upstream repo: https://github.com/consbio/mbtileserver
- MBTiles spec: https://github.com/mapbox/mbtiles-spec
- ATAK Network Map Source docs: TAK.gov product documentation
- Alternatives considered: TileServer GL (heavier, Node.js), Martin (PostGIS-first), PMTiles static hosting (no server needed but requires CDN or Caddy range-request passthrough)
