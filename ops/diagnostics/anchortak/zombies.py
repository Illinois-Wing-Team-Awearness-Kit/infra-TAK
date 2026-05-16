#!/usr/bin/env python3
"""TAK Server connection-state diagnostic (v0.9.23 Phase 6b, v2 model).

Replaces the v1 "zombie subscription" bucket logic, which was based on a
misunderstanding of the Marti API's `lastEventTime: null` semantics. v1
interpreted null timestamps as "zombie subscriptions left behind by Authentik
LDAP outage windows"; field forensic on tak-10 (2026-05-15 afternoon) showed
that `lastEventTime: null` actually means "this client is currently in the
Disconnected state" — proven by re-querying a "zombie" device that was
actively connecting/disconnecting all day.

v2 reads the cot DB directly:

    client_endpoint                client_endpoint_event
    ───────────────                ─────────────────────
    id (PK)        ◄─── FK ──────── client_endpoint_id
    callsign                       connection_event_type_id  (1=Connected, 2=Disconnected)
    uid                            created_ts (NOT NULL, indexed)
    username
    team
    role

"Last event per identity" tells us actual connection state. Event count
in time buckets tells us actual activity.

Usage (called by zombies.sh):
    python3 zombies.py <stats_tsv_file> <sample_tsv_file>

stats_tsv_file (one row, tab-separated):
    total_identities  connected  disconnected  total_events
    events_5min  events_1h  events_24h  earliest_iso  latest_iso

sample_tsv_file (up to 10 rows, tab-separated):
    callsign  uid  username  connected_since_iso
"""
import sys
from datetime import datetime, timezone


def _int(s):
    try:
        return int((s or '0').strip() or '0')
    except ValueError:
        return 0


def _fmt_age(iso_str):
    if not iso_str:
        return '(none)'
    s = iso_str.strip()
    if not s:
        return '(none)'
    try:
        if ' ' in s and 'T' not in s:
            s_iso = s.replace(' ', 'T', 1)
        else:
            s_iso = s
        if '+' not in s_iso and 'Z' not in s_iso and s_iso.count('-') >= 3:
            last_dash = s_iso.rfind('-')
            if last_dash > 10:
                s_iso = s_iso[:last_dash] + ('+' + s_iso[last_dash + 1:].replace(':', '')).ljust(6, '0')
        dt = datetime.fromisoformat(s_iso.replace('Z', '+00:00'))
        age_s = (datetime.now(timezone.utc) - dt).total_seconds()
        if age_s < 60:
            return f'{int(age_s)}s ago'
        elif age_s < 3600:
            return f'{int(age_s / 60)}m ago'
        elif age_s < 86400:
            return f'{age_s / 3600:.1f}h ago'
        else:
            return f'{age_s / 86400:.1f}d ago'
    except Exception:
        return s


def main():
    if len(sys.argv) < 2:
        print("ERROR: usage: zombies.py <stats_tsv> [sample_tsv]")
        sys.exit(1)

    try:
        with open(sys.argv[1]) as f:
            raw_stats = f.read().strip()
    except OSError as e:
        print(f"ERROR: cannot read stats file: {e}")
        sys.exit(1)

    if not raw_stats:
        print("ERROR: stats file is empty — cot DB query may have failed.")
        sys.exit(1)

    line = raw_stats.splitlines()[0]
    parts = line.split('\t')
    if len(parts) < 9:
        print(f"ERROR: stats output malformed ({len(parts)} fields, expected 9): {line[:120]!r}")
        sys.exit(1)

    total_identities       = _int(parts[0])
    connected              = _int(parts[1])
    disconnected           = _int(parts[2])
    total_events           = _int(parts[3])
    events_last_5min       = _int(parts[4])
    events_last_1h         = _int(parts[5])
    events_last_24h        = _int(parts[6])
    earliest_event         = (parts[7] or '').strip()
    latest_event           = (parts[8] or '').strip()

    print('=== TAK Server connection state (cot DB) ===')
    print(f'  Currently connected     : {connected:4d}')
    print(f'  Currently disconnected  : {disconnected:4d}')
    print(f'  Total identities seen   : {total_identities:4d}  (all-time, immortal audit log)')
    print()
    print('=== Event activity (real signal) ===')
    print(f'  Events in last 5 min    : {events_last_5min:4d}')
    print(f'  Events in last 1 hour   : {events_last_1h:4d}')
    print(f'  Events in last 24 hours : {events_last_24h:4d}')
    print(f'  Total events (audit log): {total_events:4d}')
    if earliest_event:
        print(f'  Earliest event          : {earliest_event}  ({_fmt_age(earliest_event)})')
    if latest_event:
        print(f'  Latest event            : {latest_event}  ({_fmt_age(latest_event)})')

    if len(sys.argv) >= 3 and connected > 0:
        try:
            with open(sys.argv[2]) as f:
                raw_sample = f.read().strip()
        except OSError:
            raw_sample = ''
        if raw_sample:
            print()
            print('=== Currently connected (top 10 by most recent connect) ===')
            for ln in raw_sample.splitlines():
                sp = ln.split('\t')
                if len(sp) >= 4:
                    callsign, uid, username, since = sp[0], sp[1], sp[2], sp[3]
                    age = _fmt_age(since)
                    print(f'  {age:>12}  callsign={callsign!r:24}  '
                          f'username={username!r:20}  uid={uid!r}')

    print()
    # Advisory v2.1: see app.py `_takserver_connection_state` comment.
    # `client_endpoint_event` records ONLY state transitions (Connect=1,
    # Disconnect=2), not CoT traffic. Silence in `events_last_5min` is
    # normal steady state for any stably-connected client, NOT a routing
    # problem. So we don't alarm on it.
    if total_events == 0:
        print('ADVISORY: INACTIVE')
        print('         No events recorded. TAK Server may be freshly installed,')
        print('         or no client has ever connected.')
    elif connected > 0:
        print('ADVISORY: HEALTHY')
        print(f'         {connected} client(s) currently connected.')
        print(f'         {events_last_1h} state-transition event(s) in last hour, '
              f'{events_last_24h} in last 24h.')
        print(f'         Audit log: {total_events} events / {total_identities} identities.')
    elif connected == 0 and events_last_1h > 0:
        print('ADVISORY: IDLE')
        print(f'         No clients connected now, but {events_last_1h} event(s) in last hour.')
        print('         Recently active — normal between sessions.')
    elif connected == 0 and events_last_24h > 0:
        print('ADVISORY: QUIET')
        print(f'         No clients connected, {events_last_24h} event(s) in last 24h.')
        print('         Normal for test/standby boxes.')
    else:
        print('ADVISORY: DORMANT')
        print(f'         No events in last 24h. {disconnected} identity/identities currently disconnected.')
        print('         Test/idle box, or no clients have connected lately.')
        print('         Verify takserver.service is healthy if this is a production box.')


if __name__ == '__main__':
    main()
