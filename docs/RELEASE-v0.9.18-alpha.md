# v0.9.18-alpha — Authentik LDAP Routing Repair Hotfix

**Date:** 2026-05-13
**Type:** Critical hotfix. Drop-in update — no operator pre-flight, no migrations to run manually.

---

## TL;DR

Boxes whose Authentik LDAP outpost was spiraling on internal direct routing (`http://authentik-server-1:9000`) — and whose `~/authentik/docker-compose.yml` already had an `extra_hosts:` entry in the `ldap:` service block from any prior partial migration — were stuck in a forever repair-rollback loop because the v0.8.4/v0.8.5 single-pass YAML rewriter was injecting a **second** `extra_hosts:` key, producing an unparseable compose file and tripping `docker compose up`'s strict YAML check. The spiral monitor's repair attempt was rolling back every 10 minutes, leaving the box at 400–500% Authentik CPU with no automatic recovery path. **Click Update Now** — the next spiral monitor tick (within 10 min) will succeed and break the spiral.

---

## What was wrong

The v0.8.4 reactive (`_apply_authentik_ldap_routing_repair`) and v0.8.5 proactive (`_ensure_authentik_ldap_outpost_on_fqdn`) LDAP outpost routing migrations rewrote the `ldap:` service block in `~/authentik/docker-compose.yml` in a single pass:

```python
for line in compose_text.splitlines(keepends=True):
    if line.startswith('  ldap:'):
        ldap_has_extra_hosts = False
    if 'extra_hosts:' in line:
        ldap_has_extra_hosts = True
    if 'image: ghcr.io/goauthentik/ldap' in line:
        new_lines.append(line)
        if not ldap_has_extra_hosts:
            new_lines.append('    extra_hosts:\n')
            new_lines.append(f'      - "{fqdn}:host-gateway"\n')
```

In every real-world compose the `image:` line comes **before** the `extra_hosts:` line. So when the rewriter processed `image:`, `ldap_has_extra_hosts` was still `False` and a brand-new `extra_hosts:` block got inserted right after `image:`. A few lines later the rewriter passed the **original** `extra_hosts:` block through unchanged. Result:

```yaml
  ldap:
    image: ghcr.io/goauthentik/ldap:${AUTHENTIK_TAG:-2026.2.3}
    extra_hosts:                                            # NEW — inserted by rewriter
      - "tak.example.com:host-gateway"
    extra_hosts:                                            # OLD — original, kept verbatim
      - "tak.example.com:host-gateway"
    ports:
    ...
```

Two `extra_hosts:` keys → docker compose's strict YAML parser refused to load the file:

```
failed to parse /root/authentik/docker-compose.yml: yaml: construct errors:
  line 1: line 107: mapping key "extra_hosts" already defined at line 105
```

The rewriter's error path then restored the backup, so on the surface the file looked fine — but the migration never landed, the spiral was never broken, and the `_authentik_spiral_monitor` retried every 10 minutes with the same failure.

### Observed on tak-10 (test12.taktical.net), 2026-05-13

- **Authentik CPU:** 492% (4.9 cores pinned)
- **Postgres CPU:** ~220%
- **Postgres connections from authentik-server:** ~213 (≥30 was the spiral threshold)
- **LDAP outpost `failed to execute flow`:** 74 per minute
- **LDAP outpost `nil pointer dereference`:** present every minute
- **Repair attempts in logs:** 6 in 70 minutes (21:10, 21:20, 21:31, 21:41, 21:51 — proactive routing + routing repair — every one rolled back with `mapping key "extra_hosts" already defined at line 105`)

The LDAP service-account password worked when probed directly with `ldapsearch -x -H ldap://127.0.0.1:389 -D adm_ldapservice -w <pw>` — the failure was the outpost's HTTP path to authentik-server, not the bind itself. That ruled out a credential issue and pointed straight at the spiral.

---

## What changed

Both rewriter implementations are now replaced with a single shared helper, `_rewrite_ldap_compose_to_fqdn(compose_text, fqdn)`, that runs **two passes**:

1. **Pre-scan pass.** Walk the compose, identify the LDAP block's start/end indices (using the same `^  [a-z_-]+:\s*$` heuristic as before), and record whether `extra_hosts:` already exists anywhere inside that block, and whether the LDAP image line is present at all.
2. **Rewrite pass.** Walk the compose again; for lines inside the LDAP block only, replace `AUTHENTIK_HOST:` with `https://<fqdn>`, and inject a new `extra_hosts:` block right after `image:` **only when the pre-scan determined none existed**.

Returns `(new_text, image_seen)`. Callers MUST abort if `image_seen == False` (LDAP service missing or has the wrong image — leaves the compose untouched).

Side-effect benefits:

