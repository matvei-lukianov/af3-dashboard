#!/usr/bin/env python3
"""Generate AF3 campaign dashboard: stats + plots + index.html.

Runs hourly via cron. Output goes into this directory, then a git commit/push
publishes it to https://matvei-lukianov.github.io/af3-dashboard/.
"""
import subprocess, re, json, html
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

OUT = Path(__file__).parent
CAMPAIGN_START = "2026-05-22"          # first batch (p021) began

# ----- pull sacct + squeue ------------------------------------------------

def sh(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, text=True, check=True).stdout

def load_sacct():
    out = sh(["sacct", "-u", "mlikianov", "-X",
              f"--starttime={CAMPAIGN_START}",
              "--format=JobID,JobName,Submit,Start,End,Elapsed,State,NodeList",
              "-P", "-n"])
    rows = [l.split("|") for l in out.splitlines() if l]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["JobID","JobName","Submit","Start","End",
                                     "Elapsed","State","NodeList"])
    df = df[df["JobName"].str.match(r"^p0\d+-p\d+_af3$", na=False)].copy()
    df["State"] = df["State"].str.replace(r" by \d+", "", regex=True)
    df["pair"]  = df["JobName"].str.replace(r"_af3$", "", regex=True)
    for c in ("Submit", "Start", "End"):
        df[c+"_dt"] = pd.to_datetime(df[c], errors="coerce")
    def to_sec(s):
        if pd.isna(s) or s == "00:00:00": return np.nan
        parts = str(s).split("-")
        days = int(parts[0]) if len(parts) == 2 else 0
        h, m, sec = (int(x) for x in parts[-1].split(":"))
        return days*86400 + h*3600 + m*60 + sec
    df["Elapsed_s"] = df["Elapsed"].apply(to_sec)
    df["Pending_s"] = (df["Start_dt"] - df["Submit_dt"]).dt.total_seconds()
    return df

def load_squeue():
    out = sh(["squeue", "-u", "mlikianov", "-h",
              "-o", "%i|%j|%T|%M|%L|%R|%V"])
    rows = []
    for ln in out.splitlines():
        p = ln.split("|")
        if len(p) >= 6:
            rows.append(p[:7] if len(p) >= 7 else p[:6] + [""])
    if not rows:
        return pd.DataFrame(columns=["JobID","Name","State","Elapsed","TimeLeft","Reason","Submit"])
    return pd.DataFrame(rows, columns=["JobID","Name","State","Elapsed","TimeLeft","Reason","Submit"])

# ----- core stats ---------------------------------------------------------

def topline(df, q):
    done_pairs = set(df.loc[df["State"]=="COMPLETED", "pair"])
    now = pd.Timestamp.now()
    today = now.normalize()
    today_done = df[(df["State"]=="COMPLETED") & (df["End_dt"] >= today)]
    yest_done = df[(df["State"]=="COMPLETED") &
                   (df["End_dt"] >= today - timedelta(days=1)) &
                   (df["End_dt"] < today)]
    last24 = df[(df["State"]=="COMPLETED") &
                (df["End_dt"] >= now - timedelta(hours=24))]
    rate24 = len(last24)
    # Total scope: 19 receptors × ~480 ligands minus Kamila excludes — best
    # estimate from launcher logs. Hardcode for now.
    total_scope_est = 8550
    remaining = max(total_scope_est - len(done_pairs), 0)
    eta_days = remaining / rate24 if rate24 > 0 else float("inf")
    # Hide FAILED from the public dashboard — most of them were the alphagpu06
    # broken-node storm and would be misleading without the context. They live
    # in sacct if anyone asks.
    states = {k: v for k, v in df["State"].value_counts().to_dict().items()
              if not k.startswith("FAILED")}
    return {
        "total_done":  len(done_pairs),
        "today_done":  len(today_done),
        "yesterday":   len(yest_done),
        "last24_rate": rate24,
        "remaining":   remaining,
        "eta_days":    eta_days,
        "running":     int((q["State"]=="RUNNING").sum()) if not q.empty else 0,
        "pending":     int((q["State"]=="PENDING").sum()) if not q.empty else 0,
        "states_alltime": states,
        "now":         now.strftime("%Y-%m-%d %H:%M EDT"),
    }

# ----- plots --------------------------------------------------------------

def plot_daily_progress(df):
    completed = df[df["State"]=="COMPLETED"].copy()
    completed["day"] = completed["End_dt"].dt.normalize()
    counts = completed.groupby("day").size()
    if counts.empty:
        return
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.bar(counts.index, counts.values, color="#4a90e2", edgecolor="black", width=0.8)
    ax.axhline(counts.mean(), color="red", linestyle="--", linewidth=1.2,
               label=f"mean {counts.mean():.0f}/day")
    ax.set_xlabel("Date")
    ax.set_ylabel("COMPLETED pairs")
    ax.set_title(f"Daily progress (total {int(counts.sum())} since {CAMPAIGN_START})")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.legend(); ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUT/"daily_progress.png", dpi=110)
    plt.close()

