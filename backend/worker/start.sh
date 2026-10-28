#!/bin/sh
# Virtual screen :99 -> x11vnc (localhost) -> noVNC on 6080 -> Tailscale Serve (tailnet only, when
# TS_AUTHKEY is set), then the worker. No VNC password: noVNC binds to NOVNC_BIND (127.0.0.1 by
# default) and is reached only through the tailnet. The portrait size fills a phone screen.
set -e
mkdir -p /data/runs /data/profile
# ponytail: runs older than 14 days pruned at boot only; a daily loop if the volume fills between deploys
find /data/runs -mindepth 1 -maxdepth 1 -mtime +14 -exec rm -rf {} +
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
Xvfb :99 -screen 0 900x1200x24 -nolisten tcp &
for i in $(seq 50); do [ -S /tmp/.X11-unix/X99 ] && break; sleep 0.1; done
x11vnc -display :99 -nopw -forever -shared -localhost -quiet -bg -o /dev/null
websockify --web /usr/share/novnc "${NOVNC_BIND:-127.0.0.1}:6080" localhost:5900 >/dev/null 2>&1 &

if [ -n "$TS_AUTHKEY" ]; then
  mkdir -p /data/tailscale
  tailscaled --tun=userspace-networking --statedir=/data/tailscale >/dev/null 2>&1 &
  tailscale up --authkey="$TS_AUTHKEY" --hostname="${TS_HOSTNAME:-hireos-worker}"
  tailscale serve --bg 6080
  [ -n "$WORKER_SCREEN_URL" ] || export WORKER_SCREEN_URL="https://$(tailscale status --json \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
fi
echo "screen: ${WORKER_SCREEN_URL:-local only (no TS_AUTHKEY)}"
exec /usr/local/bin/python apply_worker.py ${WORKER_ARGS:---watch}   # WORKER_ARGS="--dry-run 69" to test
