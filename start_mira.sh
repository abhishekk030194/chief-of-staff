#!/bin/bash
PIDFILE=/tmp/mira.pid

# Kill any previous instance using the PID file
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    kill -9 "$OLD_PID" 2>/dev/null
    rm -f "$PIDFILE"
fi
# Also sweep by name as a safety net
pkill -9 -f bot.py 2>/dev/null
sleep 1

set -a
source /Users/abhishek/Documents/aiProjects/chief-of-staff/.env
set +a
cd /Users/abhishek/Documents/aiProjects/chief-of-staff

# Write our PID before exec (exec replaces the shell, keeping the same PID)
echo $$ > "$PIDFILE"
exec /usr/bin/python3 bot.py
