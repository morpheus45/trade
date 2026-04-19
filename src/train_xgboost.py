"""
Entraînement du modèle XGBoost — version améliorée.

Améliorations :
 - 19 features (ADX, ROC, OBV, VWAP, squeeze, momentum_composite)
 - Hyperparamètres optimisés (eta plus faible, plus d'estimateurs)
 - Label amélioré : hausse >1% OU passage du TP théorique dans N bougies
 - Validation croisée Walk-Forward (plus réaliste en trading)
 - Feature importance affichée après entraînement

Usage :
    python train_xgboost.py
"""
import sys
import logging
import time
import numpy as np
import pandas as pd
import xgboost as xgb
import ccxt
from pathlib import Path
from sklearn.metrics import roc_auc_score, accuracy_score, precision_score, recall_score

sys.path.insert(0, str(Path(__file__).parent))
import config
from indicators import add_all_indicators, ML_FEATURES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PAIRS         = ["BTC/EUR", "ETH/EUR", "BNB/EUR", "SOL/EUR", "XRP/EUR"]
TIMEFRAME     = "1h"
LOOKAHEAD     = 4       # Bougies en avant pour le label
TARGET_GAIN   = 0.01    # Gain minimum : +1%
MIN_AUC_SCORE = 0.55    # Seuil de qualité minimum
N_FOLDS       = 5       # Walk-forward folds


def fetch_historical(pair: str, timeframe: str = "1h", months: int = 24) -> pd.DataFrame:
    """Télécharge les données historiques depuis Binance (sans clé API)."""
    exchange = ccxt.binance({"enableRateLimit": True})
    all_candles = []
    limit = 500

    since_ms = exchange.parse8601(
        str(pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=months))
    )

    logger.info(f"Téléchargement {pair} {timeframe} ({months} mois)...")
    while True:
        try:
            candles = exchange.fetch_ohlcv(pair, timeframe, since=since_ms, limit=limit)
        except Exception as e:
            logger.error(f"Erreur fetch {pair}: {e}")
            time.sleep(5)
            continue

        if not candles:
            break

        all_candles.extend(candles)
        last_ts = candles[-1][0]

        if len(candles) < limit:
            break

        since_ms = last_ts + 1
        time.sleep(0.3)

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df.drop_duplicates(inplace=True)
    df.sort_index(inplace=True)
    logger.info(f"  → {len(df)} bougies pour {pair}")
    return df


def create_labels(df: pd.DataFrame, lookahead: int = LOOKAHEAD, gain: float = TARGET_GAIN) -> pd.Series:
    """
    Label = 1 si le prix monte d'au moins `gain` dans les prochaines `lookahead` bougies.
    Utilise le max futur pour être plus généreux (simule le TP).
    """
    future_max = df["close"].rolling(window=lookahead).max().shift(-lookahead)
    labels = (future_max / df["close"] - 1 >= gain).astype(int)
    return labels


def prepare_dataset() -> tuple[np.ndarray, np.ndarray]:
    all_X, all_y = [], []

    for pair in PAIRS:
        df = fetch_historical(pair)
        if df.empty or len(df) < 300:
            logger.warning(f"Données insuffisantes pour {pair}, skip")
            continue

        df = add_all_indicators(df)
        labels = create_labels(df)

        valid = df[ML_FEATURES].copy()
        valid["label"] = labels
        valid.dropna(inplace=True)

        X = valid[ML_FEATURES].values
        y = valid["label"].values

        all_X.append(X)
        all_y.append(y)
        logger.info(f"  {pair}: {len(X)} samples | {y.mean()*100:.1f}% positifs")

    if not all_X:
        raise RuntimeError("Aucune donnée disponible")

    return np.vstack(all_X), np.concatenate(all_y)


