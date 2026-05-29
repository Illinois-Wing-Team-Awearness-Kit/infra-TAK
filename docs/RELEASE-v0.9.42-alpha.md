# v0.9.42-alpha — TAK Video Restreamer module

**Date:** 2026-05-29
**Type:** New feature — drop-in update via Update Now.
**Status:** RELEASED to `main` 2026-05-29. Validated on test12 (SSD-Nodes, single-server).

---

## TL;DR

New Marketplace module: **TAK Video Restreamer** — a Flask + MediaMTX + FFmpeg streaming server deployed as a Docker container. Mutually exclusive with the standalone MediaMTX module (they share streaming ports). Lives behind Caddy at `stream.<FQDN>`, same subdomain as MediaMTX.

---

## What's new

### TAK Video Restreamer module (`tak_video_restreamer`)

**Source repo:** `https://github.com/raytheonbbn/tak-video-restreamer`

A full streaming server with adaptive bitrate, KLV metadata support, and recording. Ships as an alternative to the standalone MediaMTX module — operators deploy one or the other, not both.

#### Deployment

- Deployed from the **Marketplace** page.
- First deploy clones the repository and builds the Docker image (~5–10 min); subsequent deploys skip the build.
- Web UI on loopback port `3100` (port `3000` is TAK Portal — TVR deliberately avoids it).
- Caddy SSL at `stream.<FQDN>` — same subdomain as MediaMTX (safe because only one can be installed at a time).

#### Authentication

TAK Video Restreamer has its own built-in Flask login (admin / generated password). This is separate from Authentik — TVR is **not** wrapped with `forward_auth` because it has no SSO-compatible auth layer. Single login via TVR's own admin page. Password is displayed on the module page (with a "show" button) and can be changed at any time via the **Change password** form without rebuilding the container.

#### Mutual exclusivity with MediaMTX

Both modules bind the same streaming ports (RTSP 8554, RTSPS 8555, SRT 8890, RTMP 1935, HLS 8888). The console enforces this:

- In the **Marketplace**, if MediaMTX is already installed, the TAK Video Restreamer card is greyed out and blocked ("Cannot deploy — MediaMTX is already installed…") and vice versa.
- The deploy function itself performs a guard check and aborts if the conflicting module is detected.

#### Stream endpoints

| Protocol | Address |
|----------|---------|
| RTSP | `rtsp://stream.<FQDN>:8554/<stream>` |
| RTSPS | `rtsps://stream.<FQDN>:8555/<stream>` |
| SRT | `srt://stream.<FQDN>:8890?streamid=publish:<stream>` |
| RTMP | `rtmp://stream.<FQDN>:1935/<stream>` |
| HLS ABR | `https://stream.<FQDN>/hls/<stream>/master.m3u8` |

#### Console integration

- **Console card:** Shows module name, git commit SHA as version, and an `update` badge when the upstream repo has a newer commit.
- **Sidebar:** Logo + "TAK Video Restreamer" label when installed.
- **Guard Dog:** HTTP monitor on `GET /login` port 3100. Alert + auto-restart after 3 failures, 15-min boot skip, cooldown.
- **Controls:** Start / Stop / Restart / Remove — all in one row. Remove prompts for the infra-TAK admin password.
- **Update Now:** `git pull --ff-only` + `docker compose up -d --build` with live log stream in the UI.

---

## Changes

- `app.py` `detect_modules()` (~line 1035): add `tak_video_restreamer` module with `conflicts: ['mediamtx']`; add `conflicts: ['tak_video_restreamer']` to `mediamtx`
- `app.py` `render_sidebar()` (~line 1213): TVR sidebar link with logo + text label
- `app.py` `SERVICE_DOMAIN_DEFAULTS` (~line 9565): `'tak_video_restreamer': 'stream'`
- `app.py` `generate_caddyfile()` (~line 11767): TVR Caddy block — HLS/public paths bypass, plain reverse_proxy to 127.0.0.1:3100 (no Authentik wrapper)
- `app.py` `TVR_MEDIAMTX_YML`, `TVR_DOCKER_COMPOSE` (~line 19215): compose and mediaMTX config templates; port mapping `127.0.0.1:3100:3000`; Docker healthcheck on `/login`
- `app.py` `_tvr_deploy_status`, `_run_tvr_deploy()` (~line 19421): 6-step deploy function (Docker check, clone/pull, write config, build, UFW, register)
- `app.py` `_get_tvr_version_info()` (~line 12768): git SHA version + GitHub API latest-commit check
- `app.py` `get_all_module_versions()` (~line 12830): add TVR entry
- `app.py` `_ensure_authentik_tvr_app()` (~line 20069): Authentik proxy provider registration (optional, when Authentik is installed and FQDN is set)
- `app.py` TVR routes (`/tak-video-restreamer`, `/api/tak-video-restreamer/*`): page, deploy, deploy-status, control, logs, uninstall, set-password, update, update-status
- `app.py` Guard Dog monitors list (~line 4364): `tvr_http` monitor on `/login:3100`; health check handler; service multi-monitor map
- `app.py` `marketplace_page()` + `MARKETPLACE_TEMPLATE` (~line 49127): conflict detection (`_conflict_with`), `.blocked` card CSS, conflict banner
- `app.py` console card template (~line 47692): add `tak_video_restreamer` to name-show list; suppress description for TVR; raw SHA version (no `v` prefix)
- `app.py` `TVR_TEMPLATE` (~line 48525): standalone CSS (no BASE_CSS), logo header, status/controls card with Remove inline, update banner, change-password form, deploy success banner
- `app.py` `VERSION`: `0.9.41-alpha` → `0.9.42-alpha`

---

## Validation

Validated on test12 (SSD-Nodes, `172.93.50.47`, single-server, FQDN `test12.taktical.net`):

- Deploy from Marketplace: clean 6-step run, Docker image built, container started
- Port conflict guard: confirmed MediaMTX marketplace card blocked when TVR installed
- Stream endpoints: RTSP, RTSPS, SRT, RTMP, HLS ABR all showing correct `stream.test12.taktical.net` hostname
- Password change: new password patched into docker-compose.yml, container recreated, login confirmed with new credentials
- Guard Dog: `tvr_http` monitor healthy (GET /login → 200)
- Console card: name, SHA version, Running badge all correct
- Sidebar: logo + "TAK Video Restreamer" label visible
- Remove button: prompts for admin password, removes container, returns to Marketplace
