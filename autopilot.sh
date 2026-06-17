#!/bin/bash
# autopilot.sh — regenerate dashboard + commit + push every hour, forever.
# Started via nohup since `crontab` is blocked for us by PAM on this cluster.
set -uo pipefail

cd /mnt/home/mlikianov/dashboard
export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/id_ed25519_github -o IdentitiesOnly=yes"

INTERVAL=3600   # seconds (1 hour)

while true; do
    TS=$(date '+%F %T')
    echo "[$TS] regenerating..."
    if python3 generate.py; then
        # Commit only if something actually changed
        if ! git diff --quiet || ! git diff --staged --quiet || [ -n "$(git status --porcelain)" ]; then
            git add -A
            git commit -m "auto: $TS" >/dev/null 2>&1 && \
              git push origin main 2>&1 | tail -2
            echo "[$TS] pushed"
        else
            echo "[$TS] nothing changed (unlikely — timestamp should always differ)"
        fi
    else
        echo "[$TS] generate.py FAILED"
    fi
    sleep "$INTERVAL"
done
