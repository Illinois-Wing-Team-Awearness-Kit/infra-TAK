#!/bin/bash
# ================================================================
# AnchorTAK Diagnostic Monitor v2.1
# anctakserver2 — Authentik PostgreSQL connection investigation
#
# Fixes over v2.0:
#   - Dynamic container IP detection (no hardcoded IPs)
#   - AMD k10temp sensor targeted for accurate CPU temperature
#   - DELTA sign handling on first sample
#   - IPs printed at startup for verification
#
# Outputs TWO CSV files:
#   anchortak_main_TIMESTAMP.csv   — every 5s
#   anchortak_detail_TIMESTAMP.csv — every 30s
#
# Usage:  sudo bash anchortak_monitor.sh [duration_minutes]
# Default: 90 minutes
# ================================================================

INTERVAL=5
DETAIL_EVERY=30
DURATION="${1:-90}"
LOG_DIR="/home/takadmin"
RUN_ID=$(date +%Y%m%d_%H%M%S)
MAIN_CSV="${LOG_DIR}/anchortak_main_${RUN_ID}.csv"
DETAIL_CSV="${LOG_DIR}/anchortak_detail_${RUN_ID}.csv"
MAX_ITER=$(( (DURATION * 60) / INTERVAL ))

RED='\033[0;31m'; YLW='\033[0;33m'; GRN='\033[0;32m'
CYN='\033[0;36m'; BLD='\033[1m'; NC='\033[0m'

