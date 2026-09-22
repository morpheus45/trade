#!/usr/bin/env bash
#
# Installation du bot de trading sur une machine Linux dediee (Debian/Ubuntu).
#
#   curl -fsSL https://raw.githubusercontent.com/morpheus45/trade/main/deploy/linux/install.sh | bash
#   ou, depuis un depot deja clone :  bash deploy/linux/install.sh
#
# Le script installe Python, cree l'environnement, met en place un service
# systemd qui demarre au boot et se relance en cas de crash, puis s'arrete pour
# te laisser renseigner tes cles.

set -euo pipefail

REPO_URL="https://github.com/morpheus45/trade.git"
APP_DIR="${APP_DIR:-$HOME/trading-bot}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="trading-bot"

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; OFF=$'\033[0m'
step()  { echo -e "\n${BOLD}==> $*${OFF}"; }
ok()    { echo -e "    ${GREEN}OK${OFF} $*"; }
warn()  { echo -e "    ${YELLOW}!${OFF}  $*"; }
die()   { echo -e "\n${RED}ERREUR :${OFF} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] && die "Ne lance pas ce script en root. Utilise ton compte utilisateur (sudo sera demande si besoin)."
command -v systemctl >/dev/null || die "systemd est requis (Debian, Ubuntu, Fedora...)."

# ─── 1. Dependances systeme ──────────────────────────────────────────────────
step "Dependances systeme"
if command -v apt-get >/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates
elif command -v dnf >/dev/null; then
    sudo dnf install -y -q python3 python3-pip git curl
else
    warn "Gestionnaire de paquets inconnu — verifie que python3, venv, pip et git sont installes."
fi
PY_VER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
ok "Python $PY_VER"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' \
    || die "Python 3.10+ requis (detecte : $PY_VER)."

# ─── 2. Code source ──────────────────────────────────────────────────────────
step "Code source dans $APP_DIR"
if [[ -d "$APP_DIR/.git" ]]; then
    git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
    git -C "$APP_DIR" checkout --quiet "$BRANCH"
    git -C "$APP_DIR" pull --ff-only --quiet
    ok "depot mis a jour"
else
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    ok "depot clone"
fi
mkdir -p "$APP_DIR"/{data,logs,models}

# ─── 3. Environnement Python ─────────────────────────────────────────────────
step "Environnement Python"
[[ -x "$APP_DIR/venv/bin/python" ]] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
ok "dependances installees"

# ─── 4. Configuration ────────────────────────────────────────────────────────
step "Configuration"
ENV_FILE="$APP_DIR/src/.env"
if [[ -f "$ENV_FILE" ]]; then
    ok ".env deja present, conserve tel quel"
    NEW_ENV=0
else
    cp "$APP_DIR/src/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"

    # Mot de passe du dashboard genere ici : personne n'a a en inventer un, et
    # il est assez long pour resister a une attaque par force brute.
    GEN_PW=$(python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(24)))')
    sed -i "s|^DASHBOARD_PASSWORD=.*|DASHBOARD_PASSWORD=$GEN_PW|" "$ENV_FILE"
    ok ".env cree avec un mot de passe genere"
    NEW_ENV=1
fi

# ─── 5. Service systemd ──────────────────────────────────────────────────────
step "Service systemd"
UNIT_SRC="$APP_DIR/deploy/linux/trading-bot.service"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"
sed -e "s|__USER__|$USER|g" -e "s|__APP_DIR__|$APP_DIR|g" "$UNIT_SRC" \
    | sudo tee "$UNIT_DST" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --quiet "$SERVICE_NAME"
ok "service installe et active au demarrage"

# ─── 6. Recapitulatif ────────────────────────────────────────────────────────
cat <<EOF

${BOLD}Installation terminee.${OFF}

  Dossier    : $APP_DIR
  Config     : $ENV_FILE
  Service    : $SERVICE_NAME

EOF

if [[ $NEW_ENV -eq 1 ]]; then
    cat <<EOF
${YELLOW}${BOLD}Avant de demarrer, ouvre $ENV_FILE et renseigne :${OFF}

  BINANCE_API_KEY / BINANCE_API_SECRET   (lecture + spot trading, JAMAIS retrait)
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  (optionnel)
  GROQ_API_KEY                           (optionnel, gratuit)

  PAPER_TRADING reste a ${BOLD}true${OFF} : le bot simule sans passer d'ordre reel.
  Ne passe a false qu'apres avoir verifie son comportement pendant plusieurs jours.

  Ton mot de passe de dashboard :
      ${BOLD}$(grep '^DASHBOARD_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)${OFF}
  (note-le maintenant, il est aussi dans $ENV_FILE)

EOF
fi

cat <<EOF
${BOLD}Commandes :${OFF}

  Demarrer         sudo systemctl start $SERVICE_NAME
  Arreter          sudo systemctl stop $SERVICE_NAME
  Etat             systemctl status $SERVICE_NAME
  Logs en direct   journalctl -u $SERVICE_NAME -f

  Dashboard local  http://127.0.0.1:5000

Pour y acceder depuis ton telephone, installe le tunnel :
  bash $APP_DIR/deploy/linux/setup-tunnel.sh
EOF
