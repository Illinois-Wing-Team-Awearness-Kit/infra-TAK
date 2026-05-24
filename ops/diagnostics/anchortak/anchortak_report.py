#!/usr/bin/env python3
"""
AnchorTAK Diagnostic Report Generator v2.0
Analyzes collected monitoring data and presents findings WITHOUT
pre-determined conclusions. Data speaks for itself.

Usage: python3 anchortak_report.py <main_csv> <detail_csv>
"""

import sys
import csv
import json
import os
from datetime import datetime, timezone

# ── Load CSVs ────────────────────────────────────────────────

def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline='') as f:
        return list(csv.DictReader(f))

# ── Numeric helper ────────────────────────────────────────────

def N(v, default=0):
    try: return int(float(str(v).strip()))
    except: return default

def F(v, default=0.0):
    try: return float(str(v).strip())
    except: return default

# ── Compute main statistics ───────────────────────────────────

def analyze_main(rows):
    if not rows:
        return {}

    idle_vals  = [N(r['idle_total']) for r in rows if r['idle_total'] not in ('','ERR')]
    srv_vals   = [N(r['idle_server']) for r in rows if r['idle_server'] not in ('','ERR')]
    wkr_vals   = [N(r['idle_worker']) for r in rows if r['idle_worker'] not in ('','ERR')]
    cpu_vals   = [N(r['cpu_pct']) for r in rows if r.get('cpu_pct','') not in ('','ERR')]
    delta_vals = [N(r['delta_idle']) for r in rows if r.get('delta_idle','') not in ('','ERR','0')]

    # Watchdog events
    watchdogs = [r for r in rows if r.get('event') == 'WATCHDOG_FIRE']
    cycles = []
    for r in watchdogs:
        for part in r.get('notes','').split(','):
            if part.startswith('cycle_sec:'):
                try: cycles.append(int(part.split(':')[1]))
                except: pass

    # Accumulation rate: only positive deltas (when connections are growing)
    pos_deltas = [d for d in delta_vals if d > 0]
    neg_deltas = [d for d in delta_vals if d < 0]

    duration_sec = N(rows[-1]['elapsed_sec']) - N(rows[0]['elapsed_sec']) if len(rows) > 1 else 0

    return {
        'total_samples': len(rows),
        'duration_sec':  duration_sec,
        'duration_min':  round(duration_sec / 60, 1),
        'first_ts':      rows[0]['timestamp_utc'],
        'last_ts':       rows[-1]['timestamp_utc'],

        'idle_min':  min(idle_vals) if idle_vals else 0,
        'idle_max':  max(idle_vals) if idle_vals else 0,
        'idle_avg':  round(sum(idle_vals)/len(idle_vals), 1) if idle_vals else 0,
        'idle_vals': idle_vals,

        'srv_avg':   round(sum(srv_vals)/len(srv_vals), 1) if srv_vals else 0,
        'wkr_avg':   round(sum(wkr_vals)/len(wkr_vals), 1) if wkr_vals else 0,
        'srv_vals':  srv_vals,
        'wkr_vals':  wkr_vals,

        'cpu_max':   max(cpu_vals) if cpu_vals else 0,
        'cpu_avg':   round(sum(cpu_vals)/len(cpu_vals), 1) if cpu_vals else 0,

        'watchdog_count':   len(watchdogs),
        'watchdog_events':  watchdogs,
        'avg_cycle_sec':    round(sum(cycles)/len(cycles), 0) if cycles else 0,
        'min_cycle_sec':    min(cycles) if cycles else 0,
        'max_cycle_sec':    max(cycles) if cycles else 0,

        'avg_growth_rate':  round(sum(pos_deltas)/len(pos_deltas)/5, 2) if pos_deltas else 0,
        'growth_events':    len(pos_deltas),
        'decay_events':     len(neg_deltas),
    }

# ── Compute detail statistics ─────────────────────────────────

