# Bot de trading crypto — paires EUR sur Binance

Bot de trading automatique tournant 24h/24 sur une machine dédiée, avec dashboard
web protégé par mot de passe et accessible depuis le téléphone.

Signal technique multi-indicateurs → filtre XGBoost → décision typée Jev (ou
validation LLM) → gestion du risque (stop ATR, trailing stop, take-profit
partiel, circuit breaker).

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
| `TYPESAFE_API_KEY` | — | Optionnel : active la couche de décision Jev |
| `JEV_MODE` | `shadow` | `off` / `shadow` / `filter` / `primary` — voir plus bas |
| `ALLOW_REMOTE_UPDATE` | `false` | Bouton « Mise à jour » (git pull + redémarrage) |
| `ALLOW_REMOTE_TRAIN` | `true` | Bouton « Entraîner le modèle » |

Les paramètres de stratégie (paires, risque, seuils d'indicateurs, circuit
breaker) sont dans `src/config.py`.

### Sur la clé API Binance

Autorise **Lecture** et **Spot Trading**, rien d'autre. N'active jamais les
retraits : une clé compromise ne doit pas pouvoir sortir de fonds. Restreins-la
à l'adresse IP de la machine quand c'est possible.

---

## Couche de décision Jev (optionnelle)

[Jev](https://docs.typesafe.ai) est un modèle « System One » de TypeSafe AI : au lieu
de générer du texte, il répond à des **questions typées** par des valeurs
structurées assorties de **probabilités calibrées**, en ~120 ms.

Le bot demandait jusqu'ici à un LLM d'écrire une analyse, puis reparsait ce texte
pour en extraire une décision. Trois défauts sur un bot de trading : plusieurs
secondes par paire, un parsing fragile, et aucune mesure exploitable de
l'incertitude. Jev remplace ce maillon.

### Ce qu'on lui demande

Plutôt qu'un « faut-il acheter ? », le bot pose huit questions indépendantes et
les combine **dans le code** ([`src/jev_decision.py`](src/jev_decision.py)) :

| Question | Type | Rôle |
|---|---|---|
| `regime` | Choice | uptrend / downtrend / range / choppy |
| `setup_quality` | Score 0–4 | qualité de cette entrée précise |
| `entry_timing` | Score 0–2 | le mouvement est-il déjà fait ? |
| `trend_alignment` | Noul | 1 h et 4 h pointent dans le même sens |
| `overextended` | Noul | risque de retour à la moyenne |
| `volume_confirms` | Noul | le volume confirme le mouvement |
| `elevated_risk` | Noul | conditions anormalement risquées |
| `better_to_wait` | Noul | contrôle : la même question posée à l'envers |

Toutes partent dans **un seul appel**, évaluées en parallèle et isolément. Quand
tu veux durcir ou assouplir le filtre, tu changes un seuil dans `config.py` — pas
un prompt.

La `confidence` pilote l'action : sous `JEV_MIN_CONFIDENCE`, Jev dit en substance
« je ne sais pas », et le bot s'abstient au lieu de deviner. Elle module aussi la
taille de position, dans les bornes `JEV_SIZE_MIN`/`JEV_SIZE_MAX`.

### Les quatre modes

On n'introduit pas une nouvelle source de décision dans un système qui engage de
l'argent sans l'avoir observée d'abord.

| `JEV_MODE` | Comportement |
|---|---|
| `off` | Jev n'est pas appelé. |
| **`shadow`** (défaut) | Jev est interrogé et journalisé, mais **ne change aucune décision**. |
| `filter` | Jev s'ajoute comme veto au pipeline existant. |
| `primary` | Jev décide seul ; le LLM n'est plus appelé. |

**Commence en `shadow`.** Chaque évaluation part dans
`logs/jev_decisions.jsonl`, avec ce que Jev aurait décidé et ce que le bot a
réellement fait. Après quelques jours, compare :

```bash
python -c "import json;rows=[json.loads(l) for l in open('logs/jev_decisions.jsonl',encoding='utf-8')];d=[r for r in rows if r['available'] and r['approved']!=r['acted']];print(f'{len(rows)} evaluations, {len(d)} divergences');[print(' ',r['pair'],r['regime'],r['reasons']) for r in d[:10]]"
```

Passe à `filter`, puis éventuellement `primary`, seulement si ces divergences te
donnent raison.

### Fiabilité des décisions

**Jev n'est pas déterministe, et TypeSafe le mesure publiquement** : sur un cas
limite, il rejoue son label majoritaire 90,8 % du temps et change d'avis sur 2
questions sur 8. Un bot de trading qui agirait sur ce genre de réponse prendrait
des décisions différentes sur les mêmes données.

Quatre mécanismes évitent cela. Les trois premiers viennent des mesures publiées
par TypeSafe :

**1. Plancher de probabilité.** Une réponse n'est exploitée que si l'option
retenue porte au moins `JEV_MIN_TOP_PROBABILITY` (0,60) de la masse de
probabilité. C'est le seuil pour lequel TypeSafe mesure **99,2 % d'accord entre
exécutions**, contre 90,8 % sans. Le coût : environ un quart des réponses sont
écartées.

**2. Bande d'incertitude sur les Noul.** Un Noul ne porte pas de `confidence` —
c'est sa valeur qui exprime l'incertitude. Entre 0,30 et 0,70, la réponse ne dit
ni oui ni non et ne déclenche rien, ni veto ni validation.

**3. Question de contrôle.** `better_to_wait` pose la même question à l'envers.
Si Jev juge le setup bon *et* qu'il vaudrait mieux attendre, ses deux réponses se
contredisent : rien de fiable à en tirer.

**4. Accord entre tirages.** Avec `JEV_CONSENSUS_SAMPLES=3`, la question est
posée trois fois et l'accord unanime est exigé. Des tirages divergents valent
abstention — c'est précisément le cas où il ne faut pas agir. Coût : ×3 sur un
budget déjà négligeable, latence ~0,4 s.

### Trois états, pas deux

La distinction compte :

| Verdict | Sens | Effet |
|---|---|---|
| `approve` | conditions réunies | trade autorisé, mise modulée |
| `reject` | condition disqualifiante **établie de façon fiable** | trade refusé |
| `abstain` | Jev n'a rien de fiable à dire | pas un veto — voir ci-dessous |

Une abstention n'est **pas** un refus. En mode `filter`, elle laisse le pipeline
existant décider. En mode `primary`, elle déclenche `JEV_ON_ERROR` exactement
comme une panne. Confondre les deux reviendrait à traiter « je ne sais pas »
comme « non », et à croire le filtre plus informatif qu'il ne l'est.

Un veto ne se déclenche jamais sur une réponse jugée non fiable : bloquer un
trade sur une réponse instable serait aussi arbitraire que d'en prendre un.

### Vérifier la fiabilité sur tes données

Des seuils sévères ne sont pas de la fiabilité, juste de la sévérité. La
fiabilité se mesure :

```bash
python tools/jev_report.py
```

Après quelques jours en `shadow`, le rapport répond à quatre questions :

1. **Le filtre est-il exploitable ?** Plus de 60 % d'abstentions = seuils trop
   sévères ; moins de 2 % = le garde-fou ne joue pas.
2. **Où le modèle bute-t-il ?** Une question incertaine plus de 40 % du temps est
   généralement mal posée, pas mal répondue.
3. **Jev aurait-il amélioré les résultats ?** P&L réel contre P&L si Jev avait
   filtré. Le rapport alerte si les trades **écartés** rapportaient mieux que les
   trades retenus — le signe que le filtre coupe du bon signal.
4. **Diverge-t-il du pipeline actuel ?** S'il est toujours d'accord, il n'apporte
   rien.

Sous ~30 trades appariés, le rapport te dit explicitement que l'écart n'est pas
distinguable du hasard. **Ne change pas `JEV_MODE` sur un échantillon plus petit.**

### Réglage recommandé avant l'argent réel

```bash
JEV_MODE=filter
JEV_CONSENSUS_SAMPLES=3
JEV_ON_ERROR=fallback
```

`filter` plutôt que `primary` : Jev peut écarter un trade, jamais en autoriser un
que le pipeline existant aurait refusé.

### Coût

Environ 700 tokens d'entrée par évaluation, à 42 $ le milliard. Jev n'est
interrogé qu'après les filtres technique et ML, donc au pire **~2 $/mois** sur
huit paires — en pratique bien moins.

Avec `JEV_CONSENSUS_SAMPLES=3`, le coût est multiplié par trois : **~6 $/mois au
pire**. C'est le meilleur rapport fiabilité/prix de tout le dispositif.

### Si Jev tombe

Le bot ne s'arrête jamais à cause de Jev :

- **clé invalide ou refusée** → couche désactivée, message explicite, pipeline habituel ;
- **panne passagère** (timeout, rate limit, 5xx) → après 3 échecs, mise en sommeil 15 min ;
- **appel échoué** → `JEV_ON_ERROR` décide : `fallback` (pipeline habituel) ou `skip` (aucun trade).

Sans `TYPESAFE_API_KEY`, ou sans le paquet `typesafe-sdk`, la couche reste
inactive et le bot se comporte exactement comme avant.

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
