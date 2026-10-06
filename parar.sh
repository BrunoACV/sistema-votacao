#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  Para o Sistema de Votação do Concurso de Halloween INTS (Linux/Servidor).
# ---------------------------------------------------------------------------
cd "$(dirname "$0")"

echo "Encerrando Sistema de Votacao de Halloween..."

if command -v docker &> /dev/null && docker compose version &> /dev/null; then
    docker compose down
fi

# Finaliza processos python na porta se ainda estiverem rodando
PID=$(lsof -t -i:9090 2>/dev/null || true)
if [ -n "$PID" ]; then
    echo "Encerrando processo $PID..."
    kill -9 "$PID" 2>/dev/null || true
fi

echo "[OK] Finalizado."
