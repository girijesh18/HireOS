# HireOS apply worker: Chromium on a virtual screen (noVNC), shared over Tailscale (tailnet only).
# Talks to HireOS over its HTTP API only. Build from the repo root:
#   docker build -f backend/worker/Dockerfile -t hireos-worker .
FROM python:3.12-slim

# Chromium only (with its system libraries); Python lives in /usr/local, apart from Debian's python3
# that websockify brings.
RUN pip install --no-cache-dir httpx==0.28.1 playwright==1.58.0 \
    && playwright install --with-deps chromium \
    && apt-get install -y --no-install-recommends xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*
# The bare screen address (https://<worker>/) opens the live screen, not noVNC's file listing
RUN printf '<meta http-equiv="refresh" content="0; url=vnc.html?autoconnect=true&resize=scale">' > /usr/share/novnc/index.html
COPY --from=tailscale/tailscale:v1.102.5 /usr/local/bin/tailscaled /usr/local/bin/tailscale /usr/local/bin/

WORKDIR /app
COPY backend/apply_worker.py backend/ats_form.py backend/workday.py ./
COPY backend/worker/start.sh /start.sh

# /data is a volume: the browser profile (Workday sign-ins), run screenshots, Tailscale's identity
ENV WORKER_DISPLAY=:99 WORKER_PROFILE=/data/profile WORKER_RUNS=/data/runs PYTHONUNBUFFERED=1
CMD ["sh", "/start.sh"]