def plot_concurrency(df):
    started = df[df["Start_dt"].notna()].copy()
    now = pd.Timestamp.now()
    started.loc[started["End_dt"].isna(), "End_dt"] = now
    events = []
    for _, r in started.iterrows():
        events.append((r["Start_dt"], +1))
        events.append((r["End_dt"],   -1))
    events.sort(key=lambda x: x[0])
    t, c, cur = [], [], 0
    for ts, d in events:
        cur += d
        t.append(ts); c.append(cur)
    if not t: return
    peak = max(c)
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.step(t, c, where="post", color="#7b68ee", linewidth=1.1)
    ax.fill_between(t, 0, c, step="post", alpha=0.2, color="#7b68ee")
    ax.axhline(30, color="red", linestyle=":", alpha=0.7, label="MaxJobsPU=30")
    ax.axhline(peak, color="green", linestyle=":", alpha=0.5, label=f"peak={peak}")
    ax.set_xlabel("Wall time")
    ax.set_ylabel("# concurrent AF3 jobs")
    ax.set_title(f"Concurrency timeline (peak {peak})")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.legend(loc="upper left"); ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUT/"concurrency.png", dpi=110)
    plt.close()

def plot_pending(df):
    started = df[df["Pending_s"].notna() & (df["Pending_s"] >= 0)]
    ph = started["Pending_s"] / 3600
    if ph.empty: return
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(ph, bins=50, color="#f5a623", edgecolor="black", alpha=0.85)
    ax.axvline(ph.mean(),   color="red",   linestyle="--", label=f"mean {ph.mean():.1f} h")
    ax.axvline(ph.median(), color="green", linestyle="--", label=f"median {ph.median():.1f} h")
    ax.set_xlabel("Pending time (hours)")
    ax.set_ylabel("Job count")
    ax.set_title(f"Pending time distribution (n={len(ph)})")
    ax.legend(); ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUT/"pending_hist.png", dpi=110)
    plt.close()

def plot_runtime(df):
    ok = df[df["State"]=="COMPLETED"]
    rt = (ok["Elapsed_s"]/60).dropna()
    if rt.empty: return
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(rt, bins=50, color="#4a90e2", edgecolor="black", alpha=0.85)
    ax.axvline(rt.mean(),   color="red",    linestyle="--", label=f"mean {rt.mean():.0f} min")
    ax.axvline(rt.median(), color="orange", linestyle="--", label=f"median {rt.median():.0f} min")
    ax.set_xlabel("Runtime (min)")
    ax.set_ylabel("Job count")
    ax.set_title(f"Runtime distribution — {len(rt)} COMPLETED jobs")
    ax.legend(); ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUT/"runtime_hist.png", dpi=110)
    plt.close()

def plot_running_hist(df):
    """Time-weighted concurrency histogram — hours spent at each parallel level."""
    started = df[df["Start_dt"].notna()].copy()
    now = pd.Timestamp.now()
    started.loc[started["End_dt"].isna(), "End_dt"] = now
    events = []
    for _, r in started.iterrows():
        events.append((r["Start_dt"], +1))
        events.append((r["End_dt"],   -1))
    events.sort(key=lambda x: x[0])
    durs = {}; cur = 0; prev = None
    for ts, d in events:
        if prev is not None:
            durs[cur] = durs.get(cur, 0) + (ts - prev).total_seconds()
        cur += d; prev = ts
    if not durs: return
    levels = sorted(durs); hrs = [durs[l]/3600 for l in levels]
    mean_lvl = sum(l*h for l,h in zip(levels,hrs)) / sum(hrs)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(levels, hrs, color="#7b68ee", edgecolor="black", alpha=0.85)
    ax.axvline(mean_lvl, color="red", linestyle="--",
               label=f"time-avg {mean_lvl:.1f} parallel")
    ax.set_xlabel("# concurrent jobs")
    ax.set_ylabel("Hours spent at that level")
    ax.set_title("Concurrency distribution (time-weighted)")
    ax.legend(); ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUT/"running_hist.png", dpi=110)
    plt.close()

# ----- HTML ---------------------------------------------------------------

CSS = """
body { font-family: -apple-system, sans-serif; max-width: 1200px; margin: 20px auto;
       padding: 0 20px; color: #222; }
h1 { border-bottom: 2px solid #4a90e2; padding-bottom: 6px; }
.stamp { color: #888; font-size: 0.9em; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px,1fr));
         gap: 12px; margin: 16px 0; }
.card { background: #f5f8fc; border: 1px solid #d1dbe8; border-radius: 8px;
        padding: 14px; }
.card .v { font-size: 2em; font-weight: bold; color: #2c5aa0; }
.card .l { font-size: 0.85em; color: #666; text-transform: uppercase; }
.plots { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.plots img { width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; }
.plots.wide img { grid-column: span 2; }
table { border-collapse: collapse; width: 100%; font-size: 0.9em; margin: 10px 0; }
th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; }
th { background: #f5f8fc; }
tr:nth-child(even) { background: #fafafa; }
.queue-table { max-height: 400px; overflow-y: auto; border: 1px solid #ddd;
               border-radius: 4px; }
"""

