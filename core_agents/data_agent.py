"""
data_agent.py – Agente per la raccolta dati di mercato.

Responsabilità:
- Scarica candele OHLCV (15m) dalla Binance Testnet via ccxt
- Calcola indicatori tecnici (RSI, MACD, EMA)
- Salva candele e indicatori nel database SQLite
"""

import logging

import pandas as pd

from utils.config import TRADING_PAIRS
from utils.api_clients import ExchangeClient
from utils.db_manager import DatabaseManager
from utils.indicators import compute_all_indicators

logger = logging.getLogger(__name__)


class DataAgent:
    """Agente di raccolta dati: fetch candele → calcolo indicatori → salvataggio DB."""

    def __init__(self, exchange: ExchangeClient, db: DatabaseManager):
        self.exchange = exchange
        self.db = db

    def run(self) -> dict:
        """Esegue un ciclo completo di raccolta dati per tutte le coppie.

        Per ogni coppia in TRADING_PAIRS:
        1. Scarica le ultime 100 candele 15m dalla Testnet
        2. Salva le candele nel DB (ignora duplicati)
        3. Calcola RSI, MACD, EMA su tutto il set
        4. Salva i valori dell'ultima candela nella tabella indicators

        Returns:
            Dict con riepilogo per ogni coppia:
            {
                "BTC/USDT": {"rsi": 45.2, "macd": -12.3, ...},
                "ETH/USDT": {"rsi": 55.1, "macd": 5.6, ...},
            }
        """
        results = {}

        for symbol in TRADING_PAIRS:
            try:
                summary = self._process_symbol(symbol)
                results[symbol] = summary
                logger.info(
                    "[DataAgent] %s → RSI=%.2f, MACD=%.4f, EMA_short=%.2f, EMA_long=%.2f",
                    symbol,
                    summary.get("rsi", 0),
                    summary.get("macd", 0),
                    summary.get("ema_short", 0),
                    summary.get("ema_long", 0),
                )
            except Exception as e:
                logger.error("[DataAgent] Errore processing %s: %s", symbol, e)
                results[symbol] = {"error": str(e)}

        return results

    def _process_symbol(self, symbol: str) -> dict:
        """Processa una singola coppia: fetch → indicatori → DB."""

        # 1. Fetch candele raw da exchange
        raw_candles = self.exchange.fetch_ohlcv(symbol)
        if not raw_candles:
            raise ValueError(f"Nessuna candela ricevuta per {symbol}")

        # 2. Salva candele nel DB
        self.db.insert_candles(symbol, raw_candles)

        # 3. Converti in DataFrame per il calcolo indicatori
        df = pd.DataFrame(
            raw_candles,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )

        # 4. Calcola tutti gli indicatori
        df_enriched = compute_all_indicators(df)

        # 5. Prendi l'ultima riga (valori più recenti)
        latest = df_enriched.iloc[-1]
        last_timestamp = int(latest["timestamp"])

        # 6. Salva indicatori nel DB
        self.db.insert_indicators(
            symbol=symbol,
            timestamp=last_timestamp,
            rsi=float(latest["rsi"]) if pd.notna(latest["rsi"]) else 0.0,
            macd=float(latest["macd"]) if pd.notna(latest["macd"]) else 0.0,
            macd_signal=float(latest["macd_signal"]) if pd.notna(latest["macd_signal"]) else 0.0,
            macd_hist=float(latest["macd_hist"]) if pd.notna(latest["macd_hist"]) else 0.0,
            ema_short=float(latest["ema_short"]) if pd.notna(latest["ema_short"]) else 0.0,
            ema_long=float(latest["ema_long"]) if pd.notna(latest["ema_long"]) else 0.0,
        )

        # 7. Ritorna riepilogo per l'orchestratore
        return {
            "timestamp": last_timestamp,
            "close": float(latest["close"]),
            "rsi": float(latest["rsi"]) if pd.notna(latest["rsi"]) else 0.0,
            "macd": float(latest["macd"]) if pd.notna(latest["macd"]) else 0.0,
            "macd_signal": float(latest["macd_signal"]) if pd.notna(latest["macd_signal"]) else 0.0,
            "macd_hist": float(latest["macd_hist"]) if pd.notna(latest["macd_hist"]) else 0.0,
            "ema_short": float(latest["ema_short"]) if pd.notna(latest["ema_short"]) else 0.0,
            "ema_long": float(latest["ema_long"]) if pd.notna(latest["ema_long"]) else 0.0,
        }
