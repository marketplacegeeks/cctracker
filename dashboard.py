"""Token consumption dashboard for cctracker — served via FastAPI."""

import json
import sqlite3
import webbrowser
from datetime import date, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

import base64

DB_PATH = Path.home() / ".cctracker" / "sessions.db"
PORT = 7821

_LOGO_PATH = Path(__file__).parent / "logo.png"
_LOGO_B64 = (
    "data:image/png;base64,"
    + base64.b64encode(_LOGO_PATH.read_bytes()).decode()
) if _LOGO_PATH.exists() else None


def _set_dock_icon():
    """Set the macOS Dock icon to logo.png (requires pyobjc)."""
    try:
        from AppKit import NSApplication, NSImage
        ns_app = NSApplication.sharedApplication()
        ns_app.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory — shows in Dock
        icon = NSImage.alloc().initWithContentsOfFile_(str(_LOGO_PATH))
        if icon:
            ns_app.setApplicationIconImage_(icon)
    except Exception as e:
        print(f"[dock icon] {e}")


from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(_app):
    import threading
    threading.Thread(target=_set_dock_icon, daemon=True).start()
    yield

app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db():
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _color(n: int) -> str:
    if n == 0:
        return "#6B7280"
    if n < 50_000:
        return "#10B981"
    if n < 500_000:
        return "#F59E0B"
    if n < 2_000_000:
        return "#F97316"
    return "#EF4444"


def _abbrev_cwd(cwd: str) -> str:
    """Return the last 2 path components as a short label, e.g. 'Docs_Content/AI Content'."""
    if not cwd:
        return ""
    parts = [p for p in cwd.replace("\\", "/").split("/") if p]
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1] if parts else cwd


def _shorten_model(m: str) -> str:
    return (
        m.replace("claude-sonnet-4-6", "Sonnet 4.6")
         .replace("claude-opus-4-6", "Opus 4.6")
         .replace("claude-haiku-4-5-20251001", "Haiku 4.5")
         .replace("claude-", "")
    )


# ── data fetch ────────────────────────────────────────────────────────────────

