# Single stage on purpose: every dependency installs from a prebuilt wheel,
# so there is no compiler toolchain for a multi-stage build to strip out.
FROM python:3.12-slim

# PYTHONUNBUFFERED: logs reach `docker logs` / Render immediately.
# PYTHONDONTWRITEBYTECODE: no .pyc files written into the image at runtime.
# PORT: local default; Render injects its own PORT at runtime, overriding this.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app

# Dependencies before source: this layer is rebuilt only when
# requirements.txt changes, not on every code edit.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Run as an unprivileged user: a compromised process can't modify the image's
# files or bind privileged ports.
RUN useradd --system --uid 10001 --no-create-home appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ['PORT'], timeout=4)"

# - `exec` makes uvicorn PID 1, so it receives SIGTERM directly and runs the
#   app's shutdown (draining running batches) instead of being killed.
# - --workers 1 is explicit: batch state and locks live in process memory, and
#   uvicorn would otherwise take its worker count from $WEB_CONCURRENCY.
# - Shutdown budget (Render: SIGTERM, then SIGKILL after 30s):
#   --timeout-graceful-shutdown 20: uploads wait for their batch, so give open
#     requests most of the budget to finish and return their real result.
#     Batches whose client already disconnected keep running meanwhile.
#   then SHUTDOWN_GRACE_SECONDS (5): last wait for any batch still running,
#     after which it is cancelled and recorded as failed (inactive).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --workers 1 --timeout-graceful-shutdown 20"]
