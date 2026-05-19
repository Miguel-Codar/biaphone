#!/usr/bin/env bash
# setup-vps.sh — configuração única de uma nova VPS
#
# Uso:
#   bash scripts/setup-vps.sh user@host [ssh-port] [app-port] [deploy-path]
#
# Exemplos:
#   bash scripts/setup-vps.sh root@1.2.3.4
#   bash scripts/setup-vps.sh root@1.2.3.4 22 8001 /root/cliente2

set -euo pipefail

TARGET="${1:?Uso: bash scripts/setup-vps.sh user@host [ssh-porta] [app-porta] [deploy-path]}"
SSH_PORT="${2:-22}"
APP_PORT="${3:-8000}"
DEPLOY_PATH="${4:-/root/biaphone}"
REPO_URL="https://github.com/Miguel-Codar/biaphone.git"

SSH="ssh -o StrictHostKeyChecking=no -p ${SSH_PORT} ${TARGET}"

echo ""
echo "════════════════════════════════════════════"
echo "  Setup VPS: $TARGET"
echo "  SSH: $SSH_PORT | App: $APP_PORT | Path: $DEPLOY_PATH"
echo "════════════════════════════════════════════"
echo ""

# ── 1. Docker ──────────────────────────────────────────────────
echo "▶ [1/4] Verificando Docker..."
$SSH "command -v docker &>/dev/null && echo 'já instalado' || (curl -fsSL https://get.docker.com | sh && echo 'instalado')"
echo "✅ Docker OK"

# ── 2. Repositório ─────────────────────────────────────────────
echo ""
echo "▶ [2/4] Clonando / atualizando repositório..."
$SSH "[ -d '${DEPLOY_PATH}' ] \
  && (cd '${DEPLOY_PATH}' && git pull && echo 'atualizado') \
  || (git clone '${REPO_URL}' '${DEPLOY_PATH}' && echo 'clonado')"
echo "✅ Repositório em ${DEPLOY_PATH}"

# ── 3. .env ────────────────────────────────────────────────────
echo ""
echo "▶ [3/4] Verificando .env..."
$SSH "[ -f '${DEPLOY_PATH}/.env' ] \
  && echo 'já existe' \
  || (cp '${DEPLOY_PATH}/.env.example' '${DEPLOY_PATH}/.env' && echo 'criado a partir do template')"

# ── 4. Criar pasta data ────────────────────────────────────────
echo ""
echo "▶ [4/4] Criando pasta de dados..."
$SSH "mkdir -p '${DEPLOY_PATH}/data'"
echo "✅ Pasta data OK"

# ── Resumo ─────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════"
echo "✅ Setup concluído!"
echo ""
echo "Próximos passos:"
echo ""
echo "  1. Edite o .env com as credenciais do cliente:"
echo "     ssh -p ${SSH_PORT} ${TARGET} \"nano ${DEPLOY_PATH}/.env\""
echo ""
echo "  2. Suba o bot:"
echo "     ssh -p ${SSH_PORT} ${TARGET} \"cd ${DEPLOY_PATH} && docker compose up -d --build\""
echo ""
echo "  3. Adicione o cliente ao clients.json para deploys futuros:"
echo "     {"
echo "       \"name\": \"nome-cliente\","
echo "       \"host\": \"$(echo $TARGET | cut -d@ -f2)\","
echo "       \"user\": \"$(echo $TARGET | cut -d@ -f1)\","
echo "       \"port\": ${SSH_PORT},"
echo "       \"path\": \"${DEPLOY_PATH}\","
echo "       \"ssh_key\": null,"
echo "       \"app_port\": ${APP_PORT},"
echo "       \"notes\": \"Descrição do cliente\""
echo "     }"
echo "════════════════════════════════════════════"
