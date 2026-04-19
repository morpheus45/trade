# Trading Bot — Guide complet

Bot de trading crypto autonome : XGBoost ML + Groq AI + Binance EUR  
Concu pour Binance France (paires EUR, conforme MiCA).

---

## LIEN DU REPO

```
https://github.com/morpheus45/trade
```

---

## PREMIERE INSTALLATION (nouvel ordinateur)

### Etape 1 — Prerequis
- Windows 10/11
- [Python 3.11+](https://www.python.org/downloads/) — cocher "Add to PATH"
- [Git](https://git-scm.com/download/win)

### Etape 2 — Cloner le repo

```bat
git clone https://github.com/morpheus45/trade.git trading-bot
cd trading-bot
```

### Etape 3 — Creer l'environnement virtuel et installer les packages

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install groq
```

### Etape 4 — Configurer les cles API

Copie le fichier modele et remplis tes vraies valeurs :

```bat
copy src\.env.example src\.env
notepad src\.env
```

Remplis ces champs dans le .env :

| Cle | Ou la trouver |
|-----|--------------|
| BINANCE_API_KEY / SECRET | binance.com -> Profil -> API Management |
| GROQ_API_KEY | console.groq.com/keys (GRATUIT) |
| TELEGRAM_BOT_TOKEN | Telegram -> @BotFather -> /newbot |
| TELEGRAM_CHAT_ID | Envoie un message a ton bot puis va sur : https://api.telegram.org/bot<TOKEN>/getUpdates |
| PAPER_TRADING | false = argent reel / true = simulation |

### Etape 5 — Entrainer le modele ML

```bat
venv\Scripts\python.exe src\train_xgboost.py
```
Duree : 5-15 minutes. Telecharge 24 mois de donnees historiques EUR.

### Etape 6 — Lancer le bot

```bat
venv\Scripts\python.exe src\run_forever.py
```

Le bot demarre, se connecte a Binance, et envoie un message Telegram de confirmation.

---

## REMISE EN ROUTE APRES CRASH OU REBOOT

### Methode manuelle

```bat
cd trading-bot
venv\Scripts\python.exe src\run_forever.py
```

Le watchdog integre (`run_forever.py`) redémarre automatiquement le bot et le dashboard en cas de crash — jusqu'a 20 fois en 5 minutes.

### Methode automatique au demarrage Windows (recommande)

Cree une tache planifiee Windows qui relance le bot a chaque demarrage :

```bat
schtasks /create /tn "TradingBot" /tr "C:\trading-bot\venv\Scripts\python.exe C:\trading-bot\src\run_forever.py" /sc onstart /ru SYSTEM /f
```

Ou double-clique sur `setup_autostart.bat` si present dans le repo.

---

## MISE A JOUR

Pour recuperer les dernieres corrections depuis GitHub :

```bat
cd trading-bot
git pull
venv\Scripts\python.exe src\run_forever.py
```

---

## COMMANDES TELEGRAM

Une fois le bot lance, envoie ces commandes depuis Telegram :

| Commande | Effet |
|----------|-------|
| /status | Capital, positions, P&L |
| /positions | Positions ouvertes en temps reel |
| /stats | Statistiques des trades fermes |
| /pause | Suspend le trading (urgence) |
| /resume | Reprend le trading |
| /help | Liste toutes les commandes |

---

## ARCHITECTURE

```
run_forever.py          <- Watchdog : garde bot + dashboard vivants 24/7
src/
  bot_trading.py        <- Boucle principale (scan, entrees, sorties)
  strategy.py           <- Signaux RSI/MACD/BB/EMA/ADX
  ai_model.py           <- Filtre XGBoost ML
  claude_analysis.py    <- Validation IA (Groq llama-3.3-70b, gratuit)
  autonomous_brain.py   <- Cerveau decisionnaire avec memoire
  risk_management.py    <- Sizing, trailing stop, circuit breaker
  exchange.py           <- Interface Binance via CCXT
  github_reporter.py    <- Push stats.json vers GitHub Pages (15 min)
  dashboard.py          <- Dashboard local http://localhost:5000
  train_xgboost.py      <- Entrainement ML (a relancer 1x/mois)
  .env                  <- Tes cles API (JAMAIS dans git)
  .env.example          <- Modele vide a copier
```

---

## PAIRES TRADEES (France / MiCA)

BTC/EUR — ETH/EUR — BNB/EUR — SOL/EUR — XRP/EUR — DOGE/EUR — ADA/EUR — LTC/EUR

---

## TABLEAU DE BORD EN LIGNE

https://morpheus45.github.io/trade/
