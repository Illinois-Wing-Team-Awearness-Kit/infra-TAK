# anchortak — Authentik PG connection forensic tool

Diagnostic suite contributed by **Tom Andersen / AnchorTAK operations** to root-cause
the Authentik 2026.2.x idle PostgreSQL connection accumulation that infra-TAK's
`ak-pg-watchdog` was catching every 5-10 minutes.

The full analysis report (linked below) is the authoritative document for the
upstream bug and our chosen mitigation strategy. **Read it first** before changing
anything in `app.py` that touches Authentik PG behaviour.

## Files

| File | Purpose |
|---|---|
| `anchortak_monitor.sh` | Bash sampler. Runs `pg_stat_activity` against `authentik-postgresql-1` at 5-second intervals and writes two CSVs: one row-per-sample summary, plus a per-snapshot detail breakdown. Default sample window: 90 minutes. |
| `anchortak_report.py` | Python report generator. Reads the CSVs and produces a single self-contained HTML page with charts (idle over time, idle by class, age histogram) and observations. |
| `anchortak_main_20260515_053721.csv` | First production capture: anctakserver2, May 15 2026, 91 min × 1056 samples, infra-TAK v0.9.22 + Authentik 2026.2.3. |
| `anchortak_detail_20260515_053721.csv` | Per-snapshot detail capture from the same run (176 snapshots). |
| `anchortak_main_20260515_053721_report.html` | Rendered HTML report from the data above. |

## The analysis

The headline forensic write-up lives at:

  **`docs/UPSTREAM-AUTHENTIK-PG-LEAK-20714.md`**

Key findings:

- 100% of the leak is in `authentik-server-1`. `authentik-worker-1` is stable at 7±2.
- The dominant query class is `enterprise/license` cache lookup (avg 61.3 idle, peak 146) — 64% of all idle connections at any moment.
- 100% of the leaked connections have a blank `application_name`, indicating they came from `django_postgres_cache` (which uses raw `psycopg` connections that don't inherit Django's `application_name`), NOT from the Django ORM pool.
- 79.6% of leaked connections are aged >60s; 37.8% are aged >5min. CONN_MAX_AGE=10 (our v0.9.21 setting) is NOT being honored on this code path because the cache backend has its own pool.
- Matches confirmed upstream bug **[goauthentik/authentik#20714](https://github.com/goauthentik/authentik/issues/20714)**, label `bug/confirmed`, assigned to `rissson` (Authentik core).

## Running the monitor

Tom's script assumed `/home/takadmin` as a working dir; on infra-TAK boxes the
console runs as `takwerx`. Easiest invocation:

```bash
# On the dev box, as root or via sudo:
cd /opt/anchortak-capture 2>/dev/null || mkdir -p /opt/anchortak-capture && cd /opt/anchortak-capture
cp /home/takwerx/infra-TAK/ops/diagnostics/anchortak/anchortak_monitor.sh .
# Edit DURATION_MIN if you want shorter/longer than 90 min:
sed -i 's|^DURATION_MIN=.*|DURATION_MIN=15|' anchortak_monitor.sh
chmod +x anchortak_monitor.sh
./anchortak_monitor.sh
```

CSVs land in the current directory. To render the report:

```bash
python3 /home/takwerx/infra-TAK/ops/diagnostics/anchortak/anchortak_report.py \
    anchortak_main_*.csv
```

This produces `anchortak_main_*_report.html` next to the CSV.

## When to run this

- You suspect the `ak-pg-watchdog` is firing more than expected (default threshold idle≥150).
- You want to validate a change to Authentik tuning (CONN_MAX_AGE, MAX_REQUESTS, idle_session_timeout, etc.) actually moved the leak curve.
- You're considering bumping a known-bad value back up — verify against fresh data first.

## When NOT to run this

- Casual symptom-chasing. The bug is upstream, the mitigation is in place, and another 90-minute capture is unlikely to surface anything new until upstream fixes #20714. Watch the issue, not the symptom.

## Attribution

Original diagnostic suite by **Tom Andersen / AnchorTAK operations**, May 2026.
Vendored into infra-TAK with permission to make the analysis reproducible.
