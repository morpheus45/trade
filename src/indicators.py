"""
Bibliothèque complète d'indicateurs techniques.
Toutes les fonctions prennent un DataFrame pandas avec colonnes open, high, low, close, volume.

Indicateurs disponibles :
  Tendance  : EMA (fast/slow/trend), ADX, DI+, DI-
  Momentum  : RSI, Stoch RSI, MACD, ROC
  Volatilité: ATR, Bollinger Bands
  Volume    : Volume Ratio, OBV, OBV EMA
  Prix      : VWAP deviation, distance aux EMAs
"""
import pandas as pd
import numpy as np
import config


# ─────────────────────────────────────────────────────────────────────────────
# Indicateurs de base
# ─────────────────────────────────────────────────────────────────────────────

def calculate_rsi(df: pd.DataFrame, period: int = None) -> pd.Series:
    """RSI (Relative Strength Index) — momentum oscilateur."""
    period = period or config.RSI_PERIOD
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calculate_macd(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD, Signal, Histogramme."""
    ema_fast = df["close"].ewm(span=config.MACD_FAST, adjust=False).mean()
    ema_slow = df["close"].ewm(span=config.MACD_SLOW, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal = macd.ewm(span=config.MACD_SIGNAL, adjust=False).mean()
    hist = macd - signal
    return macd, signal, hist


def calculate_bollinger_bands(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands : upper, middle, lower."""
    middle = df["close"].rolling(window=config.BB_PERIOD).mean()
    std = df["close"].rolling(window=config.BB_PERIOD).std()
    upper = middle + config.BB_STD * std
    lower = middle - config.BB_STD * std
    return upper, middle, lower


def calculate_ema(df: pd.DataFrame, period: int) -> pd.Series:
    """EMA (Exponential Moving Average)."""
    return df["close"].ewm(span=period, adjust=False).mean()


def calculate_atr(df: pd.DataFrame, period: int = None) -> pd.Series:
    """ATR (Average True Range) — mesure la volatilité."""
    period = period or config.ATR_PERIOD
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def calculate_volume_ratio(df: pd.DataFrame) -> pd.Series:
    """Ratio volume courant / moyenne mobile du volume."""
    vol_ma = df["volume"].rolling(window=config.VOLUME_MA_PERIOD).mean()
    return df["volume"] / vol_ma.replace(0, np.nan)


def calculate_stoch_rsi(
    df: pd.DataFrame, period: int = 14, smooth_k: int = 3, smooth_d: int = 3
) -> tuple[pd.Series, pd.Series]:
    """Stochastic RSI (K et D)."""
    rsi = calculate_rsi(df, period)
    rsi_min = rsi.rolling(window=period).min()
    rsi_max = rsi.rolling(window=period).max()
    rsi_range = (rsi_max - rsi_min).replace(0, np.nan)
    stoch = (rsi - rsi_min) / rsi_range * 100
    k = stoch.rolling(window=smooth_k).mean()
    d = k.rolling(window=smooth_d).mean()
    return k, d


# ─────────────────────────────────────────────────────────────────────────────
# Nouveaux indicateurs haute valeur
# ─────────────────────────────────────────────────────────────────────────────

def calculate_adx(df: pd.DataFrame, period: int = None) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    ADX (Average Directional Index) — force de la tendance.
    Retourne : adx, di_plus, di_minus

    ADX > 25 : marché directionnel → signaux fiables
    ADX < 15 : marché sans direction → éviter d'entrer
    DI+ > DI- : tendance haussière
    DI- > DI+ : tendance baissière
    """
    period = period or config.ADX_PERIOD

    high = df["high"]
    low  = df["low"]
    close = df["close"]

    # True Range
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Directional movements
    dm_plus  = high.diff()
    dm_minus = -low.diff()

    dm_plus  = dm_plus.where((dm_plus > dm_minus) & (dm_plus > 0), 0)
    dm_minus = dm_minus.where((dm_minus > dm_plus) & (dm_minus > 0), 0)

    # Smoothed (Wilder)
    atr_s    = tr.ewm(alpha=1/period, adjust=False).mean()
    dmp_s    = dm_plus.ewm(alpha=1/period, adjust=False).mean()
    dmm_s    = dm_minus.ewm(alpha=1/period, adjust=False).mean()

    di_plus  = 100 * dmp_s / atr_s.replace(0, np.nan)
    di_minus = 100 * dmm_s / atr_s.replace(0, np.nan)

    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx = dx.ewm(alpha=1/period, adjust=False).mean()

    return adx, di_plus, di_minus


def calculate_roc(df: pd.DataFrame, period: int = None) -> pd.Series:
    """
    Rate of Change — momentum en % de variation sur N bougies.
    ROC > 0 et croissant → momentum haussier
    ROC < 0 et décroissant → momentum baissier
    """
    period = period or config.ROC_PERIOD
    return df["close"].pct_change(period) * 100


def calculate_obv(df: pd.DataFrame) -> pd.Series:
    """
    On Balance Volume — accumulation / distribution basée sur le volume.
    OBV croissant avec le prix = confirmation haussière (smart money suit)
    OBV diverge avec le prix = signal d'essoufflement
    """
    direction = np.sign(df["close"].diff())
    direction.iloc[0] = 0
    obv = (df["volume"] * direction).cumsum()
    return obv


def calculate_obv_signal(df: pd.DataFrame) -> pd.Series:
    """EMA de l'OBV pour filtrer le bruit."""
    obv = calculate_obv(df)
    return obv.ewm(span=config.OBV_EMA_PERIOD, adjust=False).mean()


def calculate_vwap_deviation(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Déviation du prix par rapport au VWAP glissant (rolling).
    > 0 → prix au-dessus du VWAP (haussier)
    < 0 → prix sous le VWAP (baissier)
    Normalisé par le prix pour comparaison cross-asset.
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    vwap = (typical_price * df["volume"]).rolling(period).sum() / df["volume"].rolling(period).sum()
    return (df["close"] - vwap) / df["close"]


def calculate_squeeze(df: pd.DataFrame) -> pd.Series:
    """
    Squeeze Momentum : détecte les périodes de compression avant une explosion.
    BB se contracte à l'intérieur des Keltner Channels → compression.
    Retourne 1 (compression), -1 (expansion), 0 (neutre).
    """
    # Bollinger Bands
    bb_mid = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    # Keltner Channels (ATR based)
    atr = calculate_atr(df, 20)
    kc_upper = bb_mid + 1.5 * atr
    kc_lower = bb_mid - 1.5 * atr

    squeeze = ((bb_lower >= kc_lower) & (bb_upper <= kc_upper)).astype(int)
    return squeeze


# ─────────────────────────────────────────────────────────────────────────────
# Fonction principale : calcul de tous les indicateurs
# ─────────────────────────────────────────────────────────────────────────────

def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule tous les indicateurs et les ajoute comme colonnes au DataFrame.
    Retourne un DataFrame enrichi.
    """
    df = df.copy()

    # ── Indicateurs de base ──────────────────────────────────────────────────
    df["rsi"] = calculate_rsi(df)
    df["macd"], df["macd_signal"], df["macd_hist"] = calculate_macd(df)

    df["bb_upper"], df["bb_middle"], df["bb_lower"] = calculate_bollinger_bands(df)
    df["bb_position"] = (
        (df["close"] - df["bb_lower"])
        / (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)
    )

    df["ema_fast"]  = calculate_ema(df, config.EMA_FAST)
    df["ema_slow"]  = calculate_ema(df, config.EMA_SLOW)
    df["ema_trend"] = calculate_ema(df, config.EMA_TREND)

    df["dist_ema_fast"]  = (df["close"] - df["ema_fast"])  / df["close"]
    df["dist_ema_slow"]  = (df["close"] - df["ema_slow"])  / df["close"]
    df["dist_ema_trend"] = (df["close"] - df["ema_trend"]) / df["close"]

    df["atr"]     = calculate_atr(df)
    df["atr_pct"] = df["atr"] / df["close"]

    df["volume_ratio"] = calculate_volume_ratio(df)
    df["stoch_k"], df["stoch_d"] = calculate_stoch_rsi(df)

    df["return_1"] = df["close"].pct_change(1)
    df["return_3"] = df["close"].pct_change(3)
    df["return_6"] = df["close"].pct_change(6)

    # ── Nouveaux indicateurs haute valeur ────────────────────────────────────
    df["adx"], df["di_plus"], df["di_minus"] = calculate_adx(df)
    df["roc"]           = calculate_roc(df)
    df["obv_signal"]    = calculate_obv_signal(df)
    df["obv_norm"]      = df["obv_signal"].pct_change(5)   # variation OBV sur 5 bougies
    df["vwap_dev"]      = calculate_vwap_deviation(df)
    df["squeeze"]       = calculate_squeeze(df)

    # ── Indicateurs dérivés ──────────────────────────────────────────────────
    # Force de la tendance normalisée (DI+ - DI- divisé par ADX)
    df["trend_strength"] = (df["di_plus"] - df["di_minus"]) / df["adx"].replace(0, np.nan)

    # Momentum composite : combine RSI + ROC + MACD hist normalisé
    rsi_norm  = (df["rsi"] - 50) / 50         # centré sur 0, borne [-1, 1]
    roc_norm  = df["roc"] / 10                 # normalisé ~[-1, 1] sur crypto
    macd_norm = df["macd_hist"] / df["atr"].replace(0, np.nan)
    df["momentum_composite"] = (rsi_norm + roc_norm + macd_norm) / 3

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Features pour le modèle ML (enrichies)
# ─────────────────────────────────────────────────────────────────────────────
ML_FEATURES = [
    # Momentum
    "rsi", "macd_hist", "stoch_k", "stoch_d", "roc", "momentum_composite",
    # Structure de prix
    "bb_position", "dist_ema_fast", "dist_ema_slow", "dist_ema_trend", "vwap_dev",
    # Tendance
    "adx", "trend_strength",
    # Volatilité
    "atr_pct",
    # Volume
    "volume_ratio", "obv_norm",
    # Rendements passés
    "return_1", "return_3", "return_6",
    # Squeeze
    "squeeze",
]