def analyze_detail(rows):
    if not rows:
        return {}

    def safe_sum(col):
        return sum(N(r.get(col, 0)) for r in rows)

    def safe_avg(col):
        vals = [N(r.get(col, 0)) for r in rows]
        return round(sum(vals)/len(vals), 1) if vals else 0

    total_idle_samples = sum(
        N(r.get('qt_license_srv',0)) + N(r.get('qt_license_wkr',0)) +
        N(r.get('qt_listen_srv',0))  + N(r.get('qt_listen_wkr',0)) +
        N(r.get('qt_commit_srv',0))  + N(r.get('qt_commit_wkr',0)) +
        N(r.get('qt_cache_other_srv',0)) + N(r.get('qt_cache_other_wkr',0)) +
        N(r.get('qt_empty_srv',0))   + N(r.get('qt_empty_wkr',0))
        for r in rows
    )

    lic_total = safe_sum('qt_license_srv') + safe_sum('qt_license_wkr')
    listen_total = safe_sum('qt_listen_srv') + safe_sum('qt_listen_wkr')
    commit_total = safe_sum('qt_commit_srv') + safe_sum('qt_commit_wkr')
    other_total  = safe_sum('qt_cache_other_srv') + safe_sum('qt_cache_other_wkr')
    empty_total  = safe_sum('qt_empty_srv') + safe_sum('qt_empty_wkr')

    def pct(n):
        if total_idle_samples == 0: return 0
        return round(n * 100 / total_idle_samples, 1)

    # Age distribution averages
    age_lt10  = safe_avg('age_lt10s')
    age_10_30 = safe_avg('age_10_30s')
    age_30_60 = safe_avg('age_30_60s')
    age_60_300= safe_avg('age_60_300s')
    age_gt300 = safe_avg('age_gt300s')
    total_age = age_lt10 + age_10_30 + age_30_60 + age_60_300 + age_gt300

    def age_pct(n):
        if total_age == 0: return 0
        return round(n * 100 / total_age, 1)

    # App name: blank = async pool
    blank_avg = safe_avg('appname_blank')
    named_avg = safe_avg('appname_named')
    total_an  = blank_avg + named_avg

    return {
        'detail_samples': len(rows),

        # Query type breakdown
        'lic_srv':    safe_avg('qt_license_srv'),
        'lic_wkr':    safe_avg('qt_license_wkr'),
        'listen_srv': safe_avg('qt_listen_srv'),
        'listen_wkr': safe_avg('qt_listen_wkr'),
        'commit_srv': safe_avg('qt_commit_srv'),
        'commit_wkr': safe_avg('qt_commit_wkr'),
        'other_srv':  safe_avg('qt_cache_other_srv'),
        'other_wkr':  safe_avg('qt_cache_other_wkr'),
        'empty_srv':  safe_avg('qt_empty_srv'),
        'empty_wkr':  safe_avg('qt_empty_wkr'),

        'pct_license': pct(lic_total),
        'pct_listen':  pct(listen_total),
        'pct_commit':  pct(commit_total),
        'pct_other':   pct(other_total),
        'pct_empty':   pct(empty_total),

        # App name
        'blank_avg':   round(blank_avg, 1),
        'named_avg':   round(named_avg, 1),
        'pct_blank':   round(blank_avg * 100 / total_an, 1) if total_an > 0 else 0,

        # Age distribution
        'age_lt10':    round(age_lt10, 1),
        'age_10_30':   round(age_10_30, 1),
        'age_30_60':   round(age_30_60, 1),
        'age_60_300':  round(age_60_300, 1),
        'age_gt300':   round(age_gt300, 1),

        'pct_age_lt10':   age_pct(age_lt10),
        'pct_age_10_30':  age_pct(age_10_30),
        'pct_age_30_60':  age_pct(age_30_60),
        'pct_age_60_300': age_pct(age_60_300),
        'pct_age_gt300':  age_pct(age_gt300),

        # Raw rows for charts
        'rows': rows,
    }

# ── Build chart data ──────────────────────────────────────────

