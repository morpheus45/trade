#!/bin/bash
# ============================================================
#  Trading Bot — Installateur ZÉRO CLIC
#  Ubuntu 20.04 / 22.04 / 24.04
#
#  USAGE (les clés sont passées en variables, jamais dans le code) :
#
#  export ANTHROPIC_API_KEY="sk-ant-api03-..."
#  export BINANCE_API_KEY="QOnH..."
#  export BINANCE_API_SECRET="NjJ7..."
#  export TELEGRAM_BOT_TOKEN="7458912815:..."
#  export TELEGRAM_CHAT_ID="6821928813"
#  curl -sSL https://raw.githubusercontent.com/morpheus45/trade/main/install_zero_click.sh | sudo -E bash
#
#  OU en une seule ligne :
#  ANTHROPIC_API_KEY="..." TELEGRAM_BOT_TOKEN="..." TELEGRAM_CHAT_ID="..." \
#    curl -sSL https://raw.githubusercontent.com/morpheus45/trade/main/install_zero_click.sh | sudo -E bash
# ============================================================

set -e
export DEBIAN_FRONTEND=noninteractive

# ── Couleurs ──────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[✓]${NC} $1"; }
info() { echo -e "${BLUE}[→]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
fail() { echo -e "${RED}[✗]${NC} $1"; exit 1; }
bar()  { echo -e "\n${BOLD}${BLUE}━━━ $1 ━━━${NC}"; }

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║    TRADING BOT v2 — Installation Zero-Clic       ║${NC}"
echo -e "${BOLD}║    Bot IA autonome H24 · Paper Trading            ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo ""

# ── Variables ─────────────────────────────────────────────────
INSTALL_DIR="/opt/trading-bot"
SERVICE_BOT="trading-bot"
SERVICE_DASH="trading-dashboard"
REPO="https://github.com/morpheus45/trade.git"
CAPITAL="1000"

# Clés API (passées en variables d'environnement, jamais hardcodées)
ANTHR_KEY="${ANTHROPIC_API_KEY:-}"
TG_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TG_CHAT="${TELEGRAM_CHAT_ID:-}"
BIN_KEY="${BINANCE_API_KEY:-${BINANCE_API_KEY_1:-}}"
BIN_SECRET="${BINANCE_API_SECRET:-${BINANCE_API_SECRET_1:-}}"

# Mode live ou paper selon presence des cles Binance
if [ -n "$BIN_KEY" ] && [ -n "$BIN_SECRET" ]; then
    PAPER_MODE="false"
else
    PAPER_MODE="true"
fi

# ── Étape 1 : Système ─────────────────────────────────────────
bar "1/10 — Mise à jour système"
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv python3-dev git curl ufw 2>/dev/null
ok "Paquets système installés"

# ── Étape 2 : Python 3.11 (si absent) ────────────────────────
bar "2/10 — Python"
PY=$(python3 --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
info "Python détecté : $PY"
if python3 -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
    ok "Python suffisant"
else
    info "Installation Python 3.11..."
    add-apt-repository ppa:deadsnakes/ppa -y -q 2>/dev/null || true
    apt-get install -y -qq python3.11 python3.11-venv python3.11-dev 2>/dev/null
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 2>/dev/null || true
    ok "Python 3.11 installé"
fi

# ── Étape 3 : Cloner le repo ──────────────────────────────────
bar "3/10 — Code source"
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Mise à jour du code..."
    git -C "$INSTALL_DIR" pull -q
    ok "Code mis à jour"
else
    info "Clonage depuis GitHub..."
    git clone -q "$REPO" "$INSTALL_DIR"
    ok "Code téléchargé"
fi
cd "$INSTALL_DIR"

# ── Étape 4 : Environnement Python ───────────────────────────
bar "4/10 — Environnement Python"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
ok "Dépendances Python installées"

# ── Étape 5 : Dossiers ────────────────────────────────────────
bar "5/10 — Structure"
mkdir -p logs models data src/static/icons docs/data
echo "$CAPITAL" > initial_capital.txt
ok "Dossiers créés, capital initial: ${CAPITAL} USDT"

# ── Étape 6 : Icônes PWA ─────────────────────────────────────
python3 src/generate_icons.py -q 2>/dev/null || true

# ── Étape 7 : Fichier .env ────────────────────────────────────
bar "6/10 — Configuration .env"
cat > src/.env << ENVEOF
# === Trading Bot — Configuration ===
BINANCE_API_KEY=${BIN_KEY}
BINANCE_API_SECRET=${BIN_SECRET}
API_KEY=${BIN_KEY}
API_SECRET=${BIN_SECRET}
TELEGRAM_BOT_TOKEN=${TG_TOKEN}
TELEGRAM_CHAT_ID=${TG_CHAT}
ANTHROPIC_API_KEY=${ANTHR_KEY}
PAPER_TRADING=${PAPER_MODE}
ENVEOF

# Vérifier les clés
[ -n "$ANTHR_KEY" ] && ok "ANTHROPIC_API_KEY configurée" || warn "ANTHROPIC_API_KEY manquante (fonctions IA limitées)"
[ -n "$TG_TOKEN"  ] && ok "TELEGRAM configuré"           || warn "Telegram non configuré (alertes désactivées)"
[ -n "$BIN_KEY"    ] && ok "BINANCE API — LIVE TRADING"   || warn "BINANCE absent → Paper Trading actif"
ok ".env créé"

# ── Étape 8 : Git config pour reporting ───────────────────────
bar "7/10 — Git config"
git config user.email "tradingbot@auto.local" 2>/dev/null || true
git config user.name  "TradingBot"            2>/dev/null || true
ok "Git configuré"

# ── Étape 9 : Modèle ML en arrière-plan ──────────────────────
bar "8/10 — Modèle ML"
if [ ! -f "models/xgboost_model.json" ]; then
    info "Entraînement ML en arrière-plan (5-10 min, ne bloque pas le démarrage)..."
    nohup bash -c "cd $INSTALL_DIR && source venv/bin/activate && python src/train_xgboost.py >> logs/ml_train.log 2>&1" &
    ML_PID=$!
    echo "$ML_PID" > logs/ml_train.pid
    ok "Entraînement ML lancé en fond (PID $ML_PID)"
    info "Le bot démarre immédiatement avec Claude seul, le ML s'ajoutera automatiquement"
else
    ok "Modèle ML déjà présent"
fi

# ── Étape 10 : Services systemd ───────────────────────────────
bar "9/10 — Services systemd"
VENV_PY="$INSTALL_DIR/venv/bin/python3"

# Service bot
cat > /etc/systemd/system/${SERVICE_BOT}.service << SVC
[Unit]
Description=Trading Bot IA Crypto H24
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR/src
ExecStart=$VENV_PY $INSTALL_DIR/src/run_forever.py
Restart=always
RestartSec=30
StandardOutput=append:$INSTALL_DIR/logs/bot.log
StandardError=append:$INSTALL_DIR/logs/bot.log
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SVC

# Service dashboard
cat > /etc/systemd/system/${SERVICE_DASH}.service << SVC
[Unit]
Description=Trading Bot Dashboard PWA
After=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR/src
ExecStart=$VENV_PY $INSTALL_DIR/src/dashboard.py
Restart=always
RestartSec=10
StandardOutput=append:$INSTALL_DIR/logs/dashboard.log
StandardError=append:$INSTALL_DIR/logs/dashboard.log
Environment=PYTHONUNBUFFERED=1
Environment=PORT=5000

[Install]
WantedBy=multi-user.target
SVC

systemctl daemon-reload
systemctl enable  "$SERVICE_BOT"  "$SERVICE_DASH"
systemctl restart "$SERVICE_BOT"
sleep 2
systemctl restart "$SERVICE_DASH"
sleep 2

BOT_STATUS=$(systemctl is-active  "$SERVICE_BOT"  2>/dev/null || echo "unknown")
DASH_STATUS=$(systemctl is-active "$SERVICE_DASH" 2>/dev/null || echo "unknown")
ok "Bot       : $BOT_STATUS"
ok "Dashboard : $DASH_STATUS"

# ── Firewall ──────────────────────────────────────────────────
bar "10/10 — Réseau"
ufw allow 22/tcp   2>/dev/null || true
ufw allow 5000/tcp 2>/dev/null || true
ufw --force enable 2>/dev/null || true
ok "Port 5000 ouvert"

# ── IP publique ───────────────────────────────────────────────
PUBLIC_IP=$(curl -s --max-time 4 ifconfig.me 2>/dev/null || \
            curl -s --max-time 4 ipinfo.io/ip 2>/dev/null || \
            hostname -I | awk '{print $1}')

# ── Résumé final ──────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║         ✅  INSTALLATION TERMINÉE                ║${NC}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e " Bot trading   : ${GREEN}${BOT_STATUS}${NC}"
echo -e " Dashboard     : ${GREEN}${DASH_STATUS}${NC}"
echo ""
echo -e " ${BOLD}Dashboard local   :${NC} ${BLUE}http://${PUBLIC_IP}:5000${NC}"
echo -e " ${BOLD}Dashboard GitHub  :${NC} ${BLUE}https://morpheus45.github.io/trade/${NC}"
echo ""
echo -e " ${BOLD}Telegram :${NC} Envoie ${YELLOW}/status${NC} pour vérifier l'état"
echo ""
echo -e " ${BOLD}Logs en temps réel :${NC}"
echo    "   sudo journalctl -fu trading-bot"
echo    "   tail -f $INSTALL_DIR/logs/bot.log"
echo ""
echo -e " ${BOLD}Mise à jour depuis GitHub :${NC}"
echo    "   cd $INSTALL_DIR && git pull && sudo systemctl restart trading-bot"
echo ""
echo -e " ${YELLOW}Note :${NC} Le modèle ML s'entraîne en arrière-plan (~10 min)"
echo    "        Logs ML : tail -f $INSTALL_DIR/logs/ml_train.log"
echo ""
