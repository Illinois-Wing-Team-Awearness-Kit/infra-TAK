# v0.7.2-alpha Release Notes

**Patch release — fixes Node-RED crash on first update from older installs.**

---

## Bug Fix: Node-RED fails to start after v0.7.1-alpha update

**Symptom:** After updating to v0.7.1-alpha, Node-RED crashed on startup with:

```
Error: Error loading context store: Error: EACCES: permission denied,
open '/data/context/global/global.json'
```

**Root cause:** v0.7.1-alpha introduced a context migration that exports in-memory Node-RED configs to disk before switching to `localfilesystem` storage. The file was written via `docker cp`, which copies as root. Node-RED runs as the `node-red` user inside the container and cannot read a root-owned file.

**Fix:** After writing context files via `docker cp`, immediately chown the `/data/context` directory to `node-red:node-red` inside the running container. Applied in both places the file is written:

- `nodered/deploy.sh` — after restoring context on every deploy
- `app.py` — after the one-time migration in `_auto_nodered_settings()`

**If you hit this on v0.7.1-alpha**, run on the affected server:

```bash
docker exec nodered chown -R node-red:node-red /data/context && docker restart nodered
```

Then update to v0.7.2-alpha normally.

| File | Change |
|------|--------|
| `nodered/deploy.sh` | `chown -R node-red:node-red /data/context` after `docker start` |
| `app.py` | Same chown after context migration `docker cp`; VERSION → 0.7.2-alpha |

---

## ⚠️ Reminder from v0.7.1: Resync LDAP to TAK Server

If you haven't already done this after updating to v0.7.1, **do it now.**

Go to **TAK Server page → Resync LDAP to TAK Server**.

This fixes password changes taking up to 24 hours to propagate to ATAK/iTAK devices. Without this one-click fix, users who reset their password can still authenticate with the old password for up to 24 hours. After Resync, new passwords take effect within 2 minutes.

This applies to every existing deployment. Fresh installs get the correct setting automatically.
