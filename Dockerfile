FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 durem \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin durem
COPY --chown=durem:durem . .
RUN mkdir -p /app/data/documents /app/data/backups && chown -R durem:durem /app/data
USER durem
ENV DUREM_HOST=0.0.0.0 DUREM_PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python","-c","import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/login', timeout=3).read(1)"]
CMD ["python","-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8080","--no-server-header"]
