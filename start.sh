#!/bin/bash

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "==> A iniciar ISLA Chatbot..."

# ── Detect active LLM provider ───────────────────────────────────────────────
LLM_PROVIDER_VAL=$(grep -E '^LLM_PROVIDER=' backend/.env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || echo "ollama")

if [ "$LLM_PROVIDER_VAL" = "ollama" ]; then
  # ── Ollama path detection ───────────────────────────────────────────────────
  OLLAMA_BIN=""
  for candidate in \
      "$(command -v ollama 2>/dev/null)" \
      /usr/local/bin/ollama \
      /run/host/usr/local/bin/ollama \
      "$HOME/.local/bin/ollama" \
      /opt/ollama/ollama; do
    if [ -x "$candidate" ]; then
      OLLAMA_BIN="$candidate"
      break
    fi
  done

  if [ -z "$OLLAMA_BIN" ]; then
    echo "ERRO: Ollama não encontrado. Instala em https://ollama.com"
    exit 1
  fi

  echo "==> Ollama encontrado em: $OLLAMA_BIN"

  if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "==> A iniciar serviço Ollama..."
    "$OLLAMA_BIN" serve &
    sleep 4
  fi

  OLLAMA_MODEL_VAL=$(grep -E '^OLLAMA_MODEL=' backend/.env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || echo "qwen2.5:3b")
  echo "==> A verificar modelo $OLLAMA_MODEL_VAL..."
  if ! "$OLLAMA_BIN" list | grep -q "$OLLAMA_MODEL_VAL"; then
    echo "==> A descarregar $OLLAMA_MODEL_VAL (pode demorar alguns minutos)..."
    "$OLLAMA_BIN" pull "$OLLAMA_MODEL_VAL"
  fi
else
  echo "==> Provider: $LLM_PROVIDER_VAL — Ollama não necessário."
fi

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
