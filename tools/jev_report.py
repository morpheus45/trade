"""
Rapport de fiabilite de la couche Jev.

    python tools/jev_report.py

Croise logs/jev_decisions.jsonl avec logs/trades.csv et repond a trois questions
qu'aucun reglage de seuil ne peut trancher a l'avance :

  1. Le filtre est-il exploitable ? (taux d'abstention, repartition des verdicts)
  2. Ou le modele bute-t-il ? (quelles questions restent incertaines)
  3. Jev aurait-il ameliore les resultats ? (win rate et P&L des trades qu'il
     aurait laisses passer contre ceux qu'il aurait ecartes)

Le point 3 n'a de sens qu'apres plusieurs jours en mode `shadow`, ou Jev est
interroge sans influencer les decisions : c'est la seule facon d'observer ce
qu'il aurait fait sans que cela change ce qui s'est passe.
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DECISIONS = ROOT / "logs" / "jev_decisions.jsonl"
TRADES    = ROOT / "logs" / "trades.csv"

# Tolerance pour rapprocher une decision d'un trade sur la meme paire.
FENETRE = timedelta(minutes=10)


def _titre(texte: str) -> None:
    print(f"\n{texte}\n" + "-" * len(texte))


def _parse_dt(valeur: str):
    if not valeur:
        return None
    try:
        dt = datetime.fromisoformat(str(valeur).strip())
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def charger_decisions() -> list[dict]:
    if not DECISIONS.exists():
        print(f"Aucune decision enregistree ({DECISIONS}).")
        print("Lance le bot avec TYPESAFE_API_KEY et JEV_MODE=shadow, puis")
        print("laisse-le tourner quelques jours avant de relancer ce rapport.")
        sys.exit(0)

    lignes = []
    for brute in DECISIONS.read_text(encoding="utf-8").splitlines():
        brute = brute.strip()
        if not brute:
            continue
        try:
            lignes.append(json.loads(brute))
        except json.JSONDecodeError:
            continue    # une ligne tronquee par un arret brutal ne doit pas tout bloquer
    return lignes


def charger_trades() -> list[dict]:
    if not TRADES.exists():
        return []
    try:
        with TRADES.open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 1. Le filtre est-il exploitable ?
# ─────────────────────────────────────────────────────────────────────────────

def section_volume(decisions: list[dict]) -> None:
    _titre("1. Verdicts rendus")

    utilisables = [d for d in decisions if d.get("available")]
    pannes = len(decisions) - len(utilisables)

    print(f"Evaluations enregistrees : {len(decisions)}")
    if pannes:
        part = pannes / len(decisions) * 100
        print(f"Jev injoignable          : {pannes} ({part:.1f} %)")
        if part > 5:
            print("  ! Au-dela de quelques pourcents, verifie la cle et le reseau.")
    if not utilisables:
        print("Aucune reponse exploitable pour l'instant.")
        return

    repartition = Counter(d.get("outcome", "?") for d in utilisables)
    for issue in ("approve", "reject", "abstain"):
        n = repartition.get(issue, 0)
        part = n / len(utilisables) * 100
        libelle = {"approve": "acceptes", "reject": "refuses",
                   "abstain": "abstentions"}[issue]
        print(f"  {libelle:12} : {n:5}  ({part:5.1f} %)")

    abst = repartition.get("abstain", 0) / len(utilisables)
    print()
    if abst > 0.60:
        print("  ! Plus de 60 % d'abstentions : les seuils sont trop severes pour")
        print("    tes donnees, Jev n'apporte presque rien. Baisse")
        print("    JEV_MIN_TOP_PROBABILITY (0.60 -> 0.55) ou augmente JEV_MAX_UNCERTAIN.")
    elif abst < 0.02:
        print("  ! Presque aucune abstention : le garde-fou ne joue pas son role.")
        print("    Verifie que JEV_MIN_TOP_PROBABILITY n'a pas ete mis trop bas.")
    else:
        print("  Taux d'abstention dans une plage saine.")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Ou le modele bute-t-il ?
# ─────────────────────────────────────────────────────────────────────────────

def section_incertitudes(decisions: list[dict]) -> None:
    _titre("2. Questions les moins fiables")

    utilisables = [d for d in decisions if d.get("available")]
    if not utilisables:
        return

    compte = Counter()
    for d in utilisables:
        for question in d.get("uncertain") or []:
            compte[question] += 1

    if not compte:
        print("Aucune question incertaine : toutes les reponses sont franches.")
        return

    print(f"{'question':20} {'incertaine':>10}   part")
    for question, n in compte.most_common():
        part = n / len(utilisables) * 100
        marque = "  <-- a revoir" if part > 40 else ""
        print(f"{question:20} {n:10}  {part:5.1f} %{marque}")

    pires = [q for q, n in compte.items() if n / len(utilisables) > 0.40]
    if pires:
        print()
        print("  Une question incertaine plus de 40 % du temps est generalement mal")
        print("  posee, pas mal repondue : soit l'etat ne contient pas de quoi y")
        print("  repondre, soit elle melange plusieurs jugements et demande a etre")
        print("  decoupee. Concernees : " + ", ".join(pires))


# ─────────────────────────────────────────────────────────────────────────────
# 3. Jev aurait-il ameliore les resultats ?
# ─────────────────────────────────────────────────────────────────────────────

def section_resultats(decisions: list[dict], trades: list[dict]) -> None:
    _titre("3. Effet sur les resultats")

    if not trades:
        print("Aucun trade ferme dans logs/trades.csv — rien a comparer encore.")
        return

    # Rapprocher chaque trade de la decision Jev qui l'a precede.
    par_paire = defaultdict(list)
    for d in decisions:
        if not d.get("available"):
            continue
        dt = _parse_dt(d.get("ts", ""))
        if dt:
            par_paire[d.get("pair")].append((dt, d))
    for paire in par_paire:
        par_paire[paire].sort(key=lambda x: x[0])

    apparies, orphelins = [], 0
    for t in trades:
        if t.get("reason") == "partial_tp":
            continue      # une prise partielle n'est pas un trade distinct
        ouverture = _parse_dt(t.get("opened_at", ""))
        paire = t.get("pair")
        if not ouverture or paire not in par_paire:
            orphelins += 1
            continue
        proches = [d for dt, d in par_paire[paire] if abs(dt - ouverture) <= FENETRE]
        if proches:
            apparies.append((t, proches[-1]))
        else:
            orphelins += 1

    if not apparies:
        print(f"Aucun trade n'a pu etre rapproche d'une decision Jev "
              f"({orphelins} trade(s) sans correspondance).")
        print("C'est normal tant que Jev n'a pas tourne pendant que le bot tradait.")
        return

    print(f"Trades rapproches d'une decision : {len(apparies)}"
          + (f"  ({orphelins} sans correspondance)" if orphelins else ""))

    groupes = defaultdict(list)
    for trade, decision in apparies:
        try:
            pnl = float(trade.get("pnl_usdt") or 0)
        except (TypeError, ValueError):
            pnl = 0.0
        groupes[decision.get("outcome", "?")].append(pnl)

    print()
    print(f"{'verdict Jev':14} {'trades':>7} {'gagnants':>9} {'P&L total':>11} {'moyenne':>9}")
    for issue in ("approve", "reject", "abstain"):
        pnls = groupes.get(issue, [])
        if not pnls:
            continue
        gagnants = sum(1 for p in pnls if p > 0)
        total = sum(pnls)
        print(f"{issue:14} {len(pnls):7} "
              f"{gagnants / len(pnls) * 100:8.1f}% "
              f"{total:+10.2f} "
              f"{total / len(pnls):+8.3f}")

    ecartes = groupes.get("reject", []) + groupes.get("abstain", [])
    retenus = groupes.get("approve", [])

    print()
    if not ecartes:
        print("Jev n'a ecarte aucun trade : rien a conclure sur son apport.")
        return
    if not retenus:
        print("Jev aurait ecarte tous les trades : seuils trop severes.")
        return

    sans_filtre = sum(retenus) + sum(ecartes)   # ce qui s'est reellement passe
    avec_filtre = sum(retenus)                  # si Jev avait filtre
    delta = avec_filtre - sans_filtre

    print(f"Resultat reel (tous les trades)        : {sans_filtre:+.2f} EUR")
    print(f"Resultat si Jev avait filtre           : {avec_filtre:+.2f} EUR")
    print(f"Effet du filtre                        : {delta:+.2f} EUR")
    print()
    if delta > 0:
        print("  Jev aurait ameliore le resultat sur cet echantillon.")
    elif delta < 0:
        print("  Jev aurait degrade le resultat sur cet echantillon.")
    else:
        print("  Effet nul sur cet echantillon.")

    # Signal le plus important du rapport : si les trades ecartes rapportaient
    # MIEUX que les trades retenus, le filtre coupe du bon signal. Le P&L net
    # peut rester positif tout en masquant cela.
    moy_retenus = avec_filtre / len(retenus)
    moy_ecartes = sum(ecartes) / len(ecartes)
    if moy_ecartes > moy_retenus:
        print()
        print(f"  ! Les trades ECARTES rapportaient en moyenne {moy_ecartes:+.3f} EUR,")
        print(f"    mieux que les trades RETENUS ({moy_retenus:+.3f} EUR).")
        print("    Le filtre coupe du bon signal : ne le passe pas en filter/primary.")

    n = len(apparies)
    if n < 30:
        print(f"  ATTENTION : {n} trades seulement. Sous ~30 trades, cet ecart")
        print("  n'est pas distinguable du hasard. Ne change pas JEV_MODE sur cette")
        print("  base : laisse tourner en shadow plus longtemps.")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Divergences avec le pipeline en place
# ─────────────────────────────────────────────────────────────────────────────

def section_divergences(decisions: list[dict]) -> None:
    _titre("4. Divergences avec le pipeline actuel")

    shadow = [d for d in decisions
              if d.get("mode") == "shadow" and d.get("available")]
    if not shadow:
        print("Aucune decision en mode shadow. Ce rapport est le plus utile quand")
        print("Jev tourne en observation (JEV_MODE=shadow).")
        return

    divergentes = [d for d in shadow if d.get("approved") != d.get("acted")]
    part = len(divergentes) / len(shadow) * 100
    print(f"{len(shadow)} evaluations en observation, "
          f"{len(divergentes)} divergences ({part:.1f} %)")

    if not divergentes:
        print("Jev et le pipeline actuel sont toujours d'accord — le filtre")
        print("n'apporterait rien de nouveau en l'etat.")
        return

    print()
    print("Dix dernieres divergences :")
    for d in divergentes[-10:]:
        aurait = "accepte" if d.get("approved") else "refuse"
        a_fait = "trade" if d.get("acted") else "pas de trade"
        motifs = " ; ".join(d.get("reasons") or [])[:70]
        print(f"  {d.get('pair','?'):9} Jev {aurait:8} / bot {a_fait:13} {motifs}")


def main() -> int:
    decisions = charger_decisions()
    trades = charger_trades()

    print("=" * 66)
    print("  FIABILITE DE LA COUCHE JEV")
    print("=" * 66)

    section_volume(decisions)
    section_incertitudes(decisions)
    section_resultats(decisions, trades)
    section_divergences(decisions)

    print("\n" + "=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