def _fetch_data(account: str | None = None):
    conn = _db()
    if conn is None:
        return None

    today_str   = date.today().isoformat()
    week_ago    = (date.today() - timedelta(days=6)).isoformat()
    month_ago   = (date.today() - timedelta(days=29)).isoformat()

    # Build account filter clause — values are internal constants, safe to inline
    af = f"AND account = '{account}'" if account else ""

    def _sum(since: str) -> int:
        r = conn.execute(
            f"SELECT COALESCE(SUM(total_tokens),0) FROM sessions "
            f"WHERE total_tokens > 0 AND date >= ? {af}", (since,)
        ).fetchone()
        return r[0]

    today_total = _sum(today_str)
    week_total  = _sum(week_ago)
    month_total = _sum(month_ago)
    all_time    = _sum("2000-01-01")

    # yesterday for delta badge
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    yesterday_total = conn.execute(
        f"SELECT COALESCE(SUM(total_tokens),0) FROM sessions "
        f"WHERE total_tokens > 0 AND date = ? {af}", (yesterday_str,)
    ).fetchone()[0]

    # 30-day daily chart
    daily_rows = conn.execute(f"""
        SELECT date,
               SUM(total_tokens)  AS total,
               SUM(input_tokens)  AS inp,
               SUM(output_tokens) AS outp,
               COUNT(*)           AS sessions
        FROM sessions
        WHERE total_tokens > 0 AND date >= ? {af}
        GROUP BY date ORDER BY date
    """, (month_ago,)).fetchall()
    daily_map = {r["date"]: dict(r) for r in daily_rows}

    chart_labels, chart_totals, chart_inp, chart_outp, chart_sessions = [], [], [], [], []
    for i in range(30):
        d = (date.today() - timedelta(days=29 - i)).isoformat()
        row = daily_map.get(d, {})
        chart_labels.append(d[5:])           # MM-DD
        chart_totals.append(row.get("total", 0))
        chart_inp.append(row.get("inp", 0))
        chart_outp.append(row.get("outp", 0))
        chart_sessions.append(row.get("sessions", 0))

    # top projects (all time)
    proj_rows = conn.execute(f"""
        SELECT project,
               SUM(total_tokens)  AS total,
               COUNT(*)           AS sessions,
               CAST(AVG(total_tokens) AS INTEGER) AS avg_tok
        FROM sessions WHERE total_tokens > 0 {af}
        GROUP BY project ORDER BY total DESC LIMIT 12
    """).fetchall()

    # top projects last 7 days
    proj_week = conn.execute(f"""
        SELECT project, SUM(total_tokens) AS total, COUNT(*) AS sessions
        FROM sessions WHERE total_tokens > 0 AND date >= ? {af}
        GROUP BY project ORDER BY total DESC LIMIT 8
    """, (week_ago,)).fetchall()

    # recent sessions
    recent = conn.execute(f"""
        SELECT id, date, start_time, end_time, duration_minutes,
               input_tokens, output_tokens, total_tokens,
               project, model, account, achievement, cwd
        FROM sessions WHERE total_tokens > 0 {af}
        ORDER BY date DESC, start_time DESC LIMIT 40
    """).fetchall()

    # model breakdown
    model_rows = conn.execute(f"""
        SELECT model, SUM(total_tokens) AS total, COUNT(*) AS sessions
        FROM sessions WHERE total_tokens > 0 AND model NOT IN ('pending','unknown') {af}
        GROUP BY model ORDER BY total DESC
    """).fetchall()

    # hourly heatmap (hour of day vs tokens — last 30 days)
    hourly = conn.execute(f"""
        SELECT CAST(SUBSTR(start_time,1,2) AS INTEGER) AS hr,
               SUM(total_tokens) AS total,
               COUNT(*) AS sessions
        FROM sessions
        WHERE total_tokens > 0 AND date >= ? AND start_time IS NOT NULL {af}
        GROUP BY hr ORDER BY hr
    """, (month_ago,)).fetchall()
    hourly_map = {r["hr"]: {"total": r["total"], "sessions": r["sessions"]} for r in hourly}

    conn.close()

    # build rolling avg for chart
    rolling = []
    for i in range(len(chart_totals)):
        window = chart_totals[max(0, i - 4): i + 1]
        rolling.append(round(sum(window) / len(window)))

    return {
        "today_total": today_total,
        "yesterday_total": yesterday_total,
        "week_total": week_total,
        "month_total": month_total,
        "all_time": all_time,
        "chart_labels": chart_labels,
        "chart_totals": chart_totals,
        "chart_inp": chart_inp,
        "chart_outp": chart_outp,
        "chart_sessions": chart_sessions,
        "rolling": rolling,
        "proj_rows": [dict(r) for r in proj_rows],
        "proj_week": [dict(r) for r in proj_week],
        "recent": [dict(r) for r in recent],
        "model_rows": [dict(r) for r in model_rows],
        "hourly_map": hourly_map,
    }


# ── HTML sub-builders ─────────────────────────────────────────────────────────

def _cards_html(d: dict) -> str:
    today_delta = d["today_total"] - d["yesterday_total"]
    delta_sign  = "+" if today_delta >= 0 else ""
    delta_color = "#EF4444" if today_delta > 0 else "#10B981"

    def card(label, value, sub=""):
        return f"""
        <div class="card">
          <div class="card-label">{label}</div>
          <div class="card-value">{_fmt(value)}</div>
          {f'<div class="card-sub">{sub}</div>' if sub else ""}
        </div>"""

    return (
        card("Today", d["today_total"],
             f'<span style="color:{delta_color}">{delta_sign}{_fmt(abs(today_delta))} vs yesterday</span>') +
        card("This Week (7d)", d["week_total"]) +
        card("This Month (30d)", d["month_total"]) +
        card("All Time", d["all_time"])
    )


