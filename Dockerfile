# ---------------------------------------------------------------------------
# Sistema de Votação Institucional de Halloween INTS — imagem de produção.
#
# As CREDENCIAIS NÃO ENTRAM NA IMAGEM. O .env fica de fora (.dockerignore) e
# chega na hora de rodar, pelo env_file do docker-compose.yml. Imagem com senha
# dentro vaza para qualquer um que a puxe de um registro, e continua nas camadas
# antigas mesmo depois de a senha ser trocada.
#
# O banco SQLite (data/) e as fotos de candidatos (static/uploads/) moram em volumes
# (ver docker-compose.yml): preservam os votos e imagens entre restarts e updates.
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm

# Python sem buffer para logs em tempo real; fuso institucional America/Bahia
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    TZ=America/Bahia \
    PORT=8080 \
    HOST=0.0.0.0

WORKDIR /app

# Dependências de sistema mínimas para processamento de imagens (Pillow)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Dependências Python antes do código: camada em cache reutilizada a cada build
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código da aplicação
COPY . .

# Cria pastas para persistência de volumes e define permissões com usuário não-root
RUN mkdir -p /app/data /app/static/uploads && \
    useradd -u 1000 -m -s /bin/bash appuser && \
    chown -R appuser:appuser /app

# Usuário seguro sem privilégios de root
USER appuser
EXPOSE 8080

# Saúde checada pelo endpoint oficial /health (retorna JSON 200)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').getcode() == 200 else 1)"

# Execução em produção com servidor WSGI Gunicorn (2 workers, 4 threads)
CMD ["gunicorn", "-w", "2", "--threads", "4", "--bind", "0.0.0.0:8080", "--access-logfile", "-", "--error-logfile", "-", "run:app"]
