# Imagem do serviço Game-Core (API/WebSocket de partida e worker de simulação).
# O mesmo binário serve os dois workloads; quem decide é o `command` do Deployment.
#
# Corrige o arranjo do docker-compose, que usa `python:3.11` e roda
# `pip install -r requirements.txt` no start, com o código vindo de um bind mount
# em /goinfre. Isso não tem equivalente em Kubernetes e não é reproduzível.

# ---- estágio 1: compila as dependências ----
FROM docker.io/library/python:3.11-slim AS builder

# psycopg2 (não -binary) compila a partir do fonte. Precisa de build-essential
# (gcc MAIS os headers da libc — só `gcc` falha com "stdlib.h: No such file") e de
# libpq-dev. Tudo fica neste estágio e não chega à imagem final.
RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels
COPY src/requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---- estágio 2: runtime ----
FROM docker.io/library/python:3.11-slim AS runtime

# libpq5 é a biblioteca de runtime do Postgres; o -dev do builder não é necessário.
RUN apt-get update && apt-get install --no-install-recommends -y \
        libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

COPY --from=builder /wheels /wheels
COPY src/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r /tmp/requirements.txt \
    && rm -rf /wheels /tmp/requirements.txt

WORKDIR /app
COPY --chown=app:app src/ /app/

USER app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8001

# Padrão: a API. O worker de simulação sobrescreve com
# ["python", "manage.py", "session_worker"].
CMD ["uvicorn", "game_project.asgi:application", "--host", "0.0.0.0", "--port", "8001"]
