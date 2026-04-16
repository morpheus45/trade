#!/bin/bash
# ============================================================
#  Trading Bot — Installation automatique complète
#  Fonctionne sur Ubuntu 20.04 / 22.04 / 24.04 (Oracle, VPS...)
#
#  USAGE — Une seule commande à lancer sur ton serveur :
#  curl -sSL https://raw.githubusercontent.com/morpheus45/trade/main/install_cloud.sh | bash
# ============================================================

set -e

# ── Couleurs ──────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
info() { echo -e "${BLUE}[→]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║     TRADING BOT — Installation Cloud H24     ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ── Vérifications ─────────────────────────────────────────────
[[ $EUID -eq 0 ]] || err "Lance ce script avec sudo ou en root."
command -v curl >/dev/null || apt-get install -y curl -qq

# ── Variables ─────────────────────────────────────────────────
INSTALL_DIR="/opt/trading-bot"
SERVICE_NAME="trading-bot"
REPO_URL="https://github.com/morpheus45/trade.git"
PYTHON="python3"

# ── 1. Système ────────────────────────────────────────────────
info "Mise à jour du système..."
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv git nano screen ufw -qq
log "Système à jour"

# ── 2. Cloner le dépôt ────────────────────────────────────────
info "Téléchargement du bot depuis GitHub..."
if [ -d "$INSTALL_DIR" ]; then
    cd "$INSTALL_DIR" && git pull -q
    log "Code mis à jour"
else
    git clone -q "$REPO_URL" "$INSTALL_DIR"
    log "Code téléchargé"
fi
cd "$INSTALL_DIR"

# ── 3. Environnement Python ───────────────────────────────────
info "Création de l'environnement Python..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
log "Dépendances installées"

# ── 4. Dossiers ───────────────────────────────────────────────
mkdir -p logs models data src/static/icons

# ── 5. Icônes PWA ─────────────────────────────────────────────
info "Génération des icônes PWA..."
python src/generate_icons.py -q 2>/dev/null || true
log "Icônes générées"

# ── 6. Configuration .env ─────────────────────────────────────
echo ""
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "${BOLD}  CONFIGURATION — Clés API requises${NC}"
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo ""

if [ ! -f "src/.env" ]; then
    cp src/.env.example src/.env
fi

# Demande les clés interactivement
prompt_key() {
    local KEY="$1"
    local DESC="$2"
    local URL="$3"
    local current
    current=$(grep "^${KEY}=" src/.env 2>/dev/null | cut -d= -f2)

    if [[ "$current" == REMPLACE* ]] || [[ -z "$current" ]]; then
        echo -e "${YELLOW}▶ ${DESC}${NC}"
        [ -n "$URL" ] && echo -e "  Obtenir sur : ${BLUE}${URL}${NC}"
        read -p "  Valeur : " value
        if [ -n "$value" ]; then
            sed -i "s|^${KEY}=.*|${KEY}=${value}|" src/.env
            log "${KEY} configuré"
        else
            warn "${KEY} non configuré — certaines fonctions seront limitées"
        fi
    else
        log "${KEY} déjà configuré"
    fi
    echo ""
}

prompt_key "API_KEY"            "Clé API Binance (lecture + trading spot)" "binance.com/fr/my/settings/api-management"
prompt_key "API_SECRET"         "Secret API Binance"                        ""
prompt_key "TELEGRAM_BOT_TOKEN" "Token du bot Telegram"                     "t.me/BotFather"
prompt_key "TELEGRAM_CHAT_ID"   "Ton Chat ID Telegram"                      "t.me/userinfobot"
prompt_key "ANTHROPIC_API_KEY"  "Clé API Claude (optionnelle)"              "console.anthropic.com/settings/keys"

# Mode paper trading
echo -e "${YELLOW}▶ Mode de trading${NC}"
echo "  1) PAPER TRADING — simulation (recommandé pour commencer)"
echo "  2) LIVE TRADING  — vrais fonds (DANGER)"
read -p "  Choix [1] : " mode_choice
if [ "$mode_choice" = "2" ]; then
    sed -i "s|^PAPER_TRADING=.*|PAPER_TRADING=false|" src/.env
    warn "Mode LIVE activé — les ordres seront réels !"
else
    sed -i "s|^PAPER_TRADING=.*|PAPER_TRADING=true|" src/.env
    log "Mode PAPER activé (simulation)"
fi

# ── 7. Capital initial (paper) ────────────────────────────────
if grep -q "PAPER_TRADING=true" src/.env; then
    echo ""
    read -p "Capital initial simulé (USDT) [1000] : " capital
    capital=${capital:-1000}
    echo "$capital" > initial_capital.txt
    log "Capital initial : ${capital} USDT"
fi

# ── 8. Entraînement ML ───────────────────────────────────────
echo ""
info "Entraînement du modèle ML XGBoost (~5-10 min, télécharge des données Binance)..."
echo "  Tu peux patienter ou appuyer sur Ctrl+C pour passer (le bot fonctionnera sans ML)."
echo ""
if python src/train_xgboost.py 2>&1 | tail -5; then
    log "Modèle ML entraîné"
else
    warn "Entraînement ML échoué — le bot fonctionnera sans filtre ML"
fi

# ── 9. Service systemd (H24, redémarrage auto) ────────────────
info "Configuration du service systemd (démarrage automatique)..."

VENV_PYTHON="$INSTALL_DIR/venv/bin/python3"

cat > /etc/systemd/system/${SERVICE_NAME}.service << SERVICE
[Unit]
Description=Trading Bot Crypto H24
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR/src
ExecStart=$VENV_PYTHON $INSTALL_DIR/src/run_forever.py
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1
Environment=HOME=/root

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable  "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
sleep 3
STATUS=$(systemctl is-active "$SERVICE_NAME")
log "Service systemd : $STATUS"

# ── 10. Service dashboard ─────────────────────────────────────
cat > /etc/systemd/system/trading-dashboard.service << SERVICE
[Unit]
Description=Trading Bot Dashboard PWA
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR/src
ExecStart=$VENV_PYTHON $INSTALL_DIR/src/dashboard.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1
Environment=PORT=5000

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable  trading-dashboard
systemctl restart trading-dashboard
sleep 2
DASH_STATUS=$(systemctl is-active trading-dashboard)
log "Dashboard : $DASH_STATUS"

# ── 11. Firewall ──────────────────────────────────────────────
info "Ouverture du port 5000 (dashboard Android)..."
ufw allow 22/tcp   2>/dev/null || true
ufw allow 5000/tcp 2>/dev/null || true
ufw --force enable 2>/dev/null || true
log "Firewall configuré"

# ── 12. Récupérer l'IP publique ───────────────────────────────
PUBLIC_IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || \
            curl -s --max-time 5 ipinfo.io/ip 2>/dev/null || \
            echo "VOTRE_IP_SERVEUR")