def build_chart_data(main_rows, detail_rows):
    # Main timeline
    labels, idle, srv, wkr, cpu_d, delta_d = [], [], [], [], [], []
    annotations = []

    for i, r in enumerate(main_rows):
        labels.append(r['timestamp_utc'][11:19])
        idle.append(N(r['idle_total']) if r['idle_total'] not in ('','ERR') else None)
        srv.append(N(r['idle_server']) if r['idle_server'] not in ('','ERR') else None)
        wkr.append(N(r['idle_worker']) if r['idle_worker'] not in ('','ERR') else None)
        cpu_d.append(N(r['cpu_pct']) if r.get('cpu_pct','') not in ('','ERR') else None)
        delta_d.append(N(r['delta_idle']) if r.get('delta_idle','') not in ('','ERR') else None)

        if r.get('event') == 'WATCHDOG_FIRE':
            annotations.append({'type':'line','xMin':i,'xMax':i,
                'borderColor':'rgba(0,229,255,0.8)','borderWidth':2,
                'label':{'content':'⚡','display':True,'color':'#00e5ff',
                         'position':'start','font':{'size':10}}})
        elif r.get('event') in ('CRITICAL','VERY_HIGH'):
            annotations.append({'type':'point','xValue':i,
                'yValue':idle[i] or 0,
                'backgroundColor':'#ff1744','radius':4})

    # Detail timeline
    d_labels, d_lic, d_listen, d_commit, d_empty, d_lt10, d_10_30, d_30_60, d_60_300, d_gt300 \
        = [], [], [], [], [], [], [], [], [], []

    for r in detail_rows:
        d_labels.append(r['timestamp_utc'][11:19])
        d_lic.append(    N(r.get('qt_license_srv',0)) + N(r.get('qt_license_wkr',0)))
        d_listen.append( N(r.get('qt_listen_srv',0))  + N(r.get('qt_listen_wkr',0)))
        d_commit.append( N(r.get('qt_commit_srv',0))  + N(r.get('qt_commit_wkr',0)))
        d_empty.append(  N(r.get('qt_empty_srv',0))   + N(r.get('qt_empty_wkr',0)))
        d_lt10.append(   N(r.get('age_lt10s',0)))
        d_10_30.append(  N(r.get('age_10_30s',0)))
        d_30_60.append(  N(r.get('age_30_60s',0)))
        d_60_300.append( N(r.get('age_60_300s',0)))
        d_gt300.append(  N(r.get('age_gt300s',0)))

    return {
        'main_labels': labels,
        'idle': idle, 'srv': srv, 'wkr': wkr,
        'cpu': cpu_d, 'delta': delta_d,
        'annotations': annotations,

        'd_labels': d_labels,
        'd_lic': d_lic, 'd_listen': d_listen,
        'd_commit': d_commit, 'd_empty': d_empty,
        'd_lt10': d_lt10, 'd_10_30': d_10_30,
        'd_30_60': d_30_60, 'd_60_300': d_60_300,
        'd_gt300': d_gt300,
    }

# ── Observation generator (data-driven, no pre-set conclusions) ──

def build_observations(m, d):
    obs = []

    # Connection source
    if m.get('srv_avg', 0) > 0 or m.get('wkr_avg', 0) > 0:
        srv_pct = round(m['srv_avg'] * 100 / max(m['idle_avg'], 1), 0)
        wkr_pct = round(m['wkr_avg'] * 100 / max(m['idle_avg'], 1), 0)
        obs.append({
            'label': 'Connection Source',
            'value': f"Server (172.18.0.4): {m['srv_avg']} avg ({srv_pct}%) · "
                     f"Worker (172.18.0.5): {m['wkr_avg']} avg ({wkr_pct}%)",
            'note':  'Which container is the primary accumulation source?'
        })

    if d:
        # Query type
        obs.append({
            'label': 'Dominant Query Type (% of idle connections)',
            'value': (f"license_cache: {d['pct_license']}% · "
                      f"channels_listen: {d['pct_listen']}% · "
                      f"commit: {d['pct_commit']}% · "
                      f"empty: {d['pct_empty']}%"),
            'note': 'Which query is responsible for most idle connections?'
        })

        # App name
        obs.append({
            'label': 'application_name (async vs sync)',
            'value': f"blank (async pool): {d['blank_avg']} avg ({d['pct_blank']}%) · "
                     f"named (sync ORM): {d['named_avg']} avg",
            'note':  'blank=async psycopg_pool (CONN_MAX_AGE ineffective) vs named=Django ORM sync'
        })

        # Age distribution
        obs.append({
            'label': 'Avg connection age distribution',
            'value': (f"<10s: {d['age_lt10']} ({d['pct_age_lt10']}%) · "
                      f"10-30s: {d['age_10_30']} ({d['pct_age_10_30']}%) · "
                      f"30-60s: {d['age_30_60']} ({d['pct_age_30_60']}%) · "
                      f"1-5min: {d['age_60_300']} ({d['pct_age_60_300']}%) · "
                      f">5min: {d['age_gt300']} ({d['pct_age_gt300']}%)"),
            'note': 'If CONN_MAX_AGE=10 is working, <10s bucket should dominate. '
                    'Old connections (>60s) confirm async pool leak.'
        })

    # Accumulation rate
    if m.get('avg_growth_rate', 0) > 0:
        obs.append({
            'label': 'Average accumulation rate',
            'value': f"{m['avg_growth_rate']} connections/sec (during growth phases)",
            'note': 'Constant rate = background process. Bursty = request-correlated.'
        })

    # Watchdog cycle
    if m.get('watchdog_count', 0) > 0:
        obs.append({
            'label': 'Watchdog cycle',
            'value': (f"Fires: {m['watchdog_count']} · "
                      f"Avg cycle: {m['avg_cycle_sec']}s · "
                      f"Range: {m['min_cycle_sec']}s–{m['max_cycle_sec']}s"),
            'note': 'Consistent cycle time confirms a deterministic accumulation process.'
        })

    return obs


