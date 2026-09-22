"""
Registre de modeles : champion contre challenger.

Pourquoi ce detour plutot qu'un simple reentrainement
-----------------------------------------------------
Reentrainer et ecraser le modele en place suppose que le nouveau est
meilleur. Rien ne le garantit : un mois de marche atypique produit un modele
qui colle a ce mois-la et generalise moins bien. Sans comparaison, on ne s'en
apercevrait qu'apres coup, en pertes.

Ici, le nouveau modele (« challenger ») doit BATTRE celui en place
(« champion ») sur une fenetre recente qu'aucun des deux n'a vue a
l'entrainement, et le battre d'une marge. Sinon le champion reste en place.

Pourquoi on n'entraine PAS sur les trades du bot
------------------------------------------------
C'est l'idee qui vient naturellement — « qu'il apprenne de ses trades » — et
c'est un piege a trois detentes :

1. Volume. Le bot tient une position a la fois. Il produit quelques trades
   par semaine ; apres un mois, une vingtaine d'exemples. De quoi
   surapprendre, pas de quoi apprendre. Le modele actuel est entraine sur
   ~87 000 echantillons.

2. Censure. On ne connait le resultat que des trades PRIS. Ceux que le filtre
   a ecartes n'ont aucune etiquette : on ignore s'ils auraient gagne.
   S'entrainer sur les seuls trades pris apprend au modele a reproduire ses
   choix passes, pas a faire de meilleurs choix.

3. Boucle de retroaction. Le modele decide, ses decisions deviennent ses
   donnees d'entrainement, qui renforcent ses biais. Il se confirme lui-meme.

Les trades du bot servent donc a MESURER le modele en place — la confiance
annoncee predit-elle vraiment le resultat ? (voir tools/model_report.py) —
jamais a l'entrainer. L'entrainement se fait sur l'historique public de
l'exchange : large, non censure, et contenant aussi les configurations que le
bot a refusees.
"""
import json
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import xgboost as xgb

import config
from indicators import ML_FEATURES

logger = logging.getLogger(__name__)

#: Part la plus RECENTE des donnees, mise de cote et jamais vue a
#: l'entrainement. C'est sur elle que se joue la comparaison : un modele doit
#: prouver qu'il generalise au marche d'aujourd'hui, pas a celui d'il y a deux ans.
HOLDOUT_RATIO = 0.15

#: Marge que le challenger doit prendre sur le champion pour le remplacer.
#: Sans marge, on remplacerait le modele a chaque bruit de mesure, et chaque
#: remplacement est un changement de comportement sur un systeme qui engage
#: de l'argent.
MARGE_PROMOTION = 0.01

#: Plancher absolu : en dessous, le challenger n'est pas promu meme s'il bat
#: le champion — mieux vaut un modele mediocre connu qu'un nouveau modele
#: mediocre dont on ne sait rien.
AUC_PLANCHER = 0.53


def _chemins() -> tuple[Path, Path, Path]:
    modele = Path(config.MODEL_PATH)
    archives = modele.parent / "archive"
    registre = modele.parent / "registry.json"
    return modele, archives, registre


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation
# ─────────────────────────────────────────────────────────────────────────────

def decouper_holdout(X: np.ndarray, y: np.ndarray,
                     ratio: float = HOLDOUT_RATIO) -> tuple:
    """
    Separe les donnees en (entrainement, fenetre recente).

    Le decoupage est CHRONOLOGIQUE, jamais aleatoire : melanger placerait des
    bougies futures dans l'entrainement et des passees dans le test, ce qui
    donnerait un score flatteur et faux.
    """
    if len(X) < 100:
        raise ValueError(f"{len(X)} echantillons : trop peu pour evaluer quoi que ce soit")
    coupe = int(len(X) * (1 - ratio))
    return X[:coupe], y[:coupe], X[coupe:], y[coupe:]


