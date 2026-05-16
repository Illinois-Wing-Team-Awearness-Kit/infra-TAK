#!/usr/bin/env bash
# v0.9.23 Phase 6b (v2 corrected model) — TAK Server connection-state diagnostic.
#
# Replaces the Marti-API-based v1 script. Why? The Marti `/api/clientEndPoints`
# endpoint reports `lastEventTime: null` for clients that are currently in the
# Disconnected state, which the v1 script misclassified as "zombie subscription".
# Field forensic on tak-10 (2026-05-15) proved the misclassification: device
# UID D8985041-... was tagged as a null-time zombie at 13:31 PT, then
# reconnected 60 seconds later. It had 7+ connect/disconnect cycles that day.
#
# Source of truth (v2): query cot DB tables `client_endpoint` (identities) and
# `client_endpoint_event` (timestamps + event types, only 2 types: 1=Connected,
# 2=Disconnected) directly. Last event per identity tells us actual state.
#
# infra-TAK runs TAK Server as a HOST systemd service. The cot DB is local
# PostgreSQL on the same box. No mTLS / no cert passphrase needed — we hit
# the DB via `sudo -u postgres psql cot` (peer auth).
#
# Usage:
#   curl -sk -o /tmp/zombies.sh https://raw.githubusercontent.com/takwerx/infra-TAK/dev/ops/diagnostics/anchortak/zombies.sh
#   curl -sk -o /tmp/zombies.py https://raw.githubusercontent.com/takwerx/infra-TAK/dev/ops/diagnostics/anchortak/zombies.py
#   sudo bash /tmp/zombies.sh
#
# Run as root or via sudo. Reads no certs, prompts for nothing.

set -euo pipefail

if ! command -v psql >/dev/null 2>&1; then
    echo "ERROR: psql not installed. This diagnostic only runs on infra-TAK boxes"
    echo "       where TAK Server's local PostgreSQL is installed."
    exit 0
fi

if ! sudo -u postgres -n psql -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw cot; then
    echo "ERROR: cot database not found. TAK Server is not deployed on this host,"
    echo "       or the cot DB was renamed. Connection-state diagnostic does not"
    echo "       apply here."
    exit 0
fi

# One-shot stats query (matches `_takserver_connection_state` in app.py).
SQL_STATS=$(cat <<'EOSQL'
WITH last_event_per_id AS (
  SELECT DISTINCT ON (client_endpoint_id)
    client_endpoint_id, connection_event_type_id, created_ts
  FROM client_endpoint_event
  ORDER BY client_endpoint_id, created_ts DESC
)
SELECT
  (SELECT COUNT(*) FROM client_endpoint) AS total_identities,
  COUNT(*) FILTER (WHERE connection_event_type_id = 1) AS connected,
  COUNT(*) FILTER (WHERE connection_event_type_id = 2) AS disconnected,
  (SELECT COUNT(*) FROM client_endpoint_event) AS total_events,
  (SELECT COUNT(*) FROM client_endpoint_event WHERE created_ts > NOW() - INTERVAL '5 minutes') AS e5,
  (SELECT COUNT(*) FROM client_endpoint_event WHERE created_ts > NOW() - INTERVAL '1 hour') AS e1h,
  (SELECT COUNT(*) FROM client_endpoint_event WHERE created_ts > NOW() - INTERVAL '24 hours') AS e24h,
  (SELECT MIN(created_ts) FROM client_endpoint_event) AS earliest,
  (SELECT MAX(created_ts) FROM client_endpoint_event) AS latest
FROM last_event_per_id;
EOSQL
)

# Sample currently-connected clients (top 10 by most recent connect).
SQL_SAMPLE=$(cat <<'EOSQL'
WITH last_event_per_id AS (
  SELECT DISTINCT ON (client_endpoint_id)
    client_endpoint_id, connection_event_type_id, created_ts
  FROM client_endpoint_event
  ORDER BY client_endpoint_id, created_ts DESC
)
SELECT ce.callsign, ce.uid, COALESCE(ce.username, ''), le.created_ts
FROM last_event_per_id le
JOIN client_endpoint ce ON ce.id = le.client_endpoint_id
WHERE le.connection_event_type_id = 1
ORDER BY le.created_ts DESC
LIMIT 10;
EOSQL
)

STATS_OUT=$(mktemp)
SAMPLE_OUT=$(mktemp)
trap 'rm -f "$STATS_OUT" "$SAMPLE_OUT"' EXIT

if ! sudo -u postgres psql cot -tAF$'\t' -c "$SQL_STATS" > "$STATS_OUT" 2>/dev/null; then
    echo "ERROR: cot DB stats query failed. Check that postgresql.service is running:"
    echo "       sudo systemctl status postgresql"
    exit 0
fi

sudo -u postgres psql cot -tAF$'\t' -c "$SQL_SAMPLE" > "$SAMPLE_OUT" 2>/dev/null || true

ZOMBIES_PY=${ZOMBIES_PY:-/tmp/zombies.py}
if [ ! -f "$ZOMBIES_PY" ]; then
    echo "ERROR: $ZOMBIES_PY not found. Download with:"
    echo "  curl -sk -o /tmp/zombies.py https://raw.githubusercontent.com/takwerx/infra-TAK/dev/ops/diagnostics/anchortak/zombies.py"
    exit 0
fi

python3 "$ZOMBIES_PY" "$STATS_OUT" "$SAMPLE_OUT"