# ── Generate HTML ─────────────────────────────────────────────

def generate_html(main_path, detail_path, main_rows, detail_rows, m, d, charts):
    generated_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    obs = build_observations(m, d)
    obs_html = ''
    for o in obs:
        obs_html += f"""
      <tr>
        <td style="color:var(--muted);white-space:nowrap;padding-right:24px">{o['label']}</td>
        <td style="font-family:monospace;font-size:12px">{o['value']}</td>
        <td style="color:var(--muted);font-size:11px;padding-left:16px">{o['note']}</td>
      </tr>"""

    # Watchdog event table
    wd_html = ''
    for r in m.get('watchdog_events', []):
        wd_html += f"<tr><td>{r['timestamp_utc']}</td><td>{r['elapsed_sec']}s</td>" \
                   f"<td>{r.get('notes','')}</td></tr>"
    if not wd_html:
        wd_html = '<tr><td colspan="3" style="color:var(--muted);text-align:center;padding:12px">No watchdog events</td></tr>'

    charts_json = json.dumps(charts)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AnchorTAK Diagnostic Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
<style>
:root{{
  --bg:#0c0e14;--surface:#131720;--border:#1a2030;
  --text:#dde4f0;--muted:#5a6a88;
  --cyan:#00e5ff;--green:#00e676;--yellow:#ffd740;--red:#ff1744;--blue:#448aff;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:'Courier New',monospace;font-size:13px;line-height:1.6}}
.hdr{{background:var(--surface);border-bottom:1px solid var(--border);padding:20px 32px;
       display:flex;justify-content:space-between;align-items:center}}
.hdr h1{{font-size:16px;color:var(--cyan);letter-spacing:.06em;text-transform:uppercase}}
.hdr p{{color:var(--muted);font-size:11px;margin-top:3px}}
.content{{max-width:1440px;margin:0 auto;padding:24px 32px}}
.row{{display:grid;gap:12px;margin-bottom:20px}}
.row-4{{grid-template-columns:repeat(4,1fr)}}
.row-2{{grid-template-columns:repeat(2,1fr)}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:5px;padding:16px}}
.card.accent-l{{border-left:3px solid var(--cyan)}}
.kv-label{{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}}
.kv-val{{font-size:28px;font-weight:700;margin-top:2px}}
.kv-sub{{font-size:11px;color:var(--muted)}}
.section{{font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:var(--cyan);
           margin:24px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--border)}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:left;color:var(--muted);font-size:10px;text-transform:uppercase;
     letter-spacing:.08em;padding:8px 12px;border-bottom:1px solid var(--border)}}
td{{padding:7px 12px;border-bottom:1px solid rgba(26,32,48,.6)}}
tr:hover td{{background:rgba(255,255,255,.02)}}
.obs-note{{font-style:italic;color:var(--muted)}}
.chart-wrap{{background:var(--surface);border:1px solid var(--border);border-radius:5px;
              padding:16px 20px;margin-bottom:16px}}
.chart-label{{font-size:10px;text-transform:uppercase;letter-spacing:.1em;
               color:var(--muted);margin-bottom:12px}}
.footer{{border-top:1px solid var(--border);padding:14px 32px;color:var(--muted);
          font-size:10px;text-align:center;margin-top:32px}}
