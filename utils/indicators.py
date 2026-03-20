"""
indicators.py – Calcolo indicatori tecnici (RSI, MACD, EMA).
Funzioni pure che operano su pandas DataFrame di candele OHLCV.
"""

import pandas as pd
import logging

logger = logging.getLogger(__name__)


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Calcola la Exponential Moving Average su una Series di prezzi.

    Args:
        series: Serie di prezzi (tipicamente 'close').
        period: Numero di periodi per il calcolo EMA.

    Returns:
        pd.Series con i valori EMA.
    """
    return series.ewm(span=period, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calcola il Relative Strength Index (RSI).

    Usa il metodo di Wilder (EMA dei gain/loss).

    Args:
        series: Serie di prezzi di chiusura.
        period: Periodo RSI (default 14).

    Returns:
        pd.Series con i valori RSI (0-100).
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def calculate_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """Calcola il MACD (Moving Average Convergence Divergence).

    Args:
        series: Serie di prezzi di chiusura.
        fast: Periodo EMA veloce (default 12).
        slow: Periodo EMA lenta (default 26).
        signal: Periodo della signal line (default 9).

    Returns:
        DataFrame con colonne: 'macd', 'macd_signal', 'macd_hist'.
    """
    ema_fast = calculate_ema(series, fast)
    ema_slow = calculate_ema(series, slow)

    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line

    return pd.DataFrame({
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_hist": histogram,
    })


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Arricchisce un DataFrame di candele OHLCV con tutti gli indicatori.

    Il DataFrame in input deve avere almeno le colonne:
    'timestamp', 'open', 'high', 'low', 'close', 'volume'.

    Aggiunge le colonne:
    - rsi (14 periodi)
    - macd, macd_signal, macd_hist
    - ema_short (EMA 9)
    - ema_long  (EMA 21)

    Args:
        df: DataFrame con candele OHLCV.

    Returns:
        DataFrame arricchito (copia, l'originale non viene modificato).
    """
    if df.empty:
        logger.warning("DataFrame vuoto, nessun indicatore calcolato.")
        return df

    result = df.copy()
    close = result["close"]

    # RSI
    result["rsi"] = calculate_rsi(close, period=14)

    # MACD
    macd_df = calculate_macd(close, fast=12, slow=26, signal=9)
    result["macd"] = macd_df["macd"]
    result["macd_signal"] = macd_df["macd_signal"]
    result["macd_hist"] = macd_df["macd_hist"]

    # EMA short (9) e long (21)
    result["ema_short"] = calculate_ema(close, period=9)
    result["ema_long"] = calculate_ema(close, period=21)

    logger.info(
        "Indicatori calcolati: %d righe, ultimo RSI=%.2f",
        len(result),
        result["rsi"].iloc[-1] if not result["rsi"].isna().all() else 0,
    )
    return result