# ── Résumé final ──────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║           INSTALLATION TERMINÉE ✓            ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e " Bot trading   : ${GREEN}$STATUS${NC}"
echo -e " Dashboard     : ${GREEN}$DASH_STATUS${NC}"
echo ""
echo -e " ${BOLD}Dashboard Android :${NC} ${BLUE}http://${PUBLIC_IP}:5000${NC}"
echo -e "   → Ouvre cette URL dans Chrome sur ton téléphone"
echo -e "   → Menu ⋮ → 'Ajouter à l'écran d'accueil' → PWA installée"
echo ""
echo -e " ${BOLD}Telegram :${NC} Envoie /status à ton bot pour vérifier"
echo ""
echo -e " ${BOLD}Commandes utiles :${NC}"
echo    "   sudo systemctl status  trading-bot       → état"
echo    "   sudo systemctl restart trading-bot       → redémarrer"
echo    "   sudo journalctl -fu    trading-bot       → logs temps réel"
echo    "   sudo journalctl -fu    trading-dashboard → logs dashboard"
echo ""
echo -e " ${BOLD}Mise à jour automatique depuis GitHub :${NC}"
echo    "   cd $INSTALL_DIR && git pull && sudo systemctl restart trading-bot"
echo ""
echo -e "${GREEN}Le bot tourne maintenant H24. Bonne chance !${NC}"
echo ""