.warn{{color:var(--yellow)}} .ok{{color:var(--green)}} .crit{{color:var(--red)}}
.highlight{{color:var(--cyan)}}
</style>
</head>
<body>
<div class="hdr">
  <div>
    <h1>AnchorTAK Diagnostic Monitor Report</h1>
    <p>anctakserver2 &nbsp;·&nbsp; infra-TAK v0.9.22 &nbsp;·&nbsp; Authentik 2026.2.3
       &nbsp;·&nbsp; {m.get('first_ts','')} → {m.get('last_ts','')}</p>
  </div>
  <div style="text-align:right;color:var(--muted);font-size:11px">
    Generated: {generated_at}<br>
    Main: {os.path.basename(main_path)}<br>
    Detail: {os.path.basename(detail_path) if detail_path else 'N/A'}
  </div>
</div>

<div class="content">

<!-- KPI row -->
<div class="row row-4" style="margin-top:20px">
  <div class="card"><div class="kv-label">Duration</div>
    <div class="kv-val highlight">{m.get('duration_min',0)}</div>
    <div class="kv-sub">minutes captured ({m.get('total_samples',0)} samples)</div></div>
  <div class="card"><div class="kv-label">Peak Idle Connections</div>
    <div class="kv-val {'crit' if m.get('idle_max',0)>=480 else 'warn' if m.get('idle_max',0)>=200 else 'ok'}">{m.get('idle_max',0)}</div>
    <div class="kv-sub">of 500 ceiling · avg {m.get('idle_avg',0)}</div></div>
  <div class="card"><div class="kv-label">Watchdog Fires</div>
    <div class="kv-val highlight">{m.get('watchdog_count',0)}</div>
    <div class="kv-sub">avg cycle {m.get('avg_cycle_sec',0)}s · range {m.get('min_cycle_sec',0)}–{m.get('max_cycle_sec',0)}s</div></div>
  <div class="card"><div class="kv-label">Accum Rate (growth)</div>
    <div class="kv-val">{m.get('avg_growth_rate',0)}</div>
    <div class="kv-sub">connections/sec during accumulation</div></div>
</div>

{'<div class="row row-4"><div class="card"><div class="kv-label">License Cache (avg)</div><div class="kv-val">' + str(d.get("pct_license",0)) + '%</div><div class="kv-sub">of idle connections</div></div><div class="card"><div class="kv-label">LISTEN / channels</div><div class="kv-val">' + str(d.get("pct_listen",0)) + '%</div><div class="kv-sub">of idle connections</div></div><div class="card"><div class="kv-label">Async Pool (blank name)</div><div class="kv-val">' + str(d.get("pct_blank",0)) + '%</div><div class="kv-sub">of idle connections</div></div><div class="card"><div class="kv-label">Age &gt;60s (CONN_MAX_AGE?)</div><div class="kv-val ' + ("crit" if (d.get("pct_age_60_300",0)+d.get("pct_age_gt300",0))>20 else "ok") + '">' + str(round(d.get("pct_age_60_300",0)+d.get("pct_age_gt300",0),1)) + '%</div><div class="kv-sub">should be ~0 if CONN_MAX_AGE=10 works</div></div></div>' if d else ''}

<!-- Observations table -->
<div class="section">Observed Data Points</div>
<div class="card">
  <table>
    <thead><tr><th>Metric</th><th>Value</th><th>Interpretation Guide</th></tr></thead>
    <tbody>{obs_html}</tbody>
  </table>
</div>

<!-- Main connection chart -->
<div class="section">Connection Timeline (5s interval)</div>
<div class="chart-wrap">
  <div class="chart-label">Idle connections — total / server (172.18.0.4) / worker (172.18.0.5)
    &nbsp;·&nbsp; ⚡ = watchdog fire</div>
  <canvas id="cMain" height="90"></canvas>
</div>

<!-- CPU chart -->
<div class="chart-wrap">
  <div class="chart-label">CPU % &amp; delta connections per 5s interval</div>
  <canvas id="cCpu" height="55"></canvas>
</div>

{'<!-- Query breakdown chart --><div class="section">Query Type Breakdown (30s detail snapshots)</div><div class="chart-wrap"><div class="chart-label">Idle connection composition by last query</div><canvas id="cQtype" height="60"></canvas></div>' if d and d.get('rows') else ''}

{'<!-- Age distribution chart --><div class="chart-wrap"><div class="chart-label">Connection age distribution — does CONN_MAX_AGE=10 eliminate connections before 30s?</div><canvas id="cAge" height="60"></canvas></div>' if d and d.get('rows') else ''}

