# BookShift — production Docker image (python:3.11-slim, non-root UID 1000)

FROM python:3.11-slim

RUN groupadd --gid 1000 bookshift \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin bookshift

WORKDIR /app

COPY requirements-dev.txt /app/requirements-dev.txt
RUN pip install --no-cache-dir -r /app/requirements-dev.txt

COPY --chown=bookshift:bookshift . /app

RUN mkdir -p /data /library \
    && chown -R bookshift:bookshift /data /library /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

USER bookshift

EXPOSE 18001

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18001/api/v1/sync/health').read()"

CMD ["python3", "-m", "bookshift", "server"]
