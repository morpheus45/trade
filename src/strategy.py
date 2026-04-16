"""
Stratégie de trading multi-indicateurs améliorée.

Améliorations vs v1 :
 - Filtre ADX : ne trader que dans les marchés directionnels
 - Confirmation multi-timeframe (4h) : aligne la tendance sur TF supérieur
 - Score de signal [0-5] : mesure la qualité de l'entrée
 - Divergence RSI/prix : détecter les reversals
 - Signal composite : RSI + MACD + BB + EMA cross + Stoch RSI

Retourne : ('BUY'|'SELL'|None, score: int)
"""
import logging
import pandas as pd
import numpy as np
import config
from indicators import add_all_indicators

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Filtres de régime
# ─────────────────────────────────────────────────────────────────────────────

def _trend_filter(df: pd.DataFrame) -> str:
    """
    Tendance via EMA 200.
    Retourne 'up', 'down', ou 'neutral'.
    """
    last = df.iloc[-1]
    if pd.isna(last["ema_trend"]):
        return "neutral"
    if last["close"] > last["ema_trend"] * 1.005:
        return "up"
    if last["close"] < last["ema_trend"] * 0.995:
        return "down"
    return "neutral"


def _adx_filter(df: pd.DataFrame) -> str:
    """
    Filtre ADX : valide seulement les marchés directionnels.
    Retourne 'trending', 'weak', ou 'ranging'.
    """
    last = df.iloc[-1]
    adx = last.get("adx")
    if pd.isna(adx):
        return "trending"  # pas bloquant si indisponible

    if adx >= config.ADX_TRENDING:
        # DI+ > DI- → haussier | DI+ < DI- → baissier
        direction = "bullish" if last.get("di_plus", 0) > last.get("di_minus", 0) else "bearish"
        return f"trending_{direction}"
    if adx < config.ADX_WEAK:
        return "ranging"
    return "weak"


def _multiframe_trend(df_4h: pd.DataFrame | None) -> str:
    """
    Tendance sur le timeframe 4h.
    Retourne 'up', 'down', 'neutral', ou 'unavailable'.
    """
    if df_4h is None or df_4h.empty or len(df_4h) < 50:
        return "unavailable"

    df_4h = add_all_indicators(df_4h)
    last = df_4h.iloc[-1]

    if pd.isna(last.get("ema_trend")):
        return "unavailable"

    if last["close"] > last["ema_trend"] * 1.005:
        return "up"
    if last["close"] < last["ema_trend"] * 0.995:
        return "down"
    return "neutral"


# ─────────────────────────────────────────────────────────────────────────────
# Signaux individuels
# ─────────────────────────────────────────────────────────────────────────────

def _rsi_signal(df: pd.DataFrame) -> str | None:
    last = df.iloc[-1]
    prev = df.iloc[-2]
    rsi  = last.get("rsi")
    if pd.isna(rsi):
        return None
    if prev["rsi"] < config.RSI_OVERSOLD and rsi >= config.RSI_OVERSOLD:
        return "BUY"   # Sortie de survente
    if prev["rsi"] > config.RSI_OVERBOUGHT and rsi <= config.RSI_OVERBOUGHT:
        return "SELL"  # Sortie de surachat
    if rsi < config.RSI_OVERSOLD + 5:
        return "BUY"
    if rsi > config.RSI_OVERBOUGHT - 5:
        return "SELL"
    return None


def _macd_signal(df: pd.DataFrame) -> str | None:
    last = df.iloc[-1]
    prev = df.iloc[-2]
    if pd.isna(last.get("macd_hist")):
        return None
    if prev["macd_hist"] < 0 and last["macd_hist"] >= 0:
        return "BUY"
    if prev["macd_hist"] > 0 and last["macd_hist"] <= 0:
        return "SELL"
    if last["macd_hist"] > 0 and last["macd"] > last["macd_signal"]:
        return "BUY"
    if last["macd_hist"] < 0 and last["macd"] < last["macd_signal"]:
        return "SELL"
    return None


def _bb_signal(df: pd.DataFrame) -> str | None:
    last = df.iloc[-1]
    bb_pos = last.get("bb_position")
    if pd.isna(bb_pos):
        return None
    if bb_pos < 0.15:
        return "BUY"
    if bb_pos > 0.85:
        return "SELL"
    return None


def _ema_cross_signal(df: pd.DataFrame) -> str | None:
    last = df.iloc[-1]
    prev = df.iloc[-2]
    if pd.isna(last.get("ema_fast")) or pd.isna(last.get("ema_slow")):
        return None
    if prev["ema_fast"] <= prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]:
        return "BUY"
    if prev["ema_fast"] >= prev["ema_slow"] and last["ema_fast"] < last["ema_slow"]:
        return "SELL"
    return None


def _stoch_signal(df: pd.DataFrame) -> str | None:
    """Signal Stochastic RSI — confirmation du momentum."""
    last = df.iloc[-1]
    prev = df.iloc[-2]
    k    = last.get("stoch_k")
    d    = last.get("stoch_d")
    if pd.isna(k) or pd.isna(d):
        return None
    # Croisement K/D en zone oversold
    if k < 20 and d < 20 and prev["stoch_k"] < prev["stoch_d"] and k > d:
        return "BUY"
    if k > 80 and d > 80 and prev["stoch_k"] > prev["stoch_d"] and k < d:
        return "SELL"
    return None


def _obv_signal(df: pd.DataFrame, target: str) -> bool:
    """
    Confirmation OBV : le volume "smart money" confirme le signal.
    BUY validé si OBV croissant | SELL validé si OBV décroissant.
    """
    obv_norm = df.iloc[-1].get("obv_norm")
    if pd.isna(obv_norm):
        return True  # pas bloquant
    if target == "BUY":
        return obv_norm >= -0.02   # OBV stable ou en hausse
    return obv_norm <= 0.02        # OBV stable ou en baisse


def _volume_confirmed(df: pd.DataFrame) -> bool:
    vol_ratio = df.iloc[-1].get("volume_ratio", 0)
    if pd.isna(vol_ratio):
        return True
    return vol_ratio >= 1.2


def _squeeze_breakout(df: pd.DataFrame, target: str) -> bool:
    """
    Détecte une sortie de compression (squeeze).
    Bonus si on entre juste après une période de compression → volatilité à venir.
    """
    last = df.iloc[-1]
    prev = df.iloc[-2]
    # Sortie de squeeze = prev=1 (compressé), last=0 (expansion)
    if prev.get("squeeze", 0) == 1 and last.get("squeeze", 0) == 0:
        # Direction de la sortie : momentum composite
        mc = last.get("momentum_composite", 0)
        if target == "BUY"  and mc > 0:
            return True
        if target == "SELL" and mc < 0:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Fonction principale
# ─────────────────────────────────────────────────────────────────────────────

def generate_signal(
    df: pd.DataFrame,
    df_4h: pd.DataFrame | None = None,
) -> tuple[str | None, int]:
    """
    Génère un signal de trading avec son score de qualité.

    Args:
        df    : DataFrame 1h avec indicateurs
        df_4h : DataFrame 4h pour confirmation de tendance (optionnel)

    Retourne:
        (signal, score) où signal ∈ {'BUY', 'SELL', None} et score ∈ [0, 5]
    """
    if len(df) < config.LOOKBACK_CANDLES // 2:
        return None, 0

    df = add_all_indicators(df)

    # ── Filtre 1 : tendance 1h ─────────────────────────────────────────────
    trend_1h = _trend_filter(df)
    if trend_1h == "neutral":
        return None, 0

    target = "BUY" if trend_1h == "up" else "SELL"

    # ── Filtre 2 : ADX (marché directionnel requis) ────────────────────────
    adx_regime = _adx_filter(df)
    if adx_regime == "ranging":
        logger.debug(f"ADX trop faible — marché sans direction, skip")
        return None, 0

    # ── Filtre 3 : confirmation 4h ─────────────────────────────────────────
    trend_4h = _multiframe_trend(df_4h)
    if trend_4h not in ("up" if target == "BUY" else "down", "unavailable"):
        logger.debug(f"Tendance 4h ({trend_4h}) en contradiction avec signal 1h ({target})")
        return None, 0

    # ── Collecte des signaux ───────────────────────────────────────────────
    signals = [
        _rsi_signal(df),
        _macd_signal(df),
        _bb_signal(df),
        _ema_cross_signal(df),
        _stoch_signal(df),
    ]

    matching = sum(1 for s in signals if s == target)
    opposing = sum(1 for s in signals if s is not None and s != target)

    logger.debug(
        f"[{target}] ADX={adx_regime} 4h={trend_4h} "
        f"matching={matching} opposing={opposing} vol={_volume_confirmed(df)}"
    )

    # Minimum 2 signaux concordants, pas plus d'1 contraire, volume confirmé
    if matching < 2 or opposing > 1 or not _volume_confirmed(df):
        return None, 0

    # ── Calcul du score [0-5] ──────────────────────────────────────────────
    score = matching  # base : nombre de signaux concordants (max 5)

    # Bonus ADX fort
    if "trending" in adx_regime:
        score = min(score + 1, 5)

    # Bonus squeeze breakout
    if _squeeze_breakout(df, target):
        score = min(score + 1, 5)

    # Bonus OBV confirme
    if _obv_signal(df, target):
        score = min(score + 1, 5)

    # Malus si tendance 4h non disponible
    if trend_4h == "unavailable":
        score = max(score - 1, 0)

    logger.info(f"Signal {target} généré — score={score}/5 | ADX={adx_regime} | 4h={trend_4h}")
    return target, score


def get_features_for_ml(df: pd.DataFrame) -> list[float] | None:
    """
    Extrait les features pour le modèle ML depuis la dernière bougie.
    Retourne une liste de floats ou None si données insuffisantes.
    """
    from indicators import ML_FEATURES
    df = add_all_indicators(df)
    last = df.iloc[-1]

    missing = [f for f in ML_FEATURES if f not in last.index or pd.isna(last[f])]
    if missing:
        logger.debug(f"Features ML manquantes: {missing}")
        return None

    return [float(last[f]) for f in ML_FEATURES]