<!-- Watchdog event log -->
<div class="section">Watchdog Event Log</div>
<div class="card" style="padding:0;overflow:hidden">
  <table>
    <thead><tr><th>Timestamp UTC</th><th>Elapsed</th><th>Details</th></tr></thead>
    <tbody>{wd_html}</tbody>
  </table>
</div>

<!-- Raw data note -->
<div class="section">Raw Data Files</div>
<div class="card accent-l">
  <p style="margin-bottom:8px"><strong>Main CSV:</strong> <span style="color:var(--cyan)">{main_path}</span></p>
  <p><strong>Detail CSV:</strong> <span style="color:var(--cyan)">{detail_path if detail_path else 'not provided'}</span></p>
  <p style="margin-top:12px;color:var(--muted);font-size:11px">
    All raw data is available in the CSV files above. Analysis and conclusions
    should be drawn after reviewing the full dataset. No pre-written conclusions
    are included in this report — the data speaks for itself.
  </p>
</div>

</div><!-- /content -->
<div class="footer">AnchorTAK Diagnostic Monitor v2.0 &nbsp;·&nbsp; {generated_at}</div>

<script>
const C = {charts_json};

// Connection timeline
new Chart(document.getElementById('cMain'),{{
  type:'line',
  data:{{
    labels:C.main_labels,
    datasets:[
      {{label:'Idle Total',data:C.idle,borderColor:'#00e5ff',backgroundColor:'rgba(0,229,255,.07)',
        borderWidth:1.5,pointRadius:0,fill:true,tension:.1}},
      {{label:'Server',data:C.srv,borderColor:'#ffd740',backgroundColor:'transparent',
        borderWidth:1,pointRadius:0,fill:false,tension:.1}},
      {{label:'Worker',data:C.wkr,borderColor:'#ff6b35',backgroundColor:'transparent',
        borderWidth:1,pointRadius:0,fill:false,tension:.1,borderDash:[3,2]}}
    ]
  }},
  options:{{responsive:true,
    plugins:{{legend:{{labels:{{color:'#5a6a88',font:{{size:10}}}}}},
              annotation:{{annotations:C.annotations}}}},
    scales:{{
      x:{{ticks:{{color:'#5a6a88',maxTicksLimit:24,maxRotation:0,font:{{size:10}}}},
           grid:{{color:'rgba(255,255,255,.03)'}}}},
      y:{{min:0,max:520,ticks:{{color:'#5a6a88',font:{{size:10}}}},
           grid:{{color:'rgba(255,255,255,.03)'}},
           title:{{display:true,text:'Idle Connections',color:'#5a6a88',font:{{size:10}}}}}}
    }}
  }}
}});

// CPU + delta chart
new Chart(document.getElementById('cCpu'),{{
  type:'line',
  data:{{
    labels:C.main_labels,
    datasets:[
      {{label:'CPU %',data:C.cpu,borderColor:'#ffd740',backgroundColor:'rgba(255,215,64,.06)',
        borderWidth:1.5,pointRadius:0,fill:true,tension:.2,yAxisID:'yCPU'}},
      {{label:'Δ Connections',data:C.delta,borderColor:'#00e676',backgroundColor:'transparent',
        borderWidth:1,pointRadius:0,fill:false,tension:.1,yAxisID:'yDelta',borderDash:[2,2]}}
    ]
  }},
  options:{{responsive:true,
    plugins:{{legend:{{labels:{{color:'#5a6a88',font:{{size:10}},boxWidth:12}}}}}},
    scales:{{
      x:{{ticks:{{color:'#5a6a88',maxTicksLimit:24,maxRotation:0,font:{{size:10}}}},
           grid:{{color:'rgba(255,255,255,.03)'}}}},
      yCPU:{{min:0,max:100,ticks:{{color:'#ffd740',font:{{size:10}}}},
              grid:{{color:'rgba(255,255,255,.03)'}},
              title:{{display:true,text:'CPU %',color:'#ffd740',font:{{size:10}}}}}},
      yDelta:{{position:'right',ticks:{{color:'#00e676',font:{{size:10}}}},
                grid:{{drawOnChartArea:false}},
                title:{{display:true,text:'Δ Conns',color:'#00e676',font:{{size:10}}}}}}
    }}
  }}
}});

