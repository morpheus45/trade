# Bot de trading crypto — paires EUR sur Binance

Bot de trading automatique tournant 24h/24 sur une machine dédiée, avec dashboard
web protégé par mot de passe et accessible depuis le téléphone.

Signal technique multi-indicateurs → filtre XGBoost → validation par IA → gestion
du risque (stop ATR, trailing stop, take-profit partiel, circuit breaker).

> **Le mode par défaut est `PAPER_TRADING=true` : le bot simule sans passer
> d'ordre réel.** Laisse-le tourner plusieurs jours dans ce mode et vérifie ses
> décisions avant d'envisager de passer en argent réel.

---

## Installation

### Windows (machine dédiée)

1. Télécharge le dépôt, clic droit sur `INSTALL.ps1` → **Exécuter avec PowerShell**
2. Le script installe tout, génère un mot de passe de dashboard et affiche un
   récapitulatif (également enregistré dans `BOT_INFO.txt` sur le bureau public)
3. Renseigne tes clés dans `C:\ProgramData\trading-bot\src\.env`
4. Redémarre le bot :

```powershell
Stop-ScheduledTask -TaskName TradingBot ; Start-ScheduledTask -TaskName TradingBot
```

Le bot démarre **au boot de la machine**, sans qu'une session soit ouverte, et se
relance automatiquement s'il s'arrête.

### Linux (machine dédiée)

```bash
curl -fsSL https://raw.githubusercontent.com/morpheus45/trade/main/deploy/linux/install.sh | bash
```

Puis renseigne `~/trading-bot/src/.env` et démarre :

```bash
sudo systemctl start trading-bot
```

### Docker (n'importe quelle machine)

```bash
git clone https://github.com/morpheus45/trade.git && cd trade
cp src/.env.example src/.env    # puis remplis-le, DASHBOARD_PASSWORD compris
docker compose up -d
```

---

## Accès depuis le téléphone

N'ouvre **jamais** le port 5000 sur ta box. Deux options propres :

| | Cloudflare Tunnel | Tailscale |
|---|---|---|
| Ce que tu obtiens | URL HTTPS publique (`bot.tondomaine.fr`) | IP privée entre tes appareils |
| Nom de domaine requis | oui | non |
| Application sur le téléphone | non | oui |
| Exposé sur Internet | oui, protégé par mot de passe | non, jamais |
| Ports ouverts sur la box | aucun | aucun |

**Windows**

```powershell
# Cloudflare Tunnel (jeton obtenu sur one.dash.cloudflare.com)
powershell -ExecutionPolicy Bypass -File "C:\ProgramData\trading-bot\deploy\windows\setup-tunnel.ps1" -Token "ey..."

# ou Tailscale, sans nom de domaine
powershell -ExecutionPolicy Bypass -File "C:\ProgramData\trading-bot\deploy\windows\setup-tunnel.ps1" -UseTailscale
```

**Linux**

```bash
bash ~/trading-bot/deploy/linux/setup-tunnel.sh "ey..."
```

**Docker**

Mets le jeton dans `src/.env` sous `TUNNEL_TOKEN=`, puis :

```bash
docker compose --profile tunnel up -d
```

Côté Cloudflare, le service du tunnel doit pointer sur `http://bot:5000`.

---

## Configuration

Tout se passe dans `src/.env` (copié depuis `src/.env.example`).

| Variable | Défaut | Rôle |
|---|---|---|
| `PAPER_TRADING` | `true` | `false` = ordres réels avec ton argent |
| `DASHBOARD_PASSWORD` | — | **Obligatoire** dès que le dashboard sort de la machine |
| `DASHBOARD_HOST` | `127.0.0.1` | `0.0.0.0` pour accepter le réseau |
| `TRUST_PROXY` | `false` | `true` uniquement derrière un tunnel / reverse proxy |
| `BINANCE_API_KEY` / `_SECRET` | — | Droits « Lecture » + « Spot Trading », **jamais** les retraits |
| `GROQ_API_KEY` | — | Optionnel, gratuit. Sans lui l'analyse IA est désactivée |
| `TELEGRAM_BOT_TOKEN` / `_CHAT_ID` | — | Optionnel : alertes et commandes `/status` `/pause` |
| `ALLOW_REMOTE_UPDATE` | `false` | Bouton « Mise à jour » (git pull + redémarrage) |
| `ALLOW_REMOTE_TRAIN` | `true` | Bouton « Entraîner le modèle » |

