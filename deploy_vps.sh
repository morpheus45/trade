#!/bin/bash
# ============================================================
#  Trading Bot — Déploiement VPS Linux (Ubuntu 22.04/24.04)
#  Lance ce script sur ton VPS après y avoir copié le projet
#
#  Usage :
#    1. Copie le projet sur le VPS :
#       scp -r "trading_bot dernier/" user@IP_VPS:~/trade/
#    2. Connecte-toi : ssh user@IP_VPS
#    3. Lance : bash deploy_vps.sh
# ============================================================

set -e
BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="trading-bot"
PYTHON="python3"
PIP="pip3"

echo ""
echo "================================================"
echo "  Trading Bot — Installation VPS"
echo "  Dossier : $BOT_DIR"
echo "================================================"
echo ""

# ── 1. Mise à jour système ────────────────────────────────
echo "[1/7] Mise à jour du système..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip python3-venv git screen -qq

# ── 2. Environnement virtuel ──────────────────────────────
echo "[2/7] Création de l'environnement virtuel..."
cd "$BOT_DIR"
python3 -m venv venv
source venv/bin/activate

# ── 3. Dépendances ────────────────────────────────────────
echo "[3/7] Installation des dépendances Python..."
pip install -r requirements.txt -q

# ── 4. Configuration .env ─────────────────────────────────
echo "[4/7] Configuration..."
if [ ! -f "src/.env" ]; then
    cp src/.env.example src/.env 2>/dev/null || cat > src/.env << 'ENV'
API_KEY=REMPLACE_PAR_TA_CLE_BINANCE
API_SECRET=REMPLACE_PAR_TON_SECRET_BINANCE
TELEGRAM_BOT_TOKEN=REMPLACE_PAR_TON_TOKEN_TELEGRAM
TELEGRAM_CHAT_ID=REMPLACE_PAR_TON_CHAT_ID
ANTHROPIC_API_KEY=REMPLACE_PAR_TA_CLE_ANTHROPIC
PAPER_TRADING=true
ENV
    echo "  IMPORTANT : Edite src/.env avec tes clés API Binance !"
fi

# ── 5. Dossiers ───────────────────────────────────────────
mkdir -p logs models data src/static/icons

# ── 6. Entraînement ML ───────────────────────────────────
echo "[5/7] Entraînement du modèle ML..."
if [ ! -f "models/xgboost_model.json" ]; then
    cd src && python train_xgboost.py && cd ..
    echo "  Modèle entraîné."
else
    echo "  Modèle déjà présent, skip."
fi

# ── 7. Service systemd ────────────────────────────────────
echo "[6/7] Création du service systemd..."
VENV_PYTHON="$BOT_DIR/venv/bin/python3"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

sudo tee "$SERVICE_FILE" > /dev/null << SERVICE
[Unit]
Description=Trading Bot Crypto — Watchdog
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$BOT_DIR/src
ExecStart=$VENV_PYTHON $BOT_DIR/src/run_forever.py
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl start  "$SERVICE_NAME"

sleep 3
STATUS=$(sudo systemctl is-active "$SERVICE_NAME")

# ── 8. Firewall (port dashboard) ─────────────────────────
echo "[7/7] Ouverture du port 5000 (dashboard)..."
sudo ufw allow 5000/tcp 2>/dev/null || true

# ── Résumé ────────────────────────────────────────────────
echo ""
echo "================================================"
echo "  DÉPLOIEMENT TERMINÉ"
echo "================================================"
echo ""
echo "  Service : $STATUS"
echo "  Commandes utiles :"
echo "    sudo systemctl status $SERVICE_NAME   → état"
echo "    sudo systemctl stop   $SERVICE_NAME   → arrêter"
echo "    sudo systemctl start  $SERVICE_NAME   → démarrer"
echo "    sudo journalctl -fu   $SERVICE_NAME   → logs temps réel"
echo ""

# Récupérer l'IP publique
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || echo "TON_IP_VPS")
echo "  Dashboard Android : http://$PUBLIC_IP:5000"
echo "  Telegram : /status pour vérifier l'état"
echo ""
echo "  IMPORTANT : Edite src/.env si ce n'est pas encore fait"
echo "================================================"
