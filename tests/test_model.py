"""
Registre de modeles : arbitrage champion / challenger, univers de paires.

    python tests/test_model.py

Aucun appel reseau : les donnees sont synthetiques et les modeles entraines
sur place, en quelques secondes.
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("DASHBOARD_PASSWORD", "motdepasse-de-test-1234")

_passed: list[str] = []
_failed: list[tuple[str, str]] = []


def test(name):
    def decorator(fn):
        try:
            fn()
            _passed.append(name)
            print(f"  [OK]   {name}")
        except Exception:
            _failed.append((name, traceback.format_exc()))
            print(f"  [ECHEC] {name}")
        return fn
    return decorator


import numpy as np           # noqa: E402
import xgboost as xgb        # noqa: E402

import config                # noqa: E402
import model_registry as reg # noqa: E402
from indicators import ML_FEATURES  # noqa: E402


def _donnees(n=1200, signal=1.0, graine=0):
    """
    Jeu synthetique : une cible correlee aux features avec une force reglable.
    `signal` eleve = modele facile a apprendre, donc AUC haute.
    """
    rng = np.random.default_rng(graine)
    X = rng.normal(size=(n, len(ML_FEATURES)))
    logit = signal * (X[:, 0] + 0.5 * X[:, 1] - 0.3 * X[:, 2])
    proba = 1 / (1 + np.exp(-logit))
    y = (rng.random(n) < proba).astype(int)
    return X.astype(np.float32), y


def _modele(X, y, rounds=60):
    dtrain = xgb.DMatrix(X, label=y, feature_names=ML_FEATURES)
    return xgb.train({"objective": "binary:logistic", "eval_metric": "auc",
                      "max_depth": 3, "eta": 0.2, "seed": 42},
                     dtrain, num_boost_round=rounds, verbose_eval=False)


class _Bac:
    """Redirige le registre vers un repertoire jetable."""

    def __enter__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ancien = config.MODEL_PATH
        config.MODEL_PATH = Path(self.dir.name) / "xgboost_model.json"
        return self

    def __exit__(self, *a):
        config.MODEL_PATH = self.ancien
        self.dir.cleanup()


# ─────────────────────────────────────────────────────────────────────────────
print("\n1. Decoupage et evaluation")
# ─────────────────────────────────────────────────────────────────────────────

@test("le holdout est chronologique, jamais melange")
def _():
    X = np.arange(1000, dtype=np.float32).reshape(-1, 1)
    X = np.repeat(X, len(ML_FEATURES), axis=1)
    y = (np.arange(1000) % 2).astype(int)
    X_tr, _, X_test, _ = reg.decouper_holdout(X, y, ratio=0.2)
    # Melanger placerait des bougies futures dans l'entrainement : le test
    # doit commencer exactement ou l'entrainement s'arrete.
    assert X_tr[-1][0] < X_test[0][0], (
        "le decoupage n'est pas chronologique — un modele evalue ainsi "
        "obtiendrait un score flatteur et faux"
    )
    assert len(X_test) == 200, len(X_test)


@test("un jeu trop petit est refuse plutot qu'evalue")
def _():
    X, y = _donnees(n=50)
    try:
        reg.decouper_holdout(X, y)
    except ValueError:
        return
    raise AssertionError("50 echantillons ont ete acceptes pour une evaluation")


@test("evaluer mesure AUC et precision au seuil de production")
def _():
    X, y = _donnees(signal=1.5)
    m = _modele(X[:900], y[:900])
    mesures = reg.evaluer(m, X[900:], y[900:])
    assert 0.0 <= mesures["auc"] <= 1.0
    assert mesures["auc"] > 0.6, f"AUC trop basse sur un signal net : {mesures['auc']}"
    assert mesures["echantillons"] == 300
    assert "taux_base" in mesures


@test("evaluer ne crashe pas sans modele")
def _():
    X, y = _donnees(n=200)
    mesures = reg.evaluer(None, X, y)
    assert mesures["auc"] == 0.0 and mesures["signaux"] == 0


# ─────────────────────────────────────────────────────────────────────────────
print("\n2. Arbitrage")
# ─────────────────────────────────────────────────────────────────────────────

@test("un challenger nettement meilleur est promu")
def _():
    X, y = _donnees(signal=1.5, graine=1)
    X_tr, y_tr, X_te, y_te = reg.decouper_holdout(X, y)
    faible = _modele(X_tr[:200], y_tr[:200], rounds=3)     # sous-entraine
    fort   = _modele(X_tr, y_tr, rounds=120)
    promouvoir, motif, mc, mch = reg.arbitrer(faible, fort, X_te, y_te)
    assert promouvoir, f"refuse a tort : {motif} ({mc['auc']} vs {mch['auc']})"


@test("un ecart insuffisant ne declenche pas de remplacement")
def _():
    X, y = _donnees(signal=1.2, graine=2)
    X_tr, y_tr, X_te, y_te = reg.decouper_holdout(X, y)
    m = _modele(X_tr, y_tr)
    # Le meme modele face a lui-meme : ecart nul.
    promouvoir, motif, _, _ = reg.arbitrer(m, m, X_te, y_te)
    assert not promouvoir, "un modele identique a ete promu"
    assert "ecart insuffisant" in motif, motif


def _avec_mesures(*mesures):
    """
    Force les mesures renvoyees par `evaluer`, dans l'ordre des appels.

    On teste ici la REGLE d'arbitrage, pas la capacite d'XGBoost a produire
    telle ou telle AUC : sur une petite fenetre, l'AUC d'un modele entraine
    sur du bruit varie de plusieurs centiemes d'une graine a l'autre, ce qui
    rendrait le test instable sans rien prouver de plus.
    """
    suite = list(mesures)

    def faux(booster, X_, y_):
        base = suite.pop(0) if suite else {}
        return {"auc": 0.0, "precision": 0.0, "signaux": 100,
                "echantillons": len(y_), "taux_base": 0.5, **base}

    return faux


@test("un challenger sous le plancher est ecarte, meme sans champion")
def _():
    X, y = _donnees(graine=3)
    X_tr, y_tr, X_te, y_te = reg.decouper_holdout(X, y)
    quelconque = _modele(X_tr[:300], y_tr[:300], rounds=5)

    vrai = reg.evaluer
    # Sans champion, un challenger sans pouvoir predictif ne doit pas etre
    # installe par defaut : mieux vaut aucun filtre qu'un filtre au hasard.
    reg.evaluer = _avec_mesures({"auc": 0.0}, {"auc": 0.51, "precision": 0.50})
    try:
        promouvoir, motif, _, _ = reg.arbitrer(None, quelconque, X_te, y_te)
    finally:
        reg.evaluer = vrai

    assert not promouvoir, (
        "un modele sans pouvoir predictif a ete installe faute de concurrent"
    )
    assert "plancher" in motif, motif


@test("sans champion, un challenger solide est bien installe")
def _():
    X, y = _donnees(graine=8)
    X_tr, y_tr, X_te, y_te = reg.decouper_holdout(X, y)
    bon = _modele(X_tr, y_tr)

    vrai = reg.evaluer
    reg.evaluer = _avec_mesures({"auc": 0.0}, {"auc": 0.68, "precision": 0.62})
    try:
        promouvoir, motif, _, _ = reg.arbitrer(None, bon, X_te, y_te)
    finally:
        reg.evaluer = vrai

    assert promouvoir, f"refuse a tort : {motif}"
    assert "aucun champion" in motif, motif


@test("une precision en baisse au seuil bloque la promotion")
def _():
    X, y = _donnees(signal=1.0, graine=4)
    X_tr, y_tr, X_te, y_te = reg.decouper_holdout(X, y)
    champion = _modele(X_tr, y_tr)

    # Modèles réels comparés via des mesures forcées : on teste la règle
    # d'arbitrage, pas la capacité à fabriquer un tel modèle.
    vrai_evaluer = reg.evaluer
    appels = {"n": 0}

    def faux_evaluer(booster, X_, y_):
        appels["n"] += 1
        # 1er appel = champion, 2e = challenger
        if appels["n"] == 1:
            return {"auc": 0.60, "precision": 0.55, "signaux": 100,
                    "echantillons": len(y_), "taux_base": 0.5}
        return {"auc": 0.65, "precision": 0.50, "signaux": 100,
                "echantillons": len(y_), "taux_base": 0.5}

    reg.evaluer = faux_evaluer
    try:
        promouvoir, motif, _, _ = reg.arbitrer(champion, champion, X_te, y_te)
    finally:
        reg.evaluer = vrai_evaluer

    assert not promouvoir, (
        "AUC en hausse mais precision en baisse au seuil : c'est pourtant la "
        "que le bot agit"
    )
    assert "precision" in motif, motif


# ─────────────────────────────────────────────────────────────────────────────
print("\n3. Univers de paires")
# ─────────────────────────────────────────────────────────────────────────────

@test("sans champion, l'univers est considere comme a reentrainer")
def _():
    with _Bac():
        change, details = reg.univers_a_change()
        assert change and "aucune paire" in details, details


@test("un univers identique ne declenche rien")
def _():
    with _Bac():
        reg.ecrire_registre({"champion": {"paires": sorted(config.TRADE_PAIRS)},
                             "historique": []})
        change, _ = reg.univers_a_change()
        assert not change, "reentrainement demande alors que rien n'a change"


@test("une paire ajoutee est detectee")
def _():
    with _Bac():
        partielles = sorted(config.TRADE_PAIRS)[:-2]
        reg.ecrire_registre({"champion": {"paires": partielles}, "historique": []})
        change, details = reg.univers_a_change()
        assert change, "l'ajout de paires n'a pas ete detecte"
        assert "jamais vues" in details, details


@test("un changement de devise est detecte")
def _():
    with _Bac():
        reg.ecrire_registre({"champion": {"paires": ["BTC/USDT", "ETH/USDT"]},
                             "historique": []})
        change, details = reg.univers_a_change()
        assert change, "le passage USDT -> EUR n'a pas ete detecte"
        assert "retirees" in details, details


@test("config refuse un melange de devises de cotation")
def _():
    import importlib
    ancien = os.environ.get("TRADE_PAIRS")
    os.environ["TRADE_PAIRS"] = "BTC/EUR,ETH/USDT"
    try:
        importlib.reload(config)
    except SystemExit as exc:
        assert "melange" in str(exc).lower() or "devise" in str(exc).lower(), str(exc)
    else:
        raise AssertionError(
            "EUR et USDT ont ete acceptes ensemble — capital et frais "
            "n'auraient plus la meme unite"
        )
    finally:
        if ancien is None:
            os.environ.pop("TRADE_PAIRS", None)
        else:
            os.environ["TRADE_PAIRS"] = ancien
        importlib.reload(config)


@test("config accepte une liste de paires personnalisee")
def _():
    import importlib
    ancien = os.environ.get("TRADE_PAIRS")
    os.environ["TRADE_PAIRS"] = "BTC/USDT, ETH/USDT ,SOL/USDT"
    try:
        importlib.reload(config)
        assert config.TRADE_PAIRS == ["BTC/USDT", "ETH/USDT", "SOL/USDT"], config.TRADE_PAIRS
        assert config.QUOTE_CURRENCY == "USDT", config.QUOTE_CURRENCY
    finally:
        if ancien is None:
            os.environ.pop("TRADE_PAIRS", None)
        else:
            os.environ["TRADE_PAIRS"] = ancien
        importlib.reload(config)


@test("l'entrainement utilise les paires reellement tradees")
def _():
    # Avant, la liste etait figee a 5 paires alors que le bot en tradait 8.
    import importlib
    import train_xgboost
    importlib.reload(train_xgboost)
    assert train_xgboost.PAIRS == list(config.TRADE_PAIRS), (
        f"entrainement sur {train_xgboost.PAIRS}, "
        f"trading sur {list(config.TRADE_PAIRS)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
print("\n4. Promotion et archives")
# ─────────────────────────────────────────────────────────────────────────────

@test("une promotion ecrit le modele, l'archive et le registre")
def _():
    with _Bac():
        X, y = _donnees(signal=1.4, graine=5)
        ancien = _modele(X[:500], y[:500], rounds=5)
        config.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        ancien.save_model(str(config.MODEL_PATH))
        reg.ecrire_registre({"champion": {"paires": sorted(config.TRADE_PAIRS)},
                             "historique": []})

        nouveau = _modele(X, y, rounds=100)
        reg.promouvoir(nouveau, {"auc": 0.72, "precision": 0.61},
                       "test", {"auc": 0.60, "precision": 0.55})

        assert config.MODEL_PATH.exists(), "le modele n'a pas ete ecrit"
        archives = list((config.MODEL_PATH.parent / "archive").glob("*.json"))
        assert archives, "l'ancien champion n'a pas ete archive"

        donnees = reg.lire_registre()
        champ = donnees["champion"]
        assert champ["mesures"]["auc"] == 0.72
        assert champ["paires"] == sorted(config.TRADE_PAIRS), (
            "les paires d'entrainement ne sont pas memorisees"
        )
        assert champ["devise"] == config.QUOTE_CURRENCY
        assert donnees["historique"][-1]["decision"] == "promu"


@test("un refus est consigne sans toucher au modele en place")
def _():
    with _Bac():
        X, y = _donnees(graine=6)
        champion = _modele(X, y)
        config.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        champion.save_model(str(config.MODEL_PATH))
        empreinte = config.MODEL_PATH.read_bytes()

        reg.refuser({"auc": 0.51}, "trop faible", {"auc": 0.60})

        assert config.MODEL_PATH.read_bytes() == empreinte, (
            "le modele en place a ete modifie par un REFUS"
        )
        assert reg.lire_registre()["historique"][-1]["decision"] == "refuse"


@test("un registre corrompu ne fait pas echouer la lecture")
def _():
    with _Bac():
        config.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        (config.MODEL_PATH.parent / "registry.json").write_text(
            "{ pas du JSON", encoding="utf-8")
        donnees = reg.lire_registre()
        assert donnees["champion"] is None and donnees["historique"] == []


@test("la signature du modele change apres une promotion")
def _():
    with _Bac():
        X, y = _donnees(graine=7)
        config.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        _modele(X[:400], y[:400], rounds=5).save_model(str(config.MODEL_PATH))
        avant = reg.signature_modele()

        import time as _t
        _t.sleep(1.1)   # granularite du mtime
        reg.promouvoir(_modele(X, y, rounds=90), {"auc": 0.7, "precision": 0.6},
                       "test", {"auc": 0.6, "precision": 0.55})
        apres = reg.signature_modele()

        assert apres != avant, (
            "la signature n'a pas bouge — le bot ne rechargerait jamais le "
            "nouveau modele a chaud"
        )


# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 62)
total = len(_passed) + len(_failed)
if _failed:
    print(f"  {len(_passed)}/{total} tests passes — {len(_failed)} ECHEC(S)")
    print("=" * 62)
    for nom, tb in _failed:
        print(f"\n--- {nom} ---\n{tb}")
    sys.exit(1)

print(f"  {total}/{total} tests passes")
print("=" * 62)
sys.exit(0)
