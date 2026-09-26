#!/bin/bash
# beta_snapshot.sh — runs on the Beta login node (separate SLURM controller that
# Alpha can't query). Every INTERVAL seconds it writes Beta's queue + job history
# + per-job pair progress to beta_snapshot.json on the shared filesystem, which
# dashboard/generate.py (on Alpha) merges into the dashboard.
# Start: nohup bash beta_snapshot.sh > beta_snapshot.log 2>&1 &
set -uo pipefail
OUT=/mnt/home/mlikianov/dashboard/beta_snapshot.json
INTERVAL=600

while true; do
python3 - "$OUT" <<'EOF'
import json, subprocess, sys, glob, os, time
out = sys.argv[1]
LOG = "/mnt/home/mlikianov/af3_logs"
def sh(c): return subprocess.run(c, capture_output=True, text=True).stdout
q = []
for ln in sh(["squeue", "-u", "mlikianov", "-h", "-o", "%i|%j|%T|%M|%L|%R|%V"]).splitlines():
    p = ln.split("|")
    if len(p) >= 7 and p[1].startswith("af3_beta_"):
        jid, name = p[0], p[1]
        pf = f"{LOG}/beta_queue/jobs/{jid}.pairs"
        total = len(open(pf).read().split()) if os.path.exists(pf) else None
        logs = glob.glob(f"{LOG}/{name}_{jid}_gpu*.log")
        done = fail = 0
        for f in logs:
            for l in open(f, errors="replace"):
                if " RESULT rc=" in l:
                    if " RESULT rc=0 " in l: done += 1
                    else: fail += 1
        q.append(dict(JobID=jid, Name=name, State=p[2], Elapsed=p[3], TimeLeft=p[4],
                      Reason=p[5], Submit=p[6], pairs_total=total, pairs_done=done, pairs_failed=fail))
hist = []
for ln in sh(["sacct", "-u", "mlikianov", "-X", "--starttime=2026-09-21",
              "--format=JobID,JobName,Submit,Start,End,Elapsed,State,NodeList", "-P", "-n"]).splitlines():
    p = ln.split("|")
    if len(p) == 8 and p[1].startswith("af3_beta_"):
        hist.append(dict(zip(["JobID","JobName","Submit","Start","End","Elapsed","State","NodeList"], p)))
tmp = out + ".tmp"
json.dump(dict(ts=time.time(), queue=q, sacct=hist), open(tmp, "w"))
os.replace(tmp, out)
EOF
    sleep "$INTERVAL"
done