Les paramètres de stratégie (paires, risque, seuils d'indicateurs, circuit
breaker) sont dans `src/config.py`.

### Sur la clé API Binance

Autorise **Lecture** et **Spot Trading**, rien d'autre. N'active jamais les
retraits : une clé compromise ne doit pas pouvoir sortir de fonds. Restreins-la
à l'adresse IP de la machine quand c'est possible.

---

## Fonctionnement

Un seul processus (`src/main.py`) fait tourner la boucle de trading et sert le
dashboard. Cela permet au dashboard de lire les positions **en temps réel** dans
l'objet du bot, et non dans des fichiers CSV relus après coup.

```
main.py
├── TradingBot (thread principal) ──── boucle toutes les 30 s
│   ├── gestion des positions ouvertes  (trailing stop, TP partiel, SL, TP)
│   ├── recherche d'entrées             (signal → ML → sentiment → validation IA)
│   └── sauvegarde d'état               → data/state.json
├── Dashboard Flask/Waitress (thread)   → port 5000, protégé par mot de passe
└── Contrôleur Telegram (thread)        → /status /pause /resume /stats
```

### Persistance

Positions ouvertes, solde, historique, trailing stops et circuit breaker sont
écrits dans `data/state.json` à chaque changement, de façon atomique. Après une
coupure de courant ou un redémarrage, le bot **reprend ses positions** au lieu de
les oublier.

C'est indispensable en mode réel : sans cela, un reboot laisserait une position
ouverte sur Binance que le bot ne surveillerait plus jamais.

### Fichiers produits

| Chemin | Contenu |
|---|---|
| `data/state.json` | État du bot (positions, solde, historique) |
| `data/secret_key` | Clé de signature des sessions du dashboard |
| `logs/bot.log` | Journal principal |
| `logs/trades.csv` | Trades fermés |
| `logs/portfolio.csv` | Instantanés horaires du portefeuille |
| `models/xgboost_model.json` | Modèle ML entraîné |

---

## Exploitation

**Windows** (PowerShell administrateur)

```powershell
Get-ScheduledTask   -TaskName TradingBot      # état
Start-ScheduledTask -TaskName TradingBot      # démarrer
Stop-ScheduledTask  -TaskName TradingBot      # arrêter
Get-Content "C:\ProgramData\trading-bot\logs\bot.log" -Tail 50 -Wait
```

**Linux**

```bash
systemctl status trading-bot
sudo systemctl restart trading-bot
journalctl -u trading-bot -f
```

**Docker**

```bash
docker compose ps
docker compose logs -f bot
docker compose restart bot
```

**Sonde de santé** : `GET /healthz` répond sans authentification et ne révèle
aucune donnée financière — utilisable par un service de monitoring externe.

---

## Dépannage

**Le dashboard refuse de démarrer, message « REFUS DE DEMARRER »**
`DASHBOARD_HOST` expose le dashboard sur le réseau mais `DASHBOARD_PASSWORD` est
vide. Définis un mot de passe, ou repasse `DASHBOARD_HOST` à `127.0.0.1`.

**`Mot de passe incorrect` en boucle, puis blocage**
Après 5 échecs, l'IP est bloquée 15 minutes. Le mot de passe est dans `src/.env`.

**Binance répond `HTTP 451` ou `Service unavailable from a restricted location`**
L'adresse IP sortante est dans une région bloquée par Binance. C'est le cas si la
machine passe par un VPN, ou si tu déportes le bot sur un hébergeur hors UE.

**Le bot ne redémarre pas après une coupure de courant (Windows)**
Vérifie que la tâche est bien déclenchée au démarrage et non à l'ouverture de
session : `(Get-ScheduledTask -TaskName TradingBot).Triggers`

**« Solde EUR nul » au démarrage en mode LIVE**
Les clés API sont absentes, invalides, ou le compte n'a pas d'EUR disponible.

---

## Avertissement

Le trading automatisé de cryptomonnaies comporte un risque de perte en capital.
Ce bot peut perdre de l'argent. Les performances passées, en simulation comme en
réel, ne préjugent pas des performances futures. N'engage que des sommes que tu
peux te permettre de perdre, et commence toujours en mode paper.
