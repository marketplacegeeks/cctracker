#!/usr/bin/env bash
# Launch the cctracker token dashboard.
# Starts the server if not already running, then opens the browser.

PORT=7821
APP_LAUNCHER="/Users/aniket/Documents/Development/TokenTracker/cctracker.app/Contents/MacOS/cctracker"
PIDFILE="/tmp/cctracker_dashboard.pid"
LOGFILE="/tmp/cctracker_dashboard.log"

# Check if already running
if [ -f "$PIDFILE" ]; then
  OLD_PID=$(cat "$PIDFILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    # Already running — just open browser
    open "http://localhost:$PORT"
    exit 0
  fi
fi

# Start server via app bundle (gives custom Dock icon)
"$APP_LAUNCHER" --no-browser >> "$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"

# Wait for server to be ready (up to 5 seconds)
for i in 1 2 3 4 5; do
  sleep 0.8
  if curl -sf "http://localhost:$PORT/healthz" > /dev/null 2>&1; then
    break
  fi
done

open "http://localhost:$PORT"
