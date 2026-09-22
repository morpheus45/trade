#!/usr/bin/env bash
#
# Expose le dashboard sur une URL HTTPS publique, sans ouvrir aucun port sur la box.
#
# Principe : cloudflared ouvre une connexion SORTANTE vers Cloudflare, qui relaie
# ensuite le trafic entrant. Aucune redirection de port, aucun port ouvert sur
# ta box, l'IP de ta maison n'est jamais exposee.
#
# Prerequis (cote Cloudflare, une seule fois) :
#   1. Un compte Cloudflare avec un nom de domaine (meme a 1 EUR/an).
#   2. https://one.dash.cloudflare.com -> Networks -> Tunnels -> Create a tunnel
#   3. Type "Cloudflared", nomme-le, copie le JETON affiche.
#   4. Onglet "Public hostname" : choisis ton sous-domaine (ex. bot.mondomaine.fr)
#      et pointe-le sur le service  http://localhost:5000
#
# Puis ici :   bash deploy/linux/setup-tunnel.sh <JETON>

set -euo pipefail

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; OFF=$'\033[0m'
step() { echo -e "\n${BOLD}==> $*${OFF}"; }
ok()   { echo -e "    ${GREEN}OK${OFF} $*"; }
die()  { echo -e "\n${RED}ERREUR :${OFF} $*" >&2; exit 1; }

APP_DIR="${APP_DIR:-$HOME/trading-bot}"
TOKEN="${1:-}"

if [[ -z "$TOKEN" ]]; then
    cat <<EOF
${BOLD}Jeton de tunnel manquant.${OFF}

  Usage : bash $0 <JETON>

  Pour obtenir le jeton :
    1. https://one.dash.cloudflare.com
    2. Networks > Tunnels > Create a tunnel > Cloudflared
    3. Copie le jeton affiche (une longue chaine commencant par "ey...")
    4. Dans l'onglet "Public hostname", pointe ton sous-domaine sur
       le service  http://localhost:5000

${YELLOW}Pas de nom de domaine ?${OFF} Utilise Tailscale a la place :
    curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
  Installe ensuite l'application Tailscale sur ton telephone : le dashboard
  sera joignable sur http://<nom-machine>:5000 depuis tes appareils, sans
  jamais etre expose sur Internet.
EOF
    exit 1
fi

# ─── 1. Installation de cloudflared ──────────────────────────────────────────
step "Installation de cloudflared"
if command -v cloudflared >/dev/null; then
    ok "deja installe ($(cloudflared --version 2>&1 | head -1))"
else
    ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m)
    case "$ARCH" in
        amd64|x86_64)  CF_ARCH=amd64  ;;
        arm64|aarch64) CF_ARCH=arm64  ;;
        armhf|armv7l)  CF_ARCH=arm    ;;
        *) die "Architecture non geree : $ARCH" ;;
    esac
    TMP=$(mktemp -d)
    curl -fsSL -o "$TMP/cloudflared.deb" \
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}.deb"
    sudo dpkg -i "$TMP/cloudflared.deb" >/dev/null
    rm -rf "$TMP"
    ok "cloudflared installe"
fi

# ─── 2. Le dashboard doit accepter le trafic du tunnel ───────────────────────
step "Configuration du dashboard"
ENV_FILE="$APP_DIR/src/.env"
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE introuvable. Lance d'abord deploy/linux/install.sh"

PW=$(grep -E '^DASHBOARD_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)
[[ -n "$PW" ]] || die "DASHBOARD_PASSWORD est vide dans $ENV_FILE.
       Le bot refusera de servir le dashboard sur le reseau sans mot de passe."

# Le tunnel se connecte en local : 127.0.0.1 suffit et reste le choix le plus sur.
# TRUST_PROXY indique a Flask qu'un proxy HTTPS est devant (cookies securises,
# vraie IP client dans les logs d'echec de connexion).
sed -i 's|^TRUST_PROXY=.*|TRUST_PROXY=true|' "$ENV_FILE"
ok "TRUST_PROXY active"

# ─── 3. Service systemd pour le tunnel ───────────────────────────────────────
step "Service du tunnel"
sudo cloudflared service install "$TOKEN"
sudo systemctl enable --quiet cloudflared
sudo systemctl restart cloudflared
ok "tunnel installe et demarre"

step "Redemarrage du bot"
sudo systemctl restart trading-bot
ok "bot redemarre"

cat <<EOF

${BOLD}Tunnel actif.${OFF}

  Ton dashboard est joignable sur le sous-domaine configure dans Cloudflare,
  en HTTPS, depuis n'importe ou.

  Mot de passe : ${BOLD}$PW${OFF}

  Etat du tunnel   systemctl status cloudflared
  Logs du tunnel   journalctl -u cloudflared -f

${YELLOW}Verifie maintenant :${OFF} ouvre l'URL depuis ton telephone en 4G (pas en Wi-Fi)
pour confirmer que l'acces fonctionne depuis l'exterieur.
EOF
