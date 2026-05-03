# v0.8.6-alpha Release Notes

## Headline: Azure / NAT deploy reliability — four field bugs fixed

All four fixes were found and confirmed during the first Azure deployment test (`tak-test-3`, Standard_D8as_v5, P10 64 GiB OS disk, ~145 MB/s sync write). Every fix is non-destructive with a safe fallback path.

---

## Changes

### 1. Authentik deploy: containers never started on slow-disk VMs

**Field case:** Azure D8as_v5, first deploy — containers never started. The API poll (Step 8) ran for 900 seconds and timed out. The operator had to SSH in and run `docker compose up -d` manually to recover.

**Root cause:** Step 7 (`docker compose up -d` + image pull) was nested inside an `elif needs_pg_update:` block in `app.py`. On a fresh deploy `needs_pg_update` is always `False`, so the block was skipped entirely. The API poll started polling for a server that had never been launched.

**Fix:** Un-indented 121 lines out of the `elif needs_pg_update:` guard so the compose bring-up, network patch, and LDAP container start execute unconditionally on every deploy path.

**Impact:** Any fresh Authentik deploy on any disk speed now works. Deploy time on Azure dropped from 900 s + manual SSH to ~4 minutes end-to-end.

---

### 2. `start.sh`: private IP shown on Azure / AWS (NAT)

**Field case:** Azure (and AWS behind NAT): `hostname -I` returns `10.0.0.x`. `start.sh` printed `Access: https://10.0.0.4:5001` — an unreachable private address.

**Fix:** Added a `curl` call to `api.ipify.org` (3 s timeout, graceful fallback). When the returned public IP differs from `hostname -I`, both are displayed:

```
Access (public):  https://20.114.56.117:5001
Access (private): https://10.0.0.4:5001
```

Single-line output is preserved on non-NAT VPS where they match.

---

### 3. Dashboard disk I/O: cached speed shown instead of real sync speed

**Field case:** Azure console dashboard showed "998 MB/s write" while `start.sh` correctly reported "145 MB/s" and warned about slow disk. Operators use the dashboard number to assess VPS health; the inflated cached number gave a false "all good" signal.

**Root cause:** Dashboard used `vmstat` output (Linux buffer/cache speed, not real throughput).

**Fix:**
- When Guard Dog is installed, read the latest sync write speed from `/var/lib/takguard/diskio_history.csv` (Guard Dog already benchmarks with `oflag=dsync` every 15 minutes — no extra I/O).
- When Guard Dog is not installed, fall back to `vmstat` but label it `"(vmstat, cached)"` so the source is visible.
- The manual disk speed test button now uses `dd oflag=dsync` (was `oflag=direct`) to match `start.sh` methodology.
- UI label updated: "Disk I/O (sync, Guard Dog)" or "Disk I/O (vmstat, cached)" depending on source.

---

### 4. LDAP SA bind check: always reported failure even when LDAP was working

**Field case (test-3):** After a successful Authentik deploy, the "Final check: LDAP SA bind" ran 24 attempts and reported failure every time. Meanwhile the LDAP container log showed `"authenticated from session"` for every attempt from #2 onward — the SA was authenticating successfully, but the check didn't recognize it.

**Two bugs found:**

**Bug A — wrong ldapsearch search base:**
`_test_ldap_bind_dn_verdict` ran ldapsearch against `dc=takldap` with base scope. Authentik doesn't expose a root object at the base DN, so ldapsearch always got LDAP error 32 ("no such object") regardless of whether the bind succeeded. Exit code was always non-zero, so the "exit code 0 = ok" fast-path never fired. Fixed by searching `ou=users,dc=takldap` with one-level scope for `cn=adm_ldapservice` — this returns an actual result when the bind works, making the exit-code check reliable.

**Bug B — timing race on Docker log check:**
`time.sleep(2)` in the inner verdict loop was too short on Azure's slow disk. The "authenticated from session" log entry was written at the same second our `docker logs --since 45s` command ran. The entry was consistently either missing or just landing as we read. Fixed by increasing to `sleep(5)` and widening the log window from `--since 45s` to `--since 90s`.

**Fallback catch-all:** `_authentik_deploy_final_verify_ldap_sa` now directly checks Docker logs for `"authenticated from session"` + `"adm_ldapservice"` as an independent fallback — no ldapsearch involved. This is what the test-3 deploy confirmed: LDAP WAS working from attempt 2 onward; we just couldn't detect it through ldapsearch.