def _proj_table_html(d: dict) -> str:
    html = ""
    all_time = d["all_time"] or 1
    for r in d["proj_rows"]:
        pct = round(r["total"] / all_time * 100, 1) if all_time else 0
        html += f"""
        <tr>
          <td class="td-project">{r["project"] or "—"}</td>
          <td class="td-right">{_fmt(r["total"])}</td>
          <td class="td-right" style="color:#6B7280">{r["sessions"]}</td>
          <td class="td-right">{_fmt(r["avg_tok"])}</td>
          <td class="td-bar">
            <div class="bar-bg"><div class="bar-fill" style="width:{pct}%"></div></div>
            <span class="bar-pct">{pct}%</span>
          </td>
        </tr>"""
    return html or '<tr><td colspan="5" style="color:#6B7280;text-align:center;padding:20px">No data</td></tr>'


def _recent_html(d: dict) -> str:
    html = ""
    for r in d["recent"]:
        tok = r["total_tokens"] or 0
        col = _color(tok)
        inp = r["input_tokens"] or 0
        out = r["output_tokens"] or 0
        ratio = f"{round(inp/(inp+out)*100)}% in" if (inp + out) > 0 else "—"
        model = _shorten_model(r["model"] or "—")
        dur = f"{r['duration_minutes']}m" if r["duration_minutes"] else "—"
        ach = r["achievement"] or ""
        cwd_full = r["cwd"] or ""
        cwd_short = _abbrev_cwd(cwd_full)
        html += f"""
        <tr>
          <td class="td-dim">{r["date"]}</td>
          <td class="td-dim">{r["start_time"] or "—"}</td>
          <td>{r["project"] or "—"}</td>
          <td class="td-cwd td-dim" title="{cwd_full}">{cwd_short}</td>
          <td class="td-right" style="color:{col};font-weight:600">{_fmt(tok)}</td>
          <td class="td-right td-dim">{ratio}</td>
          <td class="td-right td-dim">{dur}</td>
          <td class="td-dim">{model}</td>
          <td class="td-dim" style="font-size:11px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{ach}</td>
        </tr>"""
    return html or '<tr><td colspan="9" style="color:#6B7280;text-align:center;padding:20px">No sessions</td></tr>'


def _heatmap_html(d: dict) -> str:
    html = ""
    hourly_map = d["hourly_map"]
    max_hr = max((hourly_map.get(h, {}).get("total", 0) for h in range(24)), default=1) or 1
    for h in range(24):
        row = hourly_map.get(h, {})
        t = row.get("total", 0)
        s = row.get("sessions", 0)
        intensity = int(t / max_hr * 255) if max_hr > 0 else 0
        alpha = round(0.05 + (intensity / 255) * 0.95, 2)
        label_h = f"{h:02d}:00"
        html += f"""
        <div class="hm-cell" title="{label_h}: {_fmt(t)} tokens, {s} sessions"
             style="background:rgba(59,130,246,{alpha})">
          <span class="hm-label">{h}</span>
        </div>"""
    return html


def _chart_js_data(d: dict) -> dict:
    proj_week_labels = [r["project"] or "?" for r in d["proj_week"]]
    proj_week_values = [r["total"] for r in d["proj_week"]]
    proj_week_colors = [
        "#3B82F6","#6366F1","#8B5CF6","#EC4899",
        "#F97316","#EAB308","#10B981","#14B8A6",
    ][:len(proj_week_labels)]
    model_labels = [_shorten_model(r["model"]) for r in d["model_rows"]]
    model_values = [r["total"] for r in d["model_rows"]]
    return {
        "daily": {
            "labels":   d["chart_labels"],
            "totals":   d["chart_totals"],
            "rolling":  d["rolling"],
        },
        "projWeek": {
            "labels": proj_week_labels,
            "values": proj_week_values,
            "colors": proj_week_colors,
        },
        "model": {
            "labels": model_labels,
            "values": model_values,
        },
    }


