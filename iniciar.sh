#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  Inicia o Sistema de Votação do Concurso de Halloween INTS (Linux/Servidor).
# ---------------------------------------------------------------------------
set -e
cd "$(dirname "$0")"

echo "=================================================="
echo "  Sistema de Votacao Institucional - INTS"
echo "=================================================="

# Garante a existencia das pastas de persistencia e permissoes no host
mkdir -p data static/uploads
chmod -R 775 data static/uploads 2>/dev/null || true

# Se o docker compose estiver disponível, prioriza contêiner
if command -v docker &> /dev/null && docker compose version &> /dev/null; then
    echo "[INFO] Subindo via Docker Compose..."
    docker compose up -d --build
    echo ""
    echo "[SUCESSO] Sistema rodando no Docker na porta 9090!"
    echo "Logs: docker compose logs -f votacao"
    exit 0
fi

# Fallback para ambiente virtual Python
if [ -d ".venv" ]; then
    echo "[INFO] Iniciando via ambiente virtual .venv..."
    source .venv/bin/activate
    python run.py
else
    echo "[ERRO] Nem Docker nem .venv foram encontrados."
    exit 1
fi
