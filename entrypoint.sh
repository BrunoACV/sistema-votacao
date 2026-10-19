#!/bin/bash
# Arranque do contêiner. Regra de ouro: os dados de produção moram nos volumes
# (votacao_dados e votacao_fotos) e NUNCA são sobrescritos por uma publicação nova.
set -e

DADOS=/app/data
FOTOS=/app/static/uploads
mkdir -p "$DADOS" "$FOTOS" "$DADOS/backups"

# 1. Banco: só é criado quando o volume ainda não tem nenhum (primeira instalação).
if [ ! -s "$DADOS/voting.db" ]; then
    if [ -s /app/legado/voting.db ]; then
        echo "[INFO] Primeira instalação: criando o banco a partir de ./data/voting.db do repositório."
        cp /app/legado/voting.db "$DADOS/voting.db"
    else
        echo "[INFO] Primeira instalação sem banco inicial: o sistema cria um banco vazio."
    fi
else
    echo "[INFO] Banco de produção preservado: $DADOS/voting.db (o ./data do repositório é ignorado)."
fi

# 2. Fotos: copia só as que faltam (cp -n nunca sobrescreve uma foto existente).
for origem in /app/legado-fotos; do
    if [ -d "$origem" ]; then cp -rn "$origem"/. "$FOTOS"/ 2>/dev/null || true; fi
done

# 3. Backup a cada subida (antes de qualquer migração de esquema); guarda os 20 últimos.
if [ -s "$DADOS/voting.db" ]; then
    python - <<'PY'
import sqlite3, time, pathlib
dados = pathlib.Path("/app/data")
destino = dados / "backups" / time.strftime("voting-%Y%m%d-%H%M%S.db")
origem = sqlite3.connect(str(dados / "voting.db"))
copia = sqlite3.connect(str(destino))
origem.backup(copia)
copia.close(); origem.close()
antigos = sorted((dados / "backups").glob("voting-*.db"))[:-20]
for f in antigos:
    f.unlink()
print(f"[INFO] Backup do banco: {destino.name}")
PY
fi

chown -R appuser:appuser "$DADOS" "$FOTOS" 2>/dev/null || true

if [ "$(id -u)" = "0" ]; then
    exec gosu appuser "$@"
else
    exec "$@"
fi
