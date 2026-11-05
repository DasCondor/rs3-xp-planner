FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RSXP_DB=/data/rsxp.sqlite3

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rsxp ./rsxp

# run as a normal user; everything it writes goes to /data
RUN useradd --system --uid 10001 rsxp && mkdir /data && chown rsxp /data
USER rsxp
VOLUME /data

EXPOSE 8765
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/players', timeout=4)" || exit 1

CMD ["python", "-m", "rsxp", "serve", "--host", "0.0.0.0", "--port", "8765"]