# ── HTML builder ──────────────────────────────────────────────────────────────

def _html(views: dict) -> str:
    # views keys: "all", "personal", "litellm"
    tab_defs = [
        ("all",      "All"),
        ("personal", "Personal"),
        ("litellm",  "LiteLLM"),
    ]

    # Pre-render per-view HTML blocks
    cards_blocks   = {k: _cards_html(views[k])      for k in views}
    proj_blocks    = {k: _proj_table_html(views[k])  for k in views}
    recent_blocks  = {k: _recent_html(views[k])      for k in views}
    heatmap_blocks = {k: _heatmap_html(views[k])     for k in views}
    chart_js       = {k: _chart_js_data(views[k])    for k in views}

    # Build tab buttons
    tab_buttons = ""
    for i, (key, label) in enumerate(tab_defs):
        active = " tab-active" if i == 0 else ""
        tab_buttons += f'<button class="tab-btn{active}" onclick="switchView(\'{key}\')" id="tab-{key}">{label}</button>'

    # Cards sections
    cards_html_all = ""
    for i, (key, _) in enumerate(tab_defs):
        display = "" if i == 0 else ' style="display:none"'
        cards_html_all += f'<div id="v-cards-{key}" class="cards" data-view="{key}"{display}>{cards_blocks[key]}</div>'

    # Proj table sections
    proj_html_all = ""
    for i, (key, _) in enumerate(tab_defs):
        display = "" if i == 0 else ' style="display:none"'
        proj_html_all += f'<tbody id="v-proj-{key}" data-view="{key}"{display}>{proj_blocks[key]}</tbody>'

    # Recent sections
    recent_html_all = ""
    for i, (key, _) in enumerate(tab_defs):
        display = "" if i == 0 else ' style="display:none"'
        recent_html_all += f'<tbody id="v-recent-{key}" data-view="{key}"{display}>{recent_blocks[key]}</tbody>'

    # Heatmap sections
    heatmap_html_all = ""
    for i, (key, _) in enumerate(tab_defs):
        display = "" if i == 0 else ' style="display:none"'
        heatmap_html_all += f'<div id="v-heatmap-{key}" class="heatmap" data-view="{key}"{display}>{heatmap_blocks[key]}</div>'

    chart_js_json = json.dumps(chart_js)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Token Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --bg:     #0F1117;
    --surface:#1A1D27;
    --border: #2A2D3E;
    --text:   #E2E8F0;
    --muted:  #6B7280;
    --blue:   #3B82F6;
    --red:    #EF4444;
    --green:  #10B981;
    --amber:  #F59E0B;
  }}
  body {{ background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:13px; min-height:100vh; }}

  /* Top bar */
  .topbar {{ background:var(--surface); border-bottom:1px solid var(--border); padding:0 24px; height:52px; display:flex; align-items:center; justify-content:space-between; position:sticky; top:0; z-index:10; }}
  .topbar-logo {{ display:flex; align-items:center; gap:10px; font-size:15px; font-weight:700; letter-spacing:-0.3px; }}
  .topbar-logo img {{ height:32px; width:32px; border-radius:7px; object-fit:cover; }}
  .topbar-logo span {{ color:var(--blue); }}
  .topbar-meta {{ font-size:11px; color:var(--muted); }}
  .refresh-btn {{ background:var(--border); border:none; color:var(--text); padding:5px 12px; border-radius:6px; cursor:pointer; font-size:12px; }}
  .refresh-btn:hover {{ background:#3A3D4E; }}

  /* Tab bar */
  .tab-bar {{ background:var(--surface); border-bottom:1px solid var(--border); padding:0 24px; display:flex; align-items:center; gap:2px; }}
  .tab-btn {{ background:none; border:none; color:var(--muted); padding:10px 16px; font-size:13px; font-weight:500; cursor:pointer; border-bottom:2px solid transparent; margin-bottom:-1px; transition:color 0.15s, border-color 0.15s; }}
  .tab-btn:hover {{ color:var(--text); }}
  .tab-btn.tab-active {{ color:var(--blue); border-bottom-color:var(--blue); }}

  /* Layout */
  .main {{ padding:20px 24px; max-width:1400px; margin:0 auto; }}
  .section-title {{ font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.8px; color:var(--muted); margin-bottom:12px; }}

  /* Summary cards */
  .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:20px; }}
  .card {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px 18px; }}
  .card-label {{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:0.6px; margin-bottom:6px; }}
  .card-value {{ font-size:28px; font-weight:700; letter-spacing:-0.5px; }}
  .card-sub {{ font-size:11px; margin-top:4px; }}

  /* Charts row */
  .charts-row {{ display:grid; grid-template-columns:2fr 1fr; gap:12px; margin-bottom:20px; }}
  .chart-card {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px 18px; }}
  .chart-wrap {{ position:relative; height:200px; }}

  /* Bottom row */
  .bottom-row {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:20px; }}

  /* Tables */
  table {{ width:100%; border-collapse:collapse; }}
  th {{ text-align:left; font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; color:var(--muted); padding:8px 10px; border-bottom:1px solid var(--border); }}
  td {{ padding:7px 10px; border-bottom:1px solid rgba(42,45,62,0.6); vertical-align:middle; }}
  tr:last-child td {{ border-bottom:none; }}
  tr:hover td {{ background:rgba(59,130,246,0.04); }}
  .td-right {{ text-align:right; }}
  .td-dim {{ color:var(--muted); }}
  .td-project {{ font-weight:500; max-width:160px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .td-cwd {{ font-size:11px; max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; cursor:default; }}
  .td-bar {{ display:flex; align-items:center; gap:6px; min-width:100px; }}
  .bar-bg {{ flex:1; height:4px; background:var(--border); border-radius:2px; overflow:hidden; }}
  .bar-fill {{ height:100%; background:var(--blue); border-radius:2px; }}
  .bar-pct {{ font-size:10px; color:var(--muted); min-width:32px; text-align:right; }}

  /* Heatmap */
  .heatmap {{ display:flex; gap:4px; flex-wrap:wrap; }}
  .hm-cell {{ width:36px; height:36px; border-radius:5px; display:flex; align-items:center; justify-content:center; cursor:default; border:1px solid var(--border); }}
  .hm-label {{ font-size:10px; color:rgba(226,232,240,0.7); font-weight:600; }}

  /* Full-width sessions */
  .sessions-card {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px 18px; }}
  .table-scroll {{ max-height:360px; overflow-y:auto; }}
  .table-scroll::-webkit-scrollbar {{ width:4px; }}
  .table-scroll::-webkit-scrollbar-track {{ background:transparent; }}
  .table-scroll::-webkit-scrollbar-thumb {{ background:var(--border); border-radius:2px; }}

  /* Model pills */
  .pill {{ display:inline-block; padding:2px 7px; border-radius:20px; font-size:10px; font-weight:600; background:var(--border); color:var(--muted); }}
</style>
</head>
<body>

<div class="topbar">
  <div class="topbar-logo">{"<img src='" + _LOGO_B64 + "' alt='logo'>" if _LOGO_B64 else ""}cc<span>tracker</span> &nbsp;·&nbsp; Token Dashboard</div>
  <div style="display:flex;gap:12px;align-items:center">
    <span class="topbar-meta" id="last-updated">Loading…</span>
    <button class="refresh-btn" onclick="location.reload()">↻ Refresh</button>
  </div>
</div>

<div class="tab-bar">
  {tab_buttons}
</div>

<div class="main">

  <!-- Summary cards (per view) -->
  <div class="section-title">Overview</div>
  {cards_html_all}

  <!-- 30-day chart + weekly projects -->
  <div class="charts-row">
    <div class="chart-card">
      <div class="section-title">Daily Token Usage — last 30 days</div>
      <div class="chart-wrap"><canvas id="dailyChart"></canvas></div>
    </div>
    <div class="chart-card">
      <div class="section-title">This Week by Project</div>
      <div class="chart-wrap"><canvas id="weekProjChart"></canvas></div>
    </div>
  </div>

  <!-- Projects table + hourly heatmap -->
  <div class="bottom-row">
    <div class="chart-card">
      <div class="section-title">All-Time — Top Projects</div>
      <div class="table-scroll">
        <table>
          <thead><tr>
            <th>Project</th><th class="td-right">Tokens</th>
            <th class="td-right">Sessions</th><th class="td-right">Avg/Session</th>
            <th>Share</th>
          </tr></thead>
          {proj_html_all}
        </table>
      </div>
    </div>
    <div style="display:flex;flex-direction:column;gap:12px;">
      <div class="chart-card" style="flex:1">
        <div class="section-title">Tokens by Model</div>
        <div class="chart-wrap" style="height:130px"><canvas id="modelChart"></canvas></div>
      </div>
      <div class="chart-card" style="flex:1">
        <div class="section-title">Peak Hours (last 30 days)</div>
        {heatmap_html_all}
      </div>
    </div>
  </div>

  <!-- Recent sessions -->
  <div class="sessions-card">
    <div class="section-title">Recent Sessions</div>
    <div class="table-scroll">
      <table>
        <thead><tr>
          <th>Date</th><th>Time</th><th>Project</th><th>Working Dir</th>
          <th class="td-right">Tokens</th><th class="td-right">Ratio</th>
          <th class="td-right">Dur</th><th>Model</th><th>Note</th>
        </tr></thead>
        {recent_html_all}
      </table>
    </div>
  </div>

</div><!-- /main -->

<script>
const chartData = {chart_js_json};
const gridColor = 'rgba(42,45,62,0.8)';
const textColor = '#6B7280';

Chart.defaults.color = textColor;
Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
Chart.defaults.font.size = 11;

let dailyChart, weekProjChart, modelChart;
let currentView = 'all';

// timestamp
document.getElementById('last-updated').textContent =
  'Updated ' + new Date().toLocaleTimeString();

function buildDailyChart(view) {{
  const cd = chartData[view].daily;
  return new Chart(document.getElementById('dailyChart'), {{
    data: {{
      labels: cd.labels,
      datasets: [
        {{
          type: 'bar',
          label: 'Total Tokens',
          data: cd.totals,
          backgroundColor: cd.totals.map(v =>
            v >= 50_000_000 ? 'rgba(239,68,68,0.7)' :
            v >= 10_000_000 ? 'rgba(249,115,22,0.7)' :
            v >= 1_000_000  ? 'rgba(245,158,11,0.7)' :
                              'rgba(59,130,246,0.6)'),
          borderRadius: 3,
          order: 2,
        }},
        {{
          type: 'line',
          label: '5d Avg',
          data: cd.rolling,
          borderColor: 'rgba(99,102,241,0.8)',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.4,
          order: 1,
        }}
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ intersect: false, mode: 'index' }},
      plugins: {{
        legend: {{ display: true, position: 'top', labels: {{ boxWidth: 10, padding: 10 }} }},
        tooltip: {{
          callbacks: {{
            label: ctx => {{
              const v = ctx.raw;
              if (v === undefined) return '';
              if (v >= 1e9) return ctx.dataset.label + ': ' + (v/1e9).toFixed(2) + 'B';
              if (v >= 1e6) return ctx.dataset.label + ': ' + (v/1e6).toFixed(1) + 'M';
              if (v >= 1e3) return ctx.dataset.label + ': ' + (v/1e3).toFixed(1) + 'K';
              return ctx.dataset.label + ': ' + v;
            }}
          }}
        }}
      }},
      scales: {{
        x: {{ grid: {{ color: gridColor }}, ticks: {{ maxRotation: 0, autoSkip: true, maxTicksLimit: 8 }} }},
        y: {{
          grid: {{ color: gridColor }},
          ticks: {{
            callback: v =>
              v >= 1e9 ? (v/1e9).toFixed(1)+'B' :
              v >= 1e6 ? (v/1e6).toFixed(0)+'M' :
              v >= 1e3 ? (v/1e3).toFixed(0)+'K' : v
          }}
        }}
      }}
    }}
  }});
}}