- ~50 lines of duplicated YAML-rewriting logic removed from `_apply_authentik_ldap_routing_repair` and `_ensure_authentik_ldap_outpost_on_fqdn` (both now 2 lines: call the helper, check `image_seen`, write the file).
- The helper is pure (no I/O), making it easy to test in isolation against any compose fixture.

### Local validation matrix (all pass)

| Case | Input state | Expected | Result |
|------|------------|----------|--------|
| 1 | tak-10 stuck state — `extra_hosts:` present + `AUTHENTIK_HOST: http://authentik-server-1:9000/` | 1 `extra_hosts:` key, `AUTHENTIK_HOST` rewritten to FQDN, valid YAML | ✓ PASS |
| 2 | Clean compose — no `extra_hosts:`, `AUTHENTIK_HOST: http://authentik-server-1:9000` | `extra_hosts:` added with FQDN, `AUTHENTIK_HOST` rewritten, valid YAML | ✓ PASS |
| 3 | No LDAP service in compose | `image_seen=False`, input returned unchanged | ✓ PASS |
| 4 | LDAP block with non-LDAP image (corrupt) | `image_seen=False`, input returned unchanged | ✓ PASS |
| 5 | Already on FQDN (`extra_hosts:` + `https://...`) | Idempotent — `extra_hosts:` kept, `AUTHENTIK_HOST` re-set to same FQDN, 1 key | ✓ PASS |
| 6 | Bug repro — run **old** rewriter on case 1 | Two `extra_hosts:` keys in output (the bug) | ✓ Confirmed |

PyYAML accepts duplicate mapping keys silently (last-wins). Docker Compose's Go YAML parser is strict and rejects them — which is why the bug only manifested on the VPS, not in any unit test that round-tripped through PyYAML. The new helper produces output with exactly one `extra_hosts:` key in every case, so it's correct for both parsers.

---

## Operator notes

- **Drop-in from v0.9.17.** Leave the update channel on `main` (after this lands on main).
- **Boxes that have never tripped the spiral monitor:** no behavioural change — the rewriter is only called by the spiral monitor's repair path or the proactive migration, both of which gate on independent preconditions (TAK installed, FQDN configured, Caddy reachable, currently on internal direct routing). The helper is just code-cleaner on those boxes.
- **Boxes currently stuck in the repair loop (like tak-10 was):** Update Now will deploy the fix. The next spiral monitor tick (within 10 min, see `journalctl -u takwerx-console | grep "spiral monitor"`) will:
  1. Pre-scan: detect that `extra_hosts:` already exists in the LDAP block.
  2. Rewrite: replace only `AUTHENTIK_HOST:` (the line that was actually wrong), leave `extra_hosts:` alone.
  3. Write the new compose, `docker compose up -d --no-deps --force-recreate ldap`.
  4. Validate: wait 30s, check `docker logs authentik-ldap-1` for `successfully connected websocket` and absence of TLS/route errors.
  5. Record the success in `settings.json` (`authentik_spiral_last_repair`).

After the repair lands you should see Authentik CPU collapse from 400–500% to its normal idle (low single-digit percent), Postgres idle-in-trans drop from 200+ back to <10, and LDAP outpost logs go quiet (no more `failed to execute flow` or `nil pointer dereference`).

### Verify the fix landed

```bash
# Check the rewriter ran cleanly (no more "yaml: construct errors" in logs)
journalctl -u takwerx-console --since "10 min ago" | grep -E "spiral monitor|routing repair|proactive routing"
# Expected: ✓ routing repair / proactive routing — outpost healthy on https://<fqdn>

# Verify the compose now points LDAP at FQDN
grep "AUTHENTIK_HOST:" ~/authentik/docker-compose.yml
# Expected: AUTHENTIK_HOST: https://<your-fqdn>  (NOT http://authentik-server-1:9000)

# Verify exactly one extra_hosts: in the LDAP block
sed -n '/^  ldap:/,/^[a-z]/p' ~/authentik/docker-compose.yml | grep -c "extra_hosts:"
# Expected: 1

# Confirm Authentik CPU normalized
docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}' | grep authentik
# Expected: all <5%
```

---

## Slack-able summary

> infra-TAK v0.9.18: critical hotfix to the v0.8.4/v0.8.5 LDAP outpost routing repair. Single-pass YAML rewriter was injecting a duplicate `extra_hosts:` key when the LDAP block already had one — making `docker-compose.yml` unparseable and trapping spiraling boxes in a forever repair-rollback loop (10-min cycles). Observed on tak-10: 492% Authentik CPU, 74 failed-flow events/min, 213 Postgres connections, 6 failed repair attempts in 70 min. Rewriter is now a shared helper that pre-scans the LDAP block once before deciding what to inject; validated against 5 real-world compose states. Drop-in update — next spiral monitor tick after Update Now will land the fix automatically.