// Query type stacked
if(document.getElementById('cQtype')){{
  new Chart(document.getElementById('cQtype'),{{
    type:'bar',
    data:{{
      labels:C.d_labels,
      datasets:[
        {{label:'license_cache',data:C.d_lic,backgroundColor:'#ff1744',stack:'q'}},
        {{label:'channels_listen',data:C.d_listen,backgroundColor:'#448aff',stack:'q'}},
        {{label:'commit',data:C.d_commit,backgroundColor:'#ffd740',stack:'q'}},
        {{label:'empty',data:C.d_empty,backgroundColor:'#5a6a88',stack:'q'}}
      ]
    }},
    options:{{responsive:true,
      plugins:{{legend:{{labels:{{color:'#5a6a88',font:{{size:10}},boxWidth:12}}}}}},
      scales:{{
        x:{{stacked:true,ticks:{{color:'#5a6a88',maxTicksLimit:20,font:{{size:10}}}},
             grid:{{color:'rgba(255,255,255,.03)'}}}},
        y:{{stacked:true,ticks:{{color:'#5a6a88',font:{{size:10}}}},
             grid:{{color:'rgba(255,255,255,.03)'}},
             title:{{display:true,text:'Count',color:'#5a6a88',font:{{size:10}}}}}}
      }}
    }}
  }});
}}

// Age distribution stacked
if(document.getElementById('cAge')){{
  new Chart(document.getElementById('cAge'),{{
    type:'bar',
    data:{{
      labels:C.d_labels,
      datasets:[
        {{label:'<10s',data:C.d_lt10,backgroundColor:'#00e676',stack:'a'}},
        {{label:'10-30s',data:C.d_10_30,backgroundColor:'#ffd740',stack:'a'}},
        {{label:'30-60s',data:C.d_30_60,backgroundColor:'#ff6b35',stack:'a'}},
        {{label:'1-5min',data:C.d_60_300,backgroundColor:'#ff1744',stack:'a'}},
        {{label:'>5min',data:C.d_gt300,backgroundColor:'#880e4f',stack:'a'}}
      ]
    }},
    options:{{responsive:true,
      plugins:{{legend:{{labels:{{color:'#5a6a88',font:{{size:10}},boxWidth:12}}}}}},
      scales:{{
        x:{{stacked:true,ticks:{{color:'#5a6a88',maxTicksLimit:20,font:{{size:10}}}},
             grid:{{color:'rgba(255,255,255,.03)'}}}},
        y:{{stacked:true,ticks:{{color:'#5a6a88',font:{{size:10}}}},
             grid:{{color:'rgba(255,255,255,.03)'}},
             title:{{display:true,text:'Count',color:'#5a6a88',font:{{size:10}}}}}}
      }}
    }}
  }});
}}
</script>
</body>
</html>"""
    return html


# ── Main ──────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 anchortak_report.py <main_csv> [detail_csv]")
        sys.exit(1)

    main_path   = sys.argv[1]
    detail_path = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Loading {main_path}...")
    main_rows = load_csv(main_path)
    print(f"  {len(main_rows)} main samples")

    detail_rows = []
    if detail_path:
        print(f"Loading {detail_path}...")
        detail_rows = load_csv(detail_path)
        print(f"  {len(detail_rows)} detail snapshots")

    m = analyze_main(main_rows)
    d = analyze_detail(detail_rows) if detail_rows else {}

    print(f"\nData summary:")
    print(f"  Duration:          {m.get('duration_min',0)} min")
    print(f"  Peak idle:         {m.get('idle_max',0)} / 500")
    print(f"  Watchdog fires:    {m.get('watchdog_count',0)}")
    print(f"  Avg cycle:         {m.get('avg_cycle_sec',0)}s")
    if d:
        print(f"  License cache %:   {d.get('pct_license',0)}%")
        print(f"  Async pool %:      {d.get('pct_blank',0)}%")
        print(f"  Age >60s %:        {round(d.get('pct_age_60_300',0)+d.get('pct_age_gt300',0),1)}%")

    charts = build_chart_data(main_rows, detail_rows)
    html   = generate_html(main_path, detail_path or '', main_rows, detail_rows, m, d, charts)

    out = main_path.replace('.csv', '_report.html')
    with open(out, 'w') as f:
        f.write(html)

    print(f"\n  Report: {out}")
    print("  Open in browser to view charts and data.")

if __name__ == '__main__':
    main()
