#!/bin/bash
set -e

# Garante a existência dos diretórios de dados
mkdir -p /app/data /app/static/uploads

# Se o volume montado estiver sem o banco de dados, restaura o snapshot inicial
if [ ! -f "/app/data/voting.db" ] && [ -f "/app/data/voting.db.seed" ]; then
    echo "[INFO] Inicializando /app/data/voting.db a partir do snapshot inicial..."
    cp /app/data/voting.db.seed /app/data/voting.db
fi

# Assegura permissões de leitura/escrita nos volumes de persistência montados
chown -R appuser:appuser /app/data /app/static/uploads 2>/dev/null || true
chmod -R 775 /app/data /app/static/uploads 2>/dev/null || true

# Executa o comando principal como appuser caso iniciado como root
if [ "$(id -u)" = "0" ]; then
    exec gosu appuser "$@"
else
    exec "$@"
fi