**Result on test-3:**
```
Final check: LDAP SA bind (1/24)...
Final check: LDAP SA bind (2/24)...
✓ LDAP SA bind verified via Docker log (authenticated from session). Safe to proceed.
✓ Deploy complete.
```

---

## Behavior on existing (non-Azure) deployments

All four changes are transparent on SSD Nodes, DigitalOcean, and other non-NAT fast-disk VPS:

| Fix | On fast/non-NAT boxes |
|-----|-----------------------|
| Compose bring-up ordering | Same behavior — always ran correctly before on fast disk (race window much smaller) |
| `start.sh` public IP | `curl` returns same IP as `hostname -I` → single-line output unchanged |
| Disk I/O display | Guard Dog CSV preferred; fast-disk boxes show higher realistic numbers |
| LDAP bind check | ldapsearch now returns exit code 0 on first success → faster than before |

---

## Files changed

| File | Change |
|------|--------|
| `app.py` | Un-indented 121 lines out of `elif needs_pg_update:` in Authentik deploy (Step 7 runs unconditionally) |
| `app.py` | `_run_disk_speed_test_local()`: `oflag=direct` → `oflag=dsync`; timeout 60s → 120s |
| `app.py` | New `_read_guarddog_latest_write_mbs()`: reads sync write speed from Guard Dog's CSV |
| `app.py` | `_get_disk_io_local()`: prioritizes Guard Dog CSV over vmstat; returns 3-tuple `(read, write, source_label)` |
| `app.py` | `_get_disk_io_remote()`: same — reads Guard Dog CSV over SSH first, falls back to vmstat |
| `app.py` | `renderResourceBreakdown` JS: "Disk write speed (Guard Dog)" label; disk color thresholds corrected (absolute MB/s, higher = better); vCPU on own line |
| `app.py` | `_test_ldap_bind_dn_verdict()`: search base `dc=takldap` → `ou=users,dc=takldap` one-level for SA; `sleep(2)` → `sleep(5)`; log window `--since 45s` → `--since 90s` |
| `app.py` | `_authentik_deploy_final_verify_ldap_sa()`: added direct Docker log fallback for "authenticated from session" |
| `app.py` | New LDAP healthcheck wait and `_ensure_ldap_flow_authentication_none()` re-call before final bind check |
| `app.py` | VERSION bumped to `0.8.6-alpha` |
| `start.sh` | Show public IP when it differs from `hostname -I` (Azure/AWS NAT) |

---

### 5. Dashboard: disk speed color logic corrected

**Problem:** The `diskIoColor` function treated higher MB/s as *worse* (designed like a CPU utilization meter where high % = saturated). This meant 121 MB/s from Guard Dog showed red while 110 MB/s from the manual test showed cyan — backwards and contradictory.

**Fix:** Replaced ratio logic with absolute thresholds: ≥ 200 MB/s → green (fast SSD/NVMe), ≥ 80 MB/s → yellow (acceptable — Azure managed disk territory), < 80 MB/s → red (slow, will affect deploy times). Both the Guard Dog line and the manual speed test line now use the same color function so they can't contradict each other.

**Label fix:** "Disk I/O (sync, Guard Dog)" → "Disk write speed (Guard Dog)" — removed the misleading "I/O" label since Guard Dog only measures write speed (`dd oflag=dsync`). No read data is available from this method.

---

### 6. Dashboard: vCPU count on its own line

The vCPU count is now displayed on a separate indented line below the processor model name:

```
Processor: AMD EPYC 7763 64-Core Processor
           8 vCPUs
```

Previously it was appended inline with a `·` separator, which made the already-long processor model name harder to read.

---

## What v0.8.6 does NOT change

- No Authentik image tag change (still 2026.2.2).
- No Guard Dog changes.
- No CoreConfig changes.
- All v0.8.5 self-healing (proactive routing migration, gunicorn timeout, spiral monitor, verifier hardening) preserved unchanged.

## Rollback

All four fixes are non-destructive:

| Fix | Rollback |
|-----|----------|
| Compose ordering | Un-indent was the correct fix; no behavioral change on working boxes |
| `start.sh` public IP | `curl` has 3 s timeout; if ipify unreachable, falls back to single-line private IP output |
| Disk I/O display | If Guard Dog CSV missing, falls back to vmstat display |
| LDAP bind check | If both ldapsearch and Docker log checks fail, returns inconclusive (same as before) |
