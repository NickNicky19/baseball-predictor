FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ARG SOURCE_COMMIT
ARG SCAFFOLD_WHEEL_SHA256
LABEL org.opencontainers.image.title="baseball-predictor-omega-staging-dashboard" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      org.opencontainers.image.description="Read-only research dashboard; no prediction execution"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DASHBOARD_SNAPSHOT_ROOT=/app/snapshots \
    PORT=8080

RUN printf '%s' "${SOURCE_COMMIT}" | grep -Eq '^[0-9a-f]{40}$' \
    && groupadd --gid 10001 dashboard \
    && useradd --uid 10001 --gid dashboard --no-create-home --shell /usr/sbin/nologin dashboard

WORKDIR /app
COPY requirements/profiles/scaffold-runtime.lock /tmp/scaffold-runtime.lock
COPY dist/omega_trust_scaffold-0.4.0-py3-none-any.whl /tmp/omega_trust_scaffold-0.4.0-py3-none-any.whl
RUN printf '%s  %s\n' "${SCAFFOLD_WHEEL_SHA256}" /tmp/omega_trust_scaffold-0.4.0-py3-none-any.whl | sha256sum -c - \
    && python -m pip install --no-cache-dir --require-hashes --no-deps -r /tmp/scaffold-runtime.lock \
    && python -m pip install --no-cache-dir --no-deps /tmp/omega_trust_scaffold-0.4.0-py3-none-any.whl \
    && python -m pip check \
    && rm /tmp/scaffold-runtime.lock /tmp/omega_trust_scaffold-0.4.0-py3-none-any.whl

RUN install -d -o root -g root -m 0555 /app/snapshots \
    && find /usr/local/lib/python3.12/site-packages/dashboard -type d -exec chmod 0555 {} + \
    && find /usr/local/lib/python3.12/site-packages/dashboard -type f -exec chmod 0444 {} + \
    && find /usr/local/lib/python3.12/site-packages/src/omega_contracts -type d -exec chmod 0555 {} + \
    && find /usr/local/lib/python3.12/site-packages/src/omega_contracts -type f -exec chmod 0444 {} +

USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"

CMD ["uvicorn", "dashboard.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