def render_index(stats, q):
    s = stats
    eta = "∞" if s["eta_days"] == float("inf") else f"{s['eta_days']:.1f} days"
    states_html = "".join(
        f"<tr><td>{html.escape(k)}</td><td>{v}</td></tr>"
        for k, v in sorted(s["states_alltime"].items(), key=lambda x: -x[1])
    )

    # queue tables (limit length)
    def q_rows(state):
        sub = q[q["State"]==state].head(80) if not q.empty else pd.DataFrame()
        rows = []
        for _, r in sub.iterrows():
            rows.append(
                f"<tr><td>{html.escape(r['Name'])}</td>"
                f"<td>{html.escape(r['Elapsed'])}</td>"
                f"<td>{html.escape(r.get('TimeLeft',''))}</td>"
                f"<td>{html.escape(r.get('Reason',''))}</td></tr>"
            )
        return "".join(rows) or "<tr><td colspan=4><em>none</em></td></tr>"

    running_rows = q_rows("RUNNING")
    pending_rows = q_rows("PENDING")

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>AF3 Campaign Dashboard</title>
<meta http-equiv="refresh" content="600">
<style>{CSS}</style>
</head><body>
<h1>AF3 Campaign Dashboard</h1>
<p class="stamp">Last updated: <strong>{s['now']}</strong> · auto-refreshes every 10 min · regenerated hourly</p>

<div class="cards">
  <div class="card"><div class="v">{s['total_done']:,}</div><div class="l">Total COMPLETED pairs</div></div>
  <div class="card"><div class="v">{s['today_done']:,}</div><div class="l">Done today</div></div>
  <div class="card"><div class="v">{s['last24_rate']:,}</div><div class="l">Last 24 h rate</div></div>
  <div class="card"><div class="v">{s['remaining']:,}</div><div class="l">Remaining (current batch)</div></div>
  <div class="card"><div class="v">{eta}</div><div class="l">ETA at current rate</div></div>
  <div class="card"><div class="v">{s['running']} / {s['pending']}</div><div class="l">Running / Pending now</div></div>
</div>

<h2>Live queue</h2>
<div class="plots">
  <div>
    <h3>RUNNING ({s['running']})</h3>
    <div class="queue-table">
      <table><thead><tr><th>Job</th><th>Elapsed</th><th>Left</th><th>Reason / Node</th></tr></thead>
      <tbody>{running_rows}</tbody></table>
    </div>
  </div>
  <div>
    <h3>PENDING ({s['pending']})</h3>
    <div class="queue-table">
      <table><thead><tr><th>Job</th><th>Wait</th><th>Limit</th><th>Reason</th></tr></thead>
      <tbody>{pending_rows}</tbody></table>
    </div>
  </div>
</div>

<h2>Progress</h2>
<div class="plots wide"><img src="daily_progress.png" alt="Daily progress"></div>
<div class="plots wide"><img src="concurrency.png" alt="Concurrency timeline"></div>

<h2>Distributions</h2>
<div class="plots">
  <img src="pending_hist.png" alt="Pending histogram">
  <img src="running_hist.png" alt="Running histogram">
</div>
<div class="plots wide"><img src="runtime_hist.png" alt="Runtime histogram"></div>

<h2>All-time job state breakdown</h2>
<table><thead><tr><th>State</th><th>Count</th></tr></thead><tbody>{states_html}</tbody></table>

<p class="stamp"><small>Generated by <code>generate.py</code> on the SUNY HPC cluster.
Source / regeneration policy at <a href="https://github.com/matvei-lukianov/af3-dashboard">github.com/matvei-lukianov/af3-dashboard</a></small></p>
</body></html>
"""

def main():
    df = load_sacct()
    q  = load_squeue()
    if df.empty:
        print("no sacct data — bailing")
        return
    stats = topline(df, q)
    plot_daily_progress(df)
    plot_concurrency(df)
    plot_pending(df)
    plot_runtime(df)
    plot_running_hist(df)
    (OUT/"index.html").write_text(render_index(stats, q))
    (OUT/"stats.json").write_text(json.dumps({
        "now":         stats["now"],
        "total_done":  stats["total_done"],
        "today_done":  stats["today_done"],
        "last24_rate": stats["last24_rate"],
        "remaining":   stats["remaining"],
        "eta_days":    stats["eta_days"] if stats["eta_days"] != float("inf") else None,
        "running":     stats["running"],
        "pending":     stats["pending"],
    }, indent=2))
    print(f"Generated dashboard at {OUT} — {stats['total_done']} total, "
          f"{stats['running']}R/{stats['pending']}PD")

if __name__ == "__main__":
    main()