function buildWeekProjChart(view) {{
  const pw = chartData[view].projWeek;
  return new Chart(document.getElementById('weekProjChart'), {{
    type: 'doughnut',
    data: {{
      labels: pw.labels,
      datasets: [{{ data: pw.values, backgroundColor: pw.colors, borderWidth: 0, hoverOffset: 6 }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      cutout: '60%',
      plugins: {{
        legend: {{ position: 'right', labels: {{ boxWidth: 10, padding: 8, font: {{ size: 11 }} }} }},
        tooltip: {{
          callbacks: {{
            label: ctx => {{
              const v = ctx.raw;
              const label = v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(0)+'K' : v;
              return ' ' + ctx.label + ': ' + label;
            }}
          }}
        }}
      }}
    }}
  }});
}}

function buildModelChart(view) {{
  const m = chartData[view].model;
  return new Chart(document.getElementById('modelChart'), {{
    type: 'bar',
    data: {{
      labels: m.labels,
      datasets: [{{
        data: m.values,
        backgroundColor: ['rgba(59,130,246,0.7)','rgba(139,92,246,0.7)','rgba(16,185,129,0.7)'],
        borderRadius: 4,
      }}]
    }},
    options: {{
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{
          grid: {{ color: gridColor }},
          ticks: {{
            callback: v =>
              v >= 1e9 ? (v/1e9).toFixed(1)+'B' :
              v >= 1e6 ? (v/1e6).toFixed(0)+'M' :
              v >= 1e3 ? (v/1e3).toFixed(0)+'K' : v
          }}
        }},
        y: {{ grid: {{ display: false }} }}
      }}
    }}
  }});
}}

