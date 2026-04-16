# Guide Oracle Cloud — Serveur GRATUIT H24

## Pourquoi Oracle Cloud ?
- **100% gratuit, pour toujours** (pas une période d'essai)
- Serveur Linux ARM avec 4 CPU et 24 Go de RAM
- Parfait pour faire tourner le bot 24h/24, 7j/7
- Même si ton PC est éteint

---

## Étape 1 — Créer un compte Oracle Cloud (5 min)

1. Va sur **https://cloud.oracle.com/free**
2. Clique **Start for free**
3. Remplis le formulaire (email, mot de passe, pays)
4. Ils demandent une carte bancaire **pour vérification** — rien n'est prélevé
5. Confirme ton email → compte créé

---

## Étape 2 — Créer le serveur gratuit (5 min)

1. Connecte-toi sur **cloud.oracle.com**
2. Dans le menu → **Compute** → **Instances** → **Create instance**
3. Configure :
   - **Name** : `trading-bot`
   - **Image** : Ubuntu 22.04 (clique *Change image*)
   - **Shape** : clique *Change shape* → **Ampere** → **VM.Standard.A1.Flex**
     - OCPUs : **4** | Memory : **24 GB** ← gratuit !
4. **SSH Keys** : clique *Generate a key pair* → télécharge la clé privée
5. Clique **Create**
6. Attends 2 minutes → note l'**adresse IP publique** affichée

---

## Étape 3 — Ouvrir le port 5000 (dashboard)

1. Clique sur ton instance → **Subnet** → **Security List**
2. **Add Ingress Rules** :
   - Source : `0.0.0.0/0`
   - Port : `5000`
   - Protocol : TCP
3. Sauvegarde

---

## Étape 4 — Se connecter et lancer le bot (2 min)

### Sur Windows — utilise PowerShell :
```powershell
# Remplace par l'IP de ton serveur Oracle
ssh -i chemin\vers\ta_cle.key ubuntu@IP_DU_SERVEUR
```

### Une fois connecté, colle cette commande :
```bash
curl -sSL https://raw.githubusercontent.com/morpheus45/trade/main/install_cloud.sh | sudo bash
```

Le script fait **tout automatiquement** :
- Installe Python et les dépendances
- Clone le code depuis GitHub
- Te demande tes clés API (Binance, Telegram, Claude)
- Entraîne le modèle ML
- Configure le démarrage automatique
- Ouvre le port du dashboard

---

## Étape 5 — Installer la PWA sur ton Android

1. Ouvre Chrome sur ton téléphone
2. Va sur `http://IP_DU_SERVEUR:5000`
3. Menu Chrome (⋮) → **Ajouter à l'écran d'accueil**
4. L'app s'installe → icône sur ton écran

---

## Commandes utiles (sur le serveur)

```bash
# Voir l'état du bot
sudo systemctl status trading-bot

# Voir les logs en temps réel
sudo journalctl -fu trading-bot

# Redémarrer le bot
sudo systemctl restart trading-bot

# Mettre à jour depuis GitHub
cd /opt/trading-bot && sudo git pull && sudo systemctl restart trading-bot
```

---

## Telegram — commandes disponibles

Envoie ces commandes à ton bot Telegram :

| Commande | Action |
|---|---|
| `/status` | Capital, P&L, positions ouvertes |
| `/positions` | Détail de chaque position |
| `/stats` | Win rate, profit factor, expectancy |
| `/pause` | Suspend les nouvelles entrées |
| `/resume` | Reprend le trading |
| `/help` | Liste des commandes |

---

## Récapitulatif

```
cloud.oracle.com → Créer compte (gratuit)
                 → Créer VM Ubuntu ARM (gratuit H24)
                 → Copier l'IP publique
                 → SSH sur le serveur
                 → curl ... | sudo bash
                 → Entrer les clés API
                 → Bot lancé automatiquement
```

**Résultat : bot autonome, 24h/24, 0€/mois.**
