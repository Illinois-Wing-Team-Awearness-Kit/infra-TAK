# v0.8.6-alpha — Work Plan

---

## 1. Disk I/O display — dashboard vs reality

**Problem:** The console dashboard "Disk I/O (current)" shows cached/buffered speed (e.g. 667 MB/s read, 998 MB/s write on Azure) which is RAM cache speed, not real disk throughput. This contradicts:
- `start.sh` which uses `dd oflag=dsync` (256 MB) and correctly warns at < 200 MB/s
- Guard Dog's `tak-diskio-watch.sh` which uses `dd oflag=dsync` (10 MB) every 15 min and logs to `/var/lib/takguard/diskio_history.csv`

The inflated number is misleading — operators see "998 MB/s write" in the dashboard while `start.sh` warns "145 MB/s — slow". Field case: Azure D8as_v5 with P30 OS disk, `start.sh` reports 145 MB/s, dashboard reports 998 MB/s. No way to tell which is right without knowing the implementation.

**Fix:**
- When Guard Dog is installed, read the latest disk speed from `/var/lib/takguard/diskio_history.csv` — already real, already `oflag=dsync`, no re-benchmark needed
- When Guard Dog is not installed, fall back to a `dd oflag=dsync` benchmark (same methodology as `start.sh`)
- Display should show "X MB/s write (sync)" so it's clear this is real throughput not cached

**Why this matters:** Operators use the dashboard number to assess whether their VPS is healthy. Wrong number = wrong assessment. Also sets expectation for deploy times.

---

## 2. `start.sh` — show public IP when behind NAT (Azure, AWS)

**Problem:** On Azure (and AWS behind NAT), `hostname -I` returns the private IP (e.g. `10.0.0.4`). `start.sh` prints:
```
Access: https://10.0.0.4:5001
```
The operator can't use this URL — they need the public IP. On SSD Nodes and DigitalOcean, `hostname -I` returns the public IP so this never manifested.

**Fix:**
```bash
# Try to get public IP from metadata/ipify, fall back to private
PUBLIC_IP=$(curl -s --max-time 3 https://api.ipify.org 2>/dev/null || echo "")
if [ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "$SERVER_IP" ]; then
    echo "  Access (public):  https://$PUBLIC_IP:5001"
    echo "  Access (private): https://$SERVER_IP:5001"
else
    echo "  Access: https://$SERVER_IP:5001"
fi
```

Show both when they differ, single line when they match (non-NAT VPS).

---

## 3. Authentik deploy — start containers before polling API

**Problem:** The Authentik deploy starts the API poll (Step 8) before `docker compose up -d` has finished pulling images and starting containers. On slow disk (Azure ~145 MB/s), the image pull takes 30-60s after the poll starts, meaning the poll is already 60-90s deep before containers exist. On very slow disk (< 100 MB/s) this causes the poll to time out entirely before containers come up.

**Observed:** Azure D8as_v5, first deploy — containers never started, poll ran for 900s, only came up after manual `docker compose up -d` from SSH.

**Fix:** After `docker compose up -d`, wait for at least `postgresql` container to show `healthy` before starting the API poll. Use `docker compose ps` or `docker inspect` health state. Something like:
```python
# Wait for postgresql healthy before starting API poll
for _ in range(60):
    result = run("docker compose ps postgresql --format json")
    if "healthy" in result:
        break
    time.sleep(5)
```
This adds at most 5-10s on fast boxes (postgres comes up quickly) and prevents the race on slow ones.

---

## 4. Rollback plan (standard for all v0.8.x)

Per the v0.8.x pattern — all three fixes above are non-destructive:

| Fix | Rollback |
|-----|---------|
| Disk display reads from CSV | If CSV missing, falls back to cached display — no regression |
| `start.sh` public IP | `curl` has 3s timeout, falls back gracefully if no internet at boot |
| Container health gate | If health check fails after 5 min, proceed to poll anyway — same behavior as today |

No new containers, no new services, no CoreConfig changes. Safe to ship together.

---

## 5. Notes from Azure test deploy (April 2026)

- Confirmed working Azure config (buddy's VM): `Standard_D8as_v5`, Ubuntu 22.04, East US, default 64 GiB P10 OS disk, no data disks. Running infra-TAK fine. **No special disk config required on Azure.**
- Azure P30 OS disk: `dd oflag=dsync` = 145 MB/s. Azure caps OS disk throughput regardless of tier. This is acceptable.
- Console dashboard showed 998 MB/s (cached) — confirmed misleading. See item 1 above.
- Root cause of first deploy failure: `docker compose up -d` had NOT been called when the API poll started. Containers never existed. Poll ran 900s, operator had to SSH in and run `docker compose up -d` manually. This is a code bug (item 3), NOT a disk speed issue.
- Azure P10 64 GiB OS disk is sufficient. No need to upsize disk for infra-TAK on Azure.
- Disk size recommendation for Azure: 64-128 GiB OS disk (P10/P15), no data disk needed.