def walk_forward_cv(X: np.ndarray, y: np.ndarray, params: dict, n_folds: int) -> list[dict]:
    """
    Walk-Forward Cross-Validation : entraîne sur le passé, valide sur le futur.
    Plus réaliste qu'un CV classique car respecte l'ordre temporel.
    """
    fold_size = len(X) // (n_folds + 1)
    results   = []

    for fold in range(1, n_folds + 1):
        train_end = fold * fold_size
        val_start = train_end
        val_end   = val_start + fold_size

        X_tr, y_tr = X[:train_end],       y[:train_end]
        X_val, y_val = X[val_start:val_end], y[val_start:val_end]

        dtrain = xgb.DMatrix(X_tr, label=y_tr, feature_names=ML_FEATURES)
        dval   = xgb.DMatrix(X_val, feature_names=ML_FEATURES)

        model = xgb.train(
            params, dtrain,
            num_boost_round=500,
            verbose_eval=False,
        )

        preds = model.predict(dval)
        auc   = roc_auc_score(y_val, preds) if len(np.unique(y_val)) > 1 else 0.5
        acc   = accuracy_score(y_val, (preds > 0.5).astype(int))
        prec  = precision_score(y_val, (preds > 0.5).astype(int), zero_division=0)
        rec   = recall_score(y_val, (preds > 0.5).astype(int), zero_division=0)

        results.append({"fold": fold, "auc": auc, "acc": acc, "prec": prec, "rec": rec})
        logger.info(
            f"  Fold {fold}: AUC={auc:.4f} Acc={acc:.4f} "
            f"Prec={prec:.4f} Recall={rec:.4f}"
        )

    return results


def train_model(X: np.ndarray, y: np.ndarray) -> tuple:
    """
    Entraîne XGBoost avec Walk-Forward CV.
    Retourne (modèle final, mean_auc).
    """
    # Paramètres optimisés pour données crypto (non-stationnaires, bruit élevé)
    params = {
        "objective":         "binary:logistic",
        "eval_metric":       "auc",
        "eta":               0.03,          # Learning rate plus faible → moins d'overfitting
        "max_depth":         4,             # Arbres peu profonds → généralisation
        "min_child_weight":  10,            # Régularisation sur les feuilles
        "subsample":         0.7,
        "colsample_bytree":  0.7,
        "colsample_bylevel": 0.7,
        "reg_alpha":         0.1,           # Régularisation L1
        "reg_lambda":        1.0,           # Régularisation L2
        "scale_pos_weight":  (y == 0).sum() / max((y == 1).sum(), 1),
        "seed":              42,
    }

    logger.info(f"Walk-Forward CV {N_FOLDS} folds sur {len(X)} samples...")
    results  = walk_forward_cv(X, y, params, N_FOLDS)
    mean_auc = np.mean([r["auc"] for r in results])
    mean_acc = np.mean([r["acc"] for r in results])
    mean_prec = np.mean([r["prec"] for r in results])

    logger.info(
        f"Moyenne WF-CV: AUC={mean_auc:.4f} | "
        f"Acc={mean_acc:.4f} | Prec={mean_prec:.4f}"
    )

    if mean_auc < MIN_AUC_SCORE:
        logger.warning(
            f"AUC={mean_auc:.4f} < seuil {MIN_AUC_SCORE}. "
            "Modèle faible — vérifier les données. Sauvegarde quand même."
        )

    # Entraînement final sur tout le dataset
    logger.info("Entraînement final...")
    dtrain = xgb.DMatrix(X, label=y, feature_names=ML_FEATURES)
    final  = xgb.train(params, dtrain, num_boost_round=500, verbose_eval=False)

    # Affichage feature importance
    importance = final.get_score(importance_type="gain")
    sorted_imp = sorted(importance.items(), key=lambda x: x[1], reverse=True)
    logger.info("Top 10 features par gain:")
    for feat, score in sorted_imp[:10]:
        logger.info(f"  {feat:30s} : {score:.2f}")

    return final, mean_auc


def main():
    config.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

    logger.info("=== Entraînement XGBoost v2 ===")
    logger.info(f"Paires: {PAIRS} | Features: {len(ML_FEATURES)}")

    X, y = prepare_dataset()
    logger.info(f"Dataset: {len(X)} samples | {y.mean()*100:.1f}% positifs")

    model, auc = train_model(X, y)
    model.save_model(str(config.MODEL_PATH))
    logger.info(f"✅ Modèle sauvegardé → {config.MODEL_PATH} (AUC={auc:.4f})")


if __name__ == "__main__":
    main()
