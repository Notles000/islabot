#!/bin/bash

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "==> A iniciar ISLA Chatbot..."



# ── Python venv ──────────────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
  echo "==> A criar ambiente virtual Python..."
  python3 -m venv venv
fi

echo "==> A activar venv e instalar dependências..."
source venv/bin/activate
pip install -q -r backend/requirements.txt

# ── Data folders ─────────────────────────────────────────────────────────────
mkdir -p data/courses data/chroma

# ── Seed DB (only if empty) ──────────────────────────────────────────────────
if [ ! -f "data/isla_chatbot.db" ]; then
  echo "==> A criar base de dados e dados iniciais..."
  python seed.py
fi

# ── Backend ──────────────────────────────────────────────────────────────────
echo ""
echo "==> Tudo pronto! A iniciar servidor..."
LOCAL_IP=$(hostname -I | awk '{print $1}')
echo "==> Abre o browser em: http://localhost:8080"
echo "==> Na rede local:     http://${LOCAL_IP}:8080"
echo "==> Admin: admin@islasantarem.pt / admin1234"
echo ""
uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload
