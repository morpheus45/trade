"""
Modèle XGBoost pour la confirmation des signaux.
Entraîné sur des features techniques réelles (voir train_xgboost.py).
Utilisé comme filtre final : ne valide un signal que si le modèle est d'accord.
"""
import logging
import numpy as np
import xgboost as xgb
import config
from indicators import ML_FEATURES

logger = logging.getLogger(__name__)

# Seuil de confiance minimum pour valider un signal
CONFIDENCE_THRESHOLD = 0.60


class AIModel:
    def __init__(self):
        self.model: xgb.Booster | None = None
        self._load()

    def _load(self) -> None:
        """Charge le modèle depuis le fichier .json."""
        model_path = config.MODEL_PATH
        if not model_path.exists():
            logger.warning(
                f"Modèle ML introuvable ({model_path}). "
                "Lance train_xgboost.py pour entraîner le modèle. "
                "Les signaux seront validés sans filtre ML."
            )
            return
        try:
            self.model = xgb.Booster()
            self.model.load_model(str(model_path))
            logger.info(f"Modèle XGBoost chargé depuis {model_path}")
        except Exception as e:
            logger.error(f"Erreur chargement modèle: {e}")
            self.model = None

    def predict(self, features: list[float]) -> tuple[str, float]:
        """
        Prédit la direction à partir des features techniques.

        Args:
            features: Liste de floats dans l'ordre de ML_FEATURES

        Returns:
            (signal, confidence) : signal = 'BUY'|'SELL'|'HOLD', confidence ∈ [0,1]
        """
        if self.model is None:
            # Pas de modèle → on laisse passer le signal technique sans filtre
            return "BYPASS", 1.0

        try:
            arr = np.array(features, dtype=np.float32).reshape(1, -1)
            dmatrix = xgb.DMatrix(arr, feature_names=ML_FEATURES)
            prob_buy = float(self.model.predict(dmatrix)[0])

            if prob_buy >= CONFIDENCE_THRESHOLD:
                return "BUY", prob_buy
            elif prob_buy <= (1 - CONFIDENCE_THRESHOLD):
                return "SELL", 1 - prob_buy
            else:
                return "HOLD", max(prob_buy, 1 - prob_buy)

        except Exception as e:
            logger.error(f"Erreur prédiction ML: {e}")
            return "BYPASS", 1.0

    def validate_signal(self, technical_signal: str, features: list[float]) -> bool:
        """
        Retourne True si le modèle ML confirme le signal technique.
        Si le modèle n'est pas chargé (BYPASS), retourne True.
        """
        ml_signal, confidence = self.predict(features)

        if ml_signal == "BYPASS":
            return True

        if ml_signal == "HOLD":
            logger.debug(f"ML indécis (conf={confidence:.2f}), signal technique ignoré")
            return False

        agrees = ml_signal == technical_signal
        logger.debug(
            f"ML signal={ml_signal} conf={confidence:.2f} | "
            f"technique={technical_signal} | accord={agrees}"
        )
        return agrees
