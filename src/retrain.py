"""
Reentrainement periodique du modele.

    python src/retrain.py              # entraine, compare, promeut si meilleur
    python src/retrain.py --forcer     # promeut meme sans marge (apres un
                                       # changement de paires, par exemple)
    python src/retrain.py --etat       # ce que dit le registre, sans rien faire

Le modele vieillit : il a ete entraine sur une fenetre d'historique qui
s'eloigne, et le marche change de regime. Le reentrainer regulierement le
garde a jour.

Mais reentrainer n'est pas ameliorer. Un nouveau modele n'est pas
automatiquement meilleur, et l'ecraser en place reviendrait a changer le
comportement d'un systeme qui engage de l'argent sans rien verifier. Le
nouveau modele doit donc BATTRE celui en place sur une fenetre recente
qu'aucun des deux n'a vue — voir model_registry pour le detail du raisonnement,
et notamment pourquoi on n'entraine pas sur les trades du bot.

Une exception : quand les paires tradees changent, le champion a ete entraine
sur un autre univers. Le comparer au challenger sur des donnees qu'il n'a
jamais vues ne mesurerait rien ; il cede la place sans discussion.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config                     # noqa: E402
import model_registry as registre # noqa: E402
from logger import setup_logging  # noqa: E402

setup_logging()
logger = logging.getLogger("retrain")


def afficher_etat() -> int:
    donnees = registre.lire_registre()
    champion = donnees.get("champion")

    print()
    print("=" * 66)
    print("  REGISTRE DU MODELE")
    print("=" * 66)
    print(f"  Paires configurees : {', '.join(config.TRADE_PAIRS)}")
    print(f"  Devise             : {config.QUOTE_CURRENCY}")
    print()

    if not champion:
        print("  Aucun champion enregistre.")
        print("  Le modele livre avec le depot est en place mais n'a pas ete")
        print("  evalue par ce registre. Lance « python src/retrain.py » pour")
        print("  entrainer un challenger et le comparer.")
    else:
        mesures = champion.get("mesures", {})
        print(f"  Champion promu le  : {champion.get('promu_le', '?')[:19]}")
        print(f"  Motif              : {champion.get('motif', '?')}")
        print(f"  AUC                : {mesures.get('auc', '?')}")
        print(f"  Precision au seuil : {mesures.get('precision', '?')}")
        print(f"  Entraine sur       : {', '.join(champion.get('paires') or ['?'])}")

    change, details = registre.univers_a_change()
    if change:
        print()
        print(f"  /!\\ L'univers a change : {details}")
        print("      Un reentrainement est necessaire — le modele en place n'a")
        print("      rien d'utile a dire des paires qu'il n'a jamais vues.")

    historique = donnees.get("historique") or []
    if historique:
        print()
        print("  Dernieres decisions :")
        for ligne in historique[-5:]:
            marque = "promu " if ligne["decision"] == "promu" else "refuse"
            print(f"    {ligne['date'][:16]}  {marque}  {ligne['motif'][:60]}")
    print("=" * 66)
    return 0


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--forcer", action="store_true",
                         help="promouvoir meme sans marge suffisante")
    parseur.add_argument("--etat", action="store_true",
                         help="afficher le registre sans rien entrainer")
    args = parseur.parse_args()

    if args.etat:
        return afficher_etat()

    # Import tardif : ces modules telechargent et calculent, inutile de les
    # charger pour un simple --etat.
    from train_xgboost import PAIRS, build_params, prepare_dataset
    import xgboost as xgb
    from indicators import ML_FEATURES

    logger.info("=" * 60)
    logger.info("  REENTRAINEMENT")
    logger.info(f"  Paires : {', '.join(PAIRS)}")
    logger.info("=" * 60)

    univers_change, details = registre.univers_a_change()
    if univers_change:
        logger.warning(f"[univers] {details}")
        logger.warning("[univers] le champion sera remplace sans comparaison : "
                       "il a ete entraine sur un autre ensemble de paires")

    # ── Donnees ──────────────────────────────────────────────────────────────
    try:
        X, y = prepare_dataset()
    except Exception as exc:
        logger.error(f"Donnees indisponibles : {exc}")
        return 1

    try:
        X_tr, y_tr, X_test, y_test = registre.decouper_holdout(X, y)
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    logger.info(f"{len(X_tr)} echantillons d'entrainement, "
                f"{len(X_test)} en fenetre recente (jamais vue)")

    # ── Challenger ───────────────────────────────────────────────────────────
    logger.info("Entrainement du challenger...")
    params = build_params(y_tr)
    dtrain = xgb.DMatrix(X_tr, label=y_tr, feature_names=ML_FEATURES)
    challenger = xgb.train(params, dtrain, num_boost_round=500, verbose_eval=False)

    # ── Arbitrage ────────────────────────────────────────────────────────────
    champion = registre.charger_champion()
    promouvoir, motif, m_champ, m_chall = registre.arbitrer(
        champion, challenger, X_test, y_test)

    logger.info(f"Champion   : AUC={m_champ['auc']:.4f} "
                f"precision={m_champ['precision']:.4f} "
                f"({m_champ['signaux']} signaux)")
    logger.info(f"Challenger : AUC={m_chall['auc']:.4f} "
                f"precision={m_chall['precision']:.4f} "
                f"({m_chall['signaux']} signaux)")
    logger.info(f"Taux de base de la fenetre : {m_chall.get('taux_base', 0):.4f}")

    if univers_change:
        promouvoir, motif = True, f"changement d'univers ({details})"
    elif args.forcer and not promouvoir:
        promouvoir, motif = True, f"promotion forcee (arbitrage : {motif})"

    if promouvoir:
        registre.promouvoir(challenger, m_chall, motif, m_champ)
        logger.info("Nouveau modele en place. Le bot le rechargera sans redemarrer.")
    else:
        registre.refuser(m_chall, motif, m_champ)
        logger.info("Champion conserve. Aucun changement de comportement.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