# ── Dynamic container IP detection ───────────────────────────
# Uses a socket probe from inside each container to find the
# exact IP that will appear in pg_stat_activity. This is the
# only reliable method — docker inspect returns the wrong IP
# when containers are on multiple networks (server is on both
# authentik_default AND infratak).
detect_container_ips() {
    echo -e "  Probing container IPs via live socket to postgresql..."

    # Open a TCP connection from inside each container to postgresql:5432
    # and return the local IP used — guaranteed to match pg_stat_activity.
    SERVER_IP=$(docker exec authentik-server-1 \
        python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(3)
try:
    s.connect(('postgresql', 5432))
    print(s.getsockname()[0])
    s.close()
except Exception as e:
    print('', end='')
" 2>/dev/null | tr -d ' \n')

    WORKER_IP=$(docker exec authentik-worker-1 \
        python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(3)
try:
    s.connect(('postgresql', 5432))
    print(s.getsockname()[0])
    s.close()
except Exception as e:
    print('', end='')
" 2>/dev/null | tr -d ' \n')

    # Fallback: docker inspect if python probe fails
    if [ -z "$SERVER_IP" ] || [ -z "$WORKER_IP" ]; then
        echo -e "  ${YLW}Socket probe failed, falling back to docker inspect${NC}"
        PG_NETWORK=$(docker inspect authentik-postgresql-1 \
            --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' \
            2>/dev/null | tr ' ' '\n' | grep -v '^$' | head -1)
        [ -z "$SERVER_IP" ] && SERVER_IP=$(docker inspect authentik-server-1 \
            --format "{{(index .NetworkSettings.Networks \"${PG_NETWORK}\").IPAddress}}" \
            2>/dev/null)
        [ -z "$WORKER_IP" ] && WORKER_IP=$(docker inspect authentik-worker-1 \
            --format "{{(index .NetworkSettings.Networks \"${PG_NETWORK}\").IPAddress}}" \
            2>/dev/null)
    fi

    if [ -z "$SERVER_IP" ] || [ -z "$WORKER_IP" ]; then
        echo -e "  ${RED}ERROR: Cannot determine container IPs.${NC}"
        echo -e "  ${RED}Are authentik-server-1 and authentik-worker-1 running?${NC}"
        exit 1
    fi

    # Show all distinct IPs currently in pg_stat_activity for cross-check
    echo -e "  ${BLD}IPs currently in pg_stat_activity (authentik DB):${NC}"
    docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA \
        -c "SELECT host(client_addr), count(*), state
            FROM pg_stat_activity
            WHERE datname='authentik' AND client_addr IS NOT NULL
            GROUP BY host(client_addr), state
            ORDER BY count DESC;" \
        2>/dev/null | while IFS='|' read ip cnt state; do
            printf "    %-16s  %3s conns  %s\n" "$ip" "$cnt" "$state"
        done

    echo ""

    # Verify our detected IPs against live pg_stat_activity
    SRV_CHECK=$(docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA \
        -c "SELECT count(*) FROM pg_stat_activity WHERE host(client_addr)='${SERVER_IP}';" \
        2>/dev/null | tr -d ' ')
    WKR_CHECK=$(docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA \
        -c "SELECT count(*) FROM pg_stat_activity WHERE host(client_addr)='${WORKER_IP}';" \
        2>/dev/null | tr -d ' ')

    PG_NETWORK="(socket-probed)"
}

# ── CPU: 1-second delta from /proc/stat ──────────────────────
get_cpu_pct() {
    local a1=( $(head -1 /proc/stat) )
    sleep 1
    local a2=( $(head -1 /proc/stat) )
    local t1=0 t2=0
    for i in 1 2 3 4 5 6 7 8; do
        t1=$(( t1 + ${a1[$i]:-0} ))
        t2=$(( t2 + ${a2[$i]:-0} ))
    done
    local dt=$(( t2 - t1 ))
    local di=$(( ${a2[4]} - ${a1[4]} ))
    [ "$dt" -eq 0 ] && echo 0 || echo $(( (dt - di) * 100 / dt ))
}

# ── CPU temperature — AMD k10temp (Ryzen) ────────────────────
# k10temp Tctl is the correct die temperature for Ryzen 5 PRO.
# Falls back to other sensors if k10temp not found.
get_temp() {
    local t=""
    # Try k10temp first (AMD Ryzen Tctl = temp1_input)
    for dir in /sys/class/hwmon/hwmon*/; do
        local name=$(cat "${dir}name" 2>/dev/null)
        if [ "$name" = "k10temp" ]; then
            t=$(cat "${dir}temp1_input" 2>/dev/null)
            [ -n "$t" ] && break
        fi
    done
    # Try nct6775 (SuperIO chip, covers package temp on some boards)
    if [ -z "$t" ]; then
        for dir in /sys/class/hwmon/hwmon*/; do
            local name=$(cat "${dir}name" 2>/dev/null)
            if [ "$name" = "nct6775" ] || [ "$name" = "nct6776" ]; then
                t=$(cat "${dir}temp1_input" 2>/dev/null)
                [ -n "$t" ] && break
            fi
        done
    fi
    # Generic fallback — highest temp reported (likely CPU)
    if [ -z "$t" ]; then
        t=$(cat /sys/class/hwmon/hwmon*/temp*_input 2>/dev/null | \
            sort -n | tail -1)
    fi
    [ -n "$t" ] && echo $(( t / 1000 )) || echo "0"
}

get_ram_pct() {
    awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}END{printf "%d",(t-a)*100/t}' \
        /proc/meminfo 2>/dev/null || echo "0"
}

# ── Primary connection metrics every 5s ──────────────────────
get_conn_primary() {
    docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA \
        -c "SELECT
              SUM(CASE WHEN state='idle'               THEN 1 ELSE 0 END),
              SUM(CASE WHEN state='idle' AND host(client_addr)='${SERVER_IP}' THEN 1 ELSE 0 END),
              SUM(CASE WHEN state='idle' AND host(client_addr)='${WORKER_IP}' THEN 1 ELSE 0 END),
              SUM(CASE WHEN state='idle'
                        AND host(client_addr) NOT IN ('${SERVER_IP}','${WORKER_IP}')
                        THEN 1 ELSE 0 END),
              SUM(CASE WHEN state='active'              THEN 1 ELSE 0 END),
              SUM(CASE WHEN state='idle in transaction' THEN 1 ELSE 0 END)
            FROM pg_stat_activity WHERE datname='authentik';" \
        2>/dev/null | tr -d ' ' | tr '|' ',' || echo "ERR,ERR,ERR,ERR,ERR,ERR"
}

# ── Detail snapshot every 30s ────────────────────────────────
run_detail() {
    local TS="$1" ELAPSED="$2"

    # Query type × source — the core diagnostic question
    QT=$(docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA \
        -c "SELECT
              SUM(CASE WHEN query LIKE '%enterprise/license%'      AND host(client_addr)='${SERVER_IP}' THEN 1 ELSE 0 END),
              SUM(CASE WHEN query LIKE '%enterprise/license%'      AND host(client_addr)='${WORKER_IP}' THEN 1 ELSE 0 END),
              SUM(CASE WHEN query LIKE '%LISTEN%channels%'         AND host(client_addr)='${SERVER_IP}' THEN 1 ELSE 0 END),
              SUM(CASE WHEN query LIKE '%LISTEN%channels%'         AND host(client_addr)='${WORKER_IP}' THEN 1 ELSE 0 END),
              SUM(CASE WHEN query='COMMIT'                         AND host(client_addr)='${SERVER_IP}' THEN 1 ELSE 0 END),
              SUM(CASE WHEN query='COMMIT'                         AND host(client_addr)='${WORKER_IP}' THEN 1 ELSE 0 END),
              SUM(CASE WHEN query LIKE '%django_postgres_cache%'
                       AND query NOT LIKE '%enterprise/license%'   AND host(client_addr)='${SERVER_IP}' THEN 1 ELSE 0 END),
              SUM(CASE WHEN query LIKE '%django_postgres_cache%'
                       AND query NOT LIKE '%enterprise/license%'   AND host(client_addr)='${WORKER_IP}' THEN 1 ELSE 0 END),
              SUM(CASE WHEN (query IS NULL OR query='')            AND host(client_addr)='${SERVER_IP}' THEN 1 ELSE 0 END),
              SUM(CASE WHEN (query IS NULL OR query='')            AND host(client_addr)='${WORKER_IP}' THEN 1 ELSE 0 END)
            FROM pg_stat_activity
            WHERE datname='authentik' AND state='idle';" \
        2>/dev/null | tr -d ' ' | tr '|' ',' || echo "0,0,0,0,0,0,0,0,0,0")

    # application_name: blank=async pool, named=sync Django ORM
    AN=$(docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA \
        -c "SELECT
              SUM(CASE WHEN (application_name IS NULL OR application_name='') THEN 1 ELSE 0 END),
              SUM(CASE WHEN  application_name IS NOT NULL AND application_name!='' THEN 1 ELSE 0 END)
            FROM pg_stat_activity
            WHERE datname='authentik' AND state='idle';" \
        2>/dev/null | tr -d ' ' | tr '|' ',' || echo "0,0")

    # Connection age distribution — proves/disproves CONN_MAX_AGE=10 effectiveness
    # If working: <10s bucket should dominate. Old connections = async pool leak proof.
    AGES=$(docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA \
        -c "SELECT
              SUM(CASE WHEN EXTRACT(EPOCH FROM (NOW()-state_change)) < 10            THEN 1 ELSE 0 END),
              SUM(CASE WHEN EXTRACT(EPOCH FROM (NOW()-state_change)) BETWEEN 10 AND 30  THEN 1 ELSE 0 END),
              SUM(CASE WHEN EXTRACT(EPOCH FROM (NOW()-state_change)) BETWEEN 30 AND 60  THEN 1 ELSE 0 END),
              SUM(CASE WHEN EXTRACT(EPOCH FROM (NOW()-state_change)) BETWEEN 60 AND 300 THEN 1 ELSE 0 END),
              SUM(CASE WHEN EXTRACT(EPOCH FROM (NOW()-state_change)) > 300             THEN 1 ELSE 0 END)
            FROM pg_stat_activity
            WHERE datname='authentik' AND state='idle';" \
        2>/dev/null | tr -d ' ' | tr '|' ',' || echo "0,0,0,0,0")

    # Top idle query count (how many idle connections share the most common query)
    TOP_Q_CNT=$(docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA \
        -c "SELECT count(*) FROM pg_stat_activity
            WHERE datname='authentik' AND state='idle'
              AND query IS NOT NULL AND query!=''
              AND query=(
                SELECT query FROM pg_stat_activity
                WHERE datname='authentik' AND state='idle'
                  AND query IS NOT NULL AND query!=''
                GROUP BY query ORDER BY count(*) DESC LIMIT 1
              );" \
        2>/dev/null | tr -d ' ' || echo "0")

    printf '"%s",%s,%s,%s,%s,%s\n' \
        "$TS" "$ELAPSED" "$QT" "$AN" "$AGES" "$TOP_Q_CNT" \
        >> "$DETAIL_CSV"
}

# ── CSV headers ───────────────────────────────────────────────
write_headers() {
    echo "timestamp_utc,elapsed_sec,cpu_pct,temp_c,load_1m,load_5m,load_15m,ram_pct,\
idle_total,idle_server,idle_worker,idle_other,\
active_total,idle_in_tx,\
delta_idle,event,notes" > "$MAIN_CSV"

    echo "timestamp_utc,elapsed_sec,\
qt_license_srv,qt_license_wkr,\
qt_listen_srv,qt_listen_wkr,\
qt_commit_srv,qt_commit_wkr,\
qt_cache_other_srv,qt_cache_other_wkr,\
qt_empty_srv,qt_empty_wkr,\
appname_blank,appname_named,\
age_lt10s,age_10_30s,age_30_60s,age_60_300s,age_gt300s,\
top_query_count" > "$DETAIL_CSV"
}

# ── Startup ───────────────────────────────────────────────────
detect_container_ips
write_headers

echo -e "${BLD}"
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  AnchorTAK Diagnostic Monitor v2.1                              ║"
echo "║  anctakserver2 · infra-TAK v0.9.22 · Authentik 2026.2.3         ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  ${CYN}Main CSV   :${NC} $MAIN_CSV"
echo -e "  ${CYN}Detail CSV :${NC} $DETAIL_CSV"
echo ""
echo -e "  ${BLD}Container IPs detected on network: ${PG_NETWORK}${NC}"
printf  "  Server  (authentik-server-1) : ${CYN}%s${NC}  [%s idle connections in pg]\n" \
    "$SERVER_IP" "${SRV_CHECK:-0}"
printf  "  Worker  (authentik-worker-1) : ${CYN}%s${NC}  [%s idle connections in pg]\n" \
    "$WORKER_IP" "${WKR_CHECK:-0}"
echo ""
printf  "  Duration: ${DURATION}m  |  Interval: ${INTERVAL}s  |  Detail snapshot: every ${DETAIL_EVERY}s\n"
echo    "  Ctrl+C to stop early"
echo ""
printf  "  %-24s %4s %5s %6s %4s | %5s %5s %5s | %6s | %-14s\n" \
    "TIME(UTC)" "CPU" "TEMP" "LOAD" "RAM" "IDLE" "SRV" "WKR" "DELTA" "EVENT"
echo "  ────────────────────────────────────────────────────────────────────────"

# ── State ─────────────────────────────────────────────────────
prev_idle=-1
iteration=0
watchdog_count=0
peak_idle=0
sum_idle=0
start_time=$(date +%s)
last_watchdog_time=$start_time
last_detail_time=0

# ── Summary on exit ───────────────────────────────────────────
cleanup() {
    local elapsed=$(( $(date +%s) - start_time ))
    local avg_idle=0
    [ "$iteration" -gt 0 ] && avg_idle=$(( sum_idle / iteration ))
    echo ""
    echo -e "${BLD}  ════════════════════════════════════════════════${NC}"
    printf  "  Duration:        %dm %ds\n"   "$(( elapsed/60 ))" "$(( elapsed%60 ))"
    printf  "  Main samples:    %d\n"         "$iteration"
    printf  "  Peak idle conns: %d / 500\n"   "$peak_idle"
    printf  "  Avg idle conns:  %d\n"         "$avg_idle"
    printf  "  Watchdog fires:  %d\n"         "$watchdog_count"
    echo ""
    echo -e "  ${CYN}Main CSV   :${NC} $MAIN_CSV"
    echo -e "  ${CYN}Detail CSV :${NC} $DETAIL_CSV"
    echo ""
    echo -e "  ${GRN}Generate report:${NC}"
    echo    "  python3 ~/anchortak_report.py $MAIN_CSV $DETAIL_CSV"
    echo ""
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── Main loop ─────────────────────────────────────────────────
while [ $iteration -lt $MAX_ITER ]; do

    TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    NOW=$(date +%s)
    ELAPSED=$(( NOW - start_time ))

    CPU=$(get_cpu_pct)
    TEMP=$(get_temp)
    RAM=$(get_ram_pct)
    read LOAD1 LOAD5 LOAD15 _ < /proc/loadavg

    IFS=',' read IDLE_T IDLE_S IDLE_W IDLE_O ACTIVE IDLE_TX \
        <<< "$(get_conn_primary)"

    # Delta — skip on first sample (prev_idle=-1)
    DELTA=0
    if [ "$prev_idle" -ge 0 ] 2>/dev/null && \
       [[ "$IDLE_T" =~ ^[0-9]+$ ]]; then
        DELTA=$(( IDLE_T - prev_idle ))
    fi

    # Peaks
    [[ "$IDLE_T" =~ ^[0-9]+$ ]] && [ "$IDLE_T" -gt "$peak_idle" ] && peak_idle=$IDLE_T
    [[ "$IDLE_T" =~ ^[0-9]+$ ]] && sum_idle=$(( sum_idle + IDLE_T ))

    # Event detection
    EVENT=""; NOTES=""
    if [ "$prev_idle" -ge 0 ] 2>/dev/null && \
       [[ "$IDLE_T" =~ ^[0-9]+$ ]]; then
        DROP=$(( prev_idle - IDLE_T ))
        if [ "$DROP" -gt 50 ]; then
            EVENT="WATCHDOG_FIRE"
            CYCLE=$(( NOW - last_watchdog_time ))
            NOTES="drop:${DROP},from:${prev_idle},cycle_sec:${CYCLE}"
            watchdog_count=$(( watchdog_count + 1 ))
            last_watchdog_time=$NOW
        fi
    fi
    if [ -z "$EVENT" ] && [[ "$IDLE_T" =~ ^[0-9]+$ ]]; then
        [ "$IDLE_T" -ge 480 ] && EVENT="CRITICAL"
        [ "$IDLE_T" -ge 350 ] && [ "$IDLE_T" -lt 480 ] && EVENT="VERY_HIGH"
        [ "$IDLE_T" -ge 200 ] && [ "$IDLE_T" -lt 350 ] && EVENT="HIGH"
    fi
    [[ "$CPU" =~ ^[0-9]+$ ]] && [ "$CPU" -ge 30 ] && \
        NOTES="${NOTES:+$NOTES,}cpu_spike:${CPU}pct"

    # Write main CSV
    printf '"%s",%d,%d,%s,%s,%s,%s,%d,%s,%s,%s,%s,%s,%s,%d,"%s","%s"\n' \
        "$TS" "$ELAPSED" "$CPU" "$TEMP" \
        "$LOAD1" "$LOAD5" "$LOAD15" "$RAM" \
        "$IDLE_T" "$IDLE_S" "$IDLE_W" "$IDLE_O" \
        "$ACTIVE" "$IDLE_TX" "$DELTA" "$EVENT" "$NOTES" >> "$MAIN_CSV"

    # Detail every 30s
    DMARK=" "
    if [ $(( NOW - last_detail_time )) -ge $DETAIL_EVERY ]; then
        run_detail "$TS" "$ELAPSED"
        last_detail_time=$NOW
        DMARK="◆"
    fi

    # Display colors
    if   [[ "$IDLE_T" =~ ^[0-9]+$ ]] && [ "$IDLE_T" -ge 400 ]; then CC="$RED"
    elif [[ "$IDLE_T" =~ ^[0-9]+$ ]] && [ "$IDLE_T" -ge 200 ]; then CC="$YLW"
    else CC="$GRN"; fi
    case "$EVENT" in
        WATCHDOG_FIRE) EC="$CYN" ;;
        CRITICAL|VERY_HIGH) EC="$RED" ;;
        HIGH) EC="$YLW" ;;
        *) EC="$NC" ;;
    esac

    # Delta sign
    DSIGN=" "
    if [[ "$DELTA" =~ ^-?[0-9]+$ ]] && [ "$prev_idle" -ge 0 ] 2>/dev/null; then
        [ "$DELTA" -gt 0 ] && DSIGN="+"
        [ "$DELTA" -lt 0 ] && DSIGN="-" && DELTA=${DELTA#-}
        [ "$DELTA" -eq 0 ] && DSIGN=" "
    fi

    printf "  %-24s %3s%% %3s°C %6s %3s%% | ${CC}%5s %5s %5s${NC} | %s%4s%s | ${EC}%-14s${NC} %s\n" \
        "$TS" "$CPU" "$TEMP" "$LOAD1" "$RAM" \
        "$IDLE_T" "$IDLE_S" "$IDLE_W" \
        "$DSIGN" "$DELTA" "$DMARK" \
        "$EVENT" "${NOTES:0:40}"

    prev_idle="$IDLE_T"
    iteration=$(( iteration + 1 ))
    sleep $(( INTERVAL - 1 ))
done

cleanup
