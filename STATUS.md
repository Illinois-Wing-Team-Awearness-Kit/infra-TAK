# infra-TAK — Working Status

> Update this file at the end of every session. `@STATUS.md` at the start of a new chat to resume instantly.

---

## Active branch
`ZeroTierServersSetup` — Server 1 VPS pulls from this branch

## VPS (Server 1)
- Host: `190.102.110.79` (`ssh.prod.ilwg.us`)
- Repo path: `/root/infra-TAK`
- Console: `https://190.102.110.79:5001`
- Service: `takwerx-console` (gunicorn, port 5001)

## VPS (tak-10 / Node-RED server — separate)
- Host: `172.93.50.47`
- Repo path: `/home/takwerx/infra-TAK`
- Container: `nodered`
- Configurator: `http://172.93.50.47:1880/configurator`
- Node-RED editor: `http://172.93.50.47:1880`

---

## What was just shipped (latest commits on ZeroTierServersSetup)

| Commit | What |
|--------|------|
| `3feb217` | **revert:** remove remote TAK Portal deployment — restore local Server 1 install (ZeroTier Authentik URL fix kept) |
| `350030d` | **feat:** add ZeroTier install to Server 1 setup flow in start.sh |
| `5426cfd` | **fix(authentik):** LDAP authorization_flow recursion bug — use ldap-authorization-flow not ldap-authentication-flow |
| `25dfd8c` | **fix:** enable VBM when installing LE cert on port 8446 |
| `89e7431` | **fix:** hardcoded localhost Authentik URL in TAK Portal, MediaMTX, and console app deploy |
| `1f2bb8e` | **feat:** ZeroTier start.sh changes (Server 2/3 support) |

---

## Architecture — 3-Server Setup

| Server | Role | Host |
|--------|------|------|
| Server 1 | TAK Server + TAK Portal + Console | `190.102.110.79` |
| Server 2 | Authentik (SSO/LDAP) | ZeroTier |
| Server 3 | CloudTAK | ZeroTier |

ZeroTier network ties all three together. Interface: `ztfp6iovwi`

### Key ZeroTier fix (commit 89e7431)
`run_takportal_deploy` uses `_get_authentik_api_url(settings)` instead of
hardcoded `localhost` — forward-auth setup works when Authentik is on a
remote ZeroTier IP.

---

## What was just completed
- ✅ Server 1 fresh deploy working (Caddy → full stack via `start.sh`)
- ✅ ZeroTier install added to Server 1 `start.sh` flow
- ✅ TAK Portal remote-deploy code reverted — back to local Server 1 install
- ✅ ZeroTier Authentik URL fix preserved

---

## Next steps / watch list
- TAK Server `.deb` was uploaded as `takserver_5.7-RELEASE32_all.deb` — install via console if not already done
- Verify TAK Portal deploys successfully end-to-end on Server 1
- Verify Authentik (Server 2) forward-auth works with TAK Portal on Server 1 over ZeroTier

---

## Deploy cheat sheet (Server 1)
```bash
# Standard pull + restart
cd /root/infra-TAK && git pull && systemctl restart takwerx-console

# Re-run setup (if needed)
cd /root/infra-TAK && sudo ./start.sh

# Check console logs
journalctl -u takwerx-console -f
```