function switchView(view) {{
  if (view === currentView) return;

  // Update tab buttons
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('tab-active'));
  document.getElementById('tab-' + view).classList.add('tab-active');

  // Show/hide view-specific HTML blocks
  ['cards', 'proj', 'recent', 'heatmap'].forEach(kind => {{
    document.querySelectorAll(`[id^="v-${{kind}}-"]`).forEach(el => {{
      el.style.display = el.dataset.view === view ? '' : 'none';
    }});
  }});

  // Destroy and rebuild charts with new view data
  if (dailyChart)    dailyChart.destroy();
  if (weekProjChart) weekProjChart.destroy();
  if (modelChart)    modelChart.destroy();
  dailyChart    = buildDailyChart(view);
  weekProjChart = buildWeekProjChart(view);
  modelChart    = buildModelChart(view);

  currentView = view;
}}

// Initial chart render
dailyChart    = buildDailyChart('all');
weekProjChart = buildWeekProjChart('all');
modelChart    = buildModelChart('all');

// Auto-refresh every 60 seconds
setTimeout(() => location.reload(), 60_000);
</script>
</body>
</html>"""


# ── Route ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    data_all      = _fetch_data()
    if data_all is None:
        return HTMLResponse("<h2 style='color:white;padding:40px;font-family:sans-serif'>"
                            "No database found at ~/.cctracker/sessions.db<br>"
                            "Run <code>cctracker backfill</code> first.</h2>", status_code=503)
    data_personal = _fetch_data("personal")
    data_work     = _fetch_data("work")
    return _html({
        "all":      data_all,
        "personal": data_personal,
        "litellm":  data_work,
    })


@app.get("/healthz")
def health():
    return {"ok": True}


# ── Entry point ───────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import sys
    open_browser = "--no-browser" not in sys.argv
    if open_browser:
        import threading
        def _open():
            import time; time.sleep(0.8)
            webbrowser.open(f"http://localhost:{PORT}")
        threading.Thread(target=_open, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
