#!/usr/bin/env bash
#
# start-viz.sh — one-click launch for the DJ library visualizer + Cloudflare tunnel.
#
#   ./start-viz.sh            start app + tunnel, print the public URL
#   ./start-viz.sh stop       stop both
#   ./start-viz.sh status     show what's running + current URL
#
# App:  http://127.0.0.1:8050   (basic auth: dj / crate-digger-8050)
# Logs: viz_app.run.log, tunnel.log
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PY="$HOME/.rbx-viz/bin/python"
PORT=8050
AUTH="dj:crate-digger-8050"
APP_LOG="$HERE/viz_app.run.log"
TUN_LOG="$HERE/tunnel.log"

app_up()  { curl -s -o /dev/null -w "%{http_code}" -u "$AUTH" "http://127.0.0.1:$PORT/" 2>/dev/null | grep -q 200; }
tun_url() { grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" "$TUN_LOG" 2>/dev/null | tail -1; }

start() {
  # --- sanity checks ---
  [ -x "$PY" ] || { echo "✗ venv python not found at $PY"; exit 1; }
  command -v cloudflared >/dev/null || { echo "✗ cloudflared not installed (brew install cloudflared)"; exit 1; }
  [ -f "$HERE/viz_arrays.npz" ] && [ -f "$HERE/viz_meta.csv" ] || {
    echo "✗ missing viz_arrays.npz / viz_meta.csv — run: $PY viz_precompute.py"; exit 1; }

  # --- app ---
  if app_up; then
    echo "• app already running on :$PORT"
  else
    echo "• starting app…"
    nohup "$PY" viz_app.py > "$APP_LOG" 2>&1 &
    for i in $(seq 1 30); do app_up && break; sleep 1; done
    app_up || { echo "✗ app failed to start — see $APP_LOG"; tail -15 "$APP_LOG"; exit 1; }
  fi

  # --- tunnel ---
  if pgrep -f "cloudflared tunnel --url http://localhost:$PORT" >/dev/null; then
    echo "• tunnel already running"
  else
    echo "• starting cloudflare tunnel…"
    : > "$TUN_LOG"
    nohup cloudflared tunnel --url "http://localhost:$PORT" > "$TUN_LOG" 2>&1 &
    for i in $(seq 1 30); do [ -n "$(tun_url)" ] && break; sleep 1; done
  fi

  local url; url="$(tun_url)"
  echo
  echo "  ✓ local:  http://127.0.0.1:$PORT/"
  echo "  ✓ public: ${url:-<pending — check $TUN_LOG>}"
  echo "  ✓ login:  dj / crate-digger-8050"
}

stop() {
  echo "• stopping tunnel + app…"
  pkill -f "cloudflared tunnel --url http://localhost:$PORT" 2>/dev/null && echo "  stopped tunnel" || echo "  tunnel not running"
  pkill -f "viz_app.py" 2>/dev/null && echo "  stopped app" || echo "  app not running"
}

status() {
  if app_up; then echo "app:    UP (http://127.0.0.1:$PORT/)"; else echo "app:    down"; fi
  if pgrep -f "cloudflared tunnel --url http://localhost:$PORT" >/dev/null; then
    echo "tunnel: UP ($(tun_url))"
  else
    echo "tunnel: down"
  fi
}

case "${1:-start}" in
  start)  start ;;
  stop)   stop ;;
  restart) stop; sleep 1; start ;;
  status) status ;;
  *) echo "usage: $0 {start|stop|restart|status}"; exit 1 ;;
esac