def evaluer(booster: xgb.Booster | None, X: np.ndarray, y: np.ndarray) -> dict:
    """
    Mesure un modele sur une fenetre donnee.

    On regarde deux choses : l'AUC, qui dit si le modele sait ordonner les
    situations, et la precision AU SEUIL REELLEMENT UTILISE en production.
    La seconde compte davantage : un modele peut bien ordonner et se tromper
    systematiquement au-dessus du seuil ou le bot agit.
    """
    if booster is None:
        return {"auc": 0.0, "precision": 0.0, "signaux": 0, "echantillons": len(y)}

    from ai_model import CONFIDENCE_THRESHOLD
    from sklearn.metrics import roc_auc_score

    dmat = xgb.DMatrix(X, feature_names=ML_FEATURES)
    probas = booster.predict(dmat)

    try:
        auc = float(roc_auc_score(y, probas))
    except ValueError:
        # Une fenetre sans aucun positif rend l'AUC indefinie.
        auc = 0.0

    au_dessus = probas >= CONFIDENCE_THRESHOLD
    n_signaux = int(au_dessus.sum())
    precision = float(y[au_dessus].mean()) if n_signaux else 0.0

    return {
        "auc": round(auc, 4),
        "precision": round(precision, 4),
        "signaux": n_signaux,
        "echantillons": int(len(y)),
        "taux_base": round(float(y.mean()), 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Registre
# ─────────────────────────────────────────────────────────────────────────────

def lire_registre() -> dict:
    _, _, registre = _chemins()
    if not registre.exists():
        return {"champion": None, "historique": []}
    try:
        return json.loads(registre.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"[registre] illisible ({exc}) — reconstruit a neuf")
        return {"champion": None, "historique": []}


def ecrire_registre(donnees: dict) -> None:
    _, _, registre = _chemins()
    registre.parent.mkdir(parents=True, exist_ok=True)
    tmp = registre.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(donnees, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(registre)


def charger_champion() -> xgb.Booster | None:
    modele, _, _ = _chemins()
    if not modele.exists():
        return None
    try:
        booster = xgb.Booster()
        booster.load_model(str(modele))
        return booster
    except Exception as exc:
        logger.error(f"[registre] champion illisible : {exc}")
        return None


def _archiver(booster: xgb.Booster, mesures: dict, etiquette: str) -> str:
    _, archives, _ = _chemins()
    archives.mkdir(parents=True, exist_ok=True)
    nom = (f"{etiquette}-{datetime.now(timezone.utc):%Y%m%d-%H%M}"
           f"-auc{mesures['auc']:.4f}.json")
    booster.save_model(str(archives / nom))
    return nom


def _elaguer_archives(garder: int = 10) -> None:
    """Conserve les N archives les plus recentes ; un modele pese ~1 Mo."""
    _, archives, _ = _chemins()
    if not archives.exists():
        return
    fichiers = sorted(archives.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for vieux in fichiers[garder:]:
        try:
            vieux.unlink()
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Promotion
# ─────────────────────────────────────────────────────────────────────────────

def arbitrer(champion: xgb.Booster | None, challenger: xgb.Booster,
             X_test: np.ndarray, y_test: np.ndarray) -> tuple[bool, str, dict, dict]:
    """
    Compare les deux modeles sur la fenetre recente.
    Retourne (promouvoir, motif, mesures_champion, mesures_challenger).
    """
    m_champ = evaluer(champion, X_test, y_test)
    m_chall = evaluer(challenger, X_test, y_test)

    if champion is None:
        if m_chall["auc"] < AUC_PLANCHER:
            return (False,
                    f"aucun champion, mais le challenger reste sous le plancher "
                    f"(AUC={m_chall['auc']:.4f} < {AUC_PLANCHER}) — pas de filtre "
                    f"vaut mieux qu'un filtre au hasard",
                    m_champ, m_chall)
        return True, "aucun champion en place", m_champ, m_chall

    if m_chall["auc"] < AUC_PLANCHER:
        return (False,
                f"challenger sous le plancher (AUC={m_chall['auc']:.4f} < {AUC_PLANCHER})",
                m_champ, m_chall)

    ecart = m_chall["auc"] - m_champ["auc"]
    if ecart < MARGE_PROMOTION:
        return (False,
                f"ecart insuffisant : {ecart:+.4f} < marge {MARGE_PROMOTION} "
                f"(champion {m_champ['auc']:.4f}, challenger {m_chall['auc']:.4f})",
                m_champ, m_chall)

    # L'AUC progresse, mais le bot n'agit qu'au-dessus du seuil de confiance :
    # une precision qui recule a cet endroit annulerait le gain.
    if m_chall["precision"] < m_champ["precision"] - 0.02:
        return (False,
                f"AUC en hausse ({ecart:+.4f}) mais precision au seuil en baisse "
                f"({m_champ['precision']:.4f} -> {m_chall['precision']:.4f}) : "
                f"c'est la ou le bot agit",
                m_champ, m_chall)

    return True, f"challenger meilleur de {ecart:+.4f} d'AUC", m_champ, m_chall


def promouvoir(challenger: xgb.Booster, mesures: dict, motif: str,
               mesures_champion: dict) -> None:
    """Remplace le champion, apres l'avoir archive."""
    modele, _, _ = _chemins()
    registre = lire_registre()

    champion = charger_champion()
    if champion is not None:
        archive = _archiver(champion, mesures_champion, "champion-sortant")
        logger.info(f"[registre] ancien champion archive : {archive}")

    modele.parent.mkdir(parents=True, exist_ok=True)
    # Ecriture puis remplacement : une coupure en pleine ecriture ne doit pas
    # laisser un fichier tronque que le bot chargerait au demarrage suivant.
    tmp = modele.with_suffix(".json.tmp")
    challenger.save_model(str(tmp))
    tmp.replace(modele)

    registre["champion"] = {
        "promu_le": datetime.now(timezone.utc).isoformat(),
        "mesures": mesures,
        "motif": motif,
        # L'univers d'entrainement fait partie de l'identite du modele : un
        # champion entraine sur des paires EUR n'a rien a dire de paires USDT.
        "paires": sorted(config.TRADE_PAIRS),
        "devise": config.QUOTE_CURRENCY,
    }
    registre.setdefault("historique", []).append({
        "date": datetime.now(timezone.utc).isoformat(),
        "decision": "promu",
        "motif": motif,
        "champion": mesures_champion,
        "challenger": mesures,
    })
    registre["historique"] = registre["historique"][-50:]
    ecrire_registre(registre)
    _elaguer_archives()
    logger.info(f"[registre] nouveau champion en place — {motif}")


def refuser(mesures_challenger: dict, motif: str, mesures_champion: dict) -> None:
    """Consigne un challenger ecarte : savoir ce qui a ete refuse, et pourquoi."""
    registre = lire_registre()
    registre.setdefault("historique", []).append({
        "date": datetime.now(timezone.utc).isoformat(),
        "decision": "refuse",
        "motif": motif,
        "champion": mesures_champion,
        "challenger": mesures_challenger,
    })
    registre["historique"] = registre["historique"][-50:]
    ecrire_registre(registre)
    logger.info(f"[registre] challenger ecarte — {motif}")


# ─────────────────────────────────────────────────────────────────────────────
# Univers de paires
# ─────────────────────────────────────────────────────────────────────────────

def paires_du_champion() -> list[str]:
    """Paires sur lesquelles le champion en place a ete entraine."""
    champion = (lire_registre().get("champion") or {})
    return list(champion.get("paires") or [])


def univers_a_change() -> tuple[bool, str]:
    """
    Compare les paires configurees a celles du champion.

    Un modele n'a rien d'utile a dire d'une paire qu'il n'a jamais vue : ses
    probabilites y sont une extrapolation, pas une mesure. Quand l'univers
    change, le champion doit ceder la place sans passer par la comparaison
    habituelle — comparer deux modeles sur des donnees que l'un n'a jamais
    vues ne mesure rien.
    """
    connues = set(paires_du_champion())
    voulues = set(config.TRADE_PAIRS)

    if not connues:
        return True, "le champion ne declare aucune paire d'entrainement"
    if connues == voulues:
        return False, ""

    ajoutees = sorted(voulues - connues)
    retirees = sorted(connues - voulues)
    details = []
    if ajoutees:
        details.append(f"jamais vues a l'entrainement : {', '.join(ajoutees)}")
    if retirees:
        details.append(f"retirees : {', '.join(retirees)}")
    return True, " ; ".join(details)


# ─────────────────────────────────────────────────────────────────────────────
# Rechargement a chaud
# ─────────────────────────────────────────────────────────────────────────────

def signature_modele() -> tuple[float, int]:
    """
    Empreinte du fichier de modele (date de modification, taille).

    Permet au bot de detecter qu'un reentrainement a promu un nouveau modele,
    et de le recharger sans redemarrer — donc sans interrompre la surveillance
    des positions ouvertes.
    """
    modele, _, _ = _chemins()
    try:
        st = modele.stat()
        return (st.st_mtime, st.st_size)
    except OSError:
        return (0.0, 0)
