# Painel de Energia: Python puro, imagem pequena, roda em x86 e ARM (Raspberry Pi, NAS).
ARG PYTHON_IMAGE=python:3.12-alpine
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    HTTP_PORT=8080 \
    HOME=/tmp

WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

COPY app ./app
COPY tools ./tools

# Roda sem privilégios. O volume /data nasce pertencendo a este usuário.
RUN mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('HTTP_PORT', '8080'), timeout=4)"]

CMD ["python", "-m", "app"]
