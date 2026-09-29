#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  Inicia o Sistema de Votação do Concurso de Halloween INTS (Linux/Servidor).
# ---------------------------------------------------------------------------
set -e
cd "$(dirname "$0")"

echo "=================================================="
echo "  Sistema de Votacao de Halloween - INTS"
echo "=================================================="

# Se o docker compose estiver disponível, prioriza contêiner
if command -v docker &> /dev/null && docker compose version &> /dev/null; then
    echo "[INFO] Subindo via Docker Compose..."
    docker compose up -d --build
    echo ""
    echo "[SUCESSO] Sistema rodando no Docker!"
    echo "Logs: docker compose logs -f voting"
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
