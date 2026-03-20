"""
api_clients.py – Wrapper per le API esterne del Crypto Trading Bot.

Contiene:
- ExchangeClient:  ccxt Binance Testnet (fetch candele, ordini, saldo)
- NewsClient:      CryptoPanic API + fallback scraping Reddit
- LLMClient:       Groq (LLaMA 3) via groq SDK
"""

import logging
import re
import time
from typing import Optional

import ccxt
import requests
from bs4 import BeautifulSoup
from groq import Groq
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from utils.config import (
    BINANCE_API_KEY,
    BINANCE_SECRET,
    CRYPTOPANIC_API_KEY,
    GROQ_API_KEY,
    GROQ_MODEL_FAST,
    CANDLE_TIMEFRAME,
    CANDLE_LIMIT,
)

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# Exchange Client  (ccxt – Binance Testnet)
# ═════════════════════════════════════════════════════════════════════════════

class ExchangeClient:
    """Wrapper attorno a ccxt.binance configurato in modalità Testnet/Sandbox."""

    def __init__(self):
        self.exchange = ccxt.binance({
            "apiKey": BINANCE_API_KEY,
            "secret": BINANCE_SECRET,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
                "adjustForTimeDifference": True,
            },
        })
        self.exchange.set_sandbox_mode(True)
        logger.info("ExchangeClient inizializzato (Binance Testnet).")

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = CANDLE_TIMEFRAME,
        limit: int = CANDLE_LIMIT,
    ) -> list[list]:
        """Scarica candele OHLCV dalla Testnet.

        Returns:
            Lista di liste [[timestamp, O, H, L, C, V], ...].
        """
        try:
            candles = self.exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=limit
            )
            logger.info("Scaricate %d candele %s (%s)", len(candles), symbol, timeframe)
            return candles
        except ccxt.BaseError as e:
            logger.error("Errore fetch OHLCV %s: %s", symbol, e)
            raise

    def fetch_balance(self) -> dict:
        """Ritorna il saldo del conto Testnet.

        Returns:
            Dict con chiavi 'total', 'free', 'used' e sotto-dict per ogni asset.
        """
        try:
            balance = self.exchange.fetch_balance()
            logger.info("Saldo recuperato con successo.")
            return balance
        except ccxt.BaseError as e:
            logger.error("Errore fetch balance: %s", e)
            raise

    def fetch_ticker(self, symbol: str) -> dict:
        """Ritorna il ticker corrente per un simbolo.

        Returns:
            Dict ccxt con 'last', 'bid', 'ask', 'high', 'low', ecc.
        """
        try:
            return self.exchange.fetch_ticker(symbol)
        except ccxt.BaseError as e:
            logger.error("Errore fetch ticker %s: %s", symbol, e)
            raise

    def create_market_order(self, symbol: str, side: str, amount: float) -> dict:
        """Piazza un ordine market sulla Testnet.

        Args:
            symbol: Coppia (es. 'BTC/USDT').
            side: 'buy' o 'sell'.
            amount: Quantità base da tradare.

        Returns:
            Dict dell'ordine creato.
        """
        try:
            order = self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=amount,
            )
            logger.info(
                "Ordine market %s %s %.6f piazzato. ID: %s",
                side.upper(), symbol, amount, order.get("id"),
            )
            return order
        except ccxt.BaseError as e:
            logger.error("Errore create_market_order %s %s: %s", side, symbol, e)
            raise

    def create_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> dict:
        """Piazza un ordine limit sulla Testnet (usato per SL/TP)."""
        try:
            order = self.exchange.create_order(
                symbol=symbol,
                type="limit",
                side=side,
                amount=amount,
                price=price,
            )
            logger.info(
                "Ordine limit %s %s %.6f @ %.2f. ID: %s",
                side.upper(), symbol, amount, price, order.get("id"),
            )
            return order
        except ccxt.BaseError as e:
            logger.error("Errore create_limit_order: %s", e)
            raise

    def fetch_order(self, order_id: str, symbol: str) -> dict:
        """Recupera lo stato di un ordine dall'exchange."""
        try:
            return self.exchange.fetch_order(order_id, symbol)
        except ccxt.BaseError as e:
            logger.error("Errore fetch_order %s: %s", order_id, e)
            raise


# ═════════════════════════════════════════════════════════════════════════════
# News Client  (CryptoPanic + Reddit fallback)
# ═════════════════════════════════════════════════════════════════════════════

class NewsClient:
    """Client per raccogliere titoli di news crypto da fonti gratuite."""

    CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"
    REDDIT_URL = "https://www.reddit.com/r/CryptoCurrency/hot.json"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "CryptoTradingBot/1.0 (educational project)"
        })

    def _fetch_cryptopanic(self, limit: int = 20) -> list[str]:
        """Fetch titoli da CryptoPanic API (gratuita)."""
        if not CRYPTOPANIC_API_KEY:
            logger.warning("CRYPTOPANIC_API_KEY non configurata, skip.")
            return []

        try:
            resp = self.session.get(
                self.CRYPTOPANIC_URL,
                params={"auth_token": CRYPTOPANIC_API_KEY, "public": "true"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            titles = [r.get("title", "") for r in data.get("results", [])[:limit]]
            logger.info("CryptoPanic: %d titoli recuperati.", len(titles))
            return titles
        except requests.RequestException as e:
            logger.warning("CryptoPanic fallito: %s", e)
            return []

    def _fetch_reddit(self, limit: int = 15) -> list[str]:
        """Scraping leggero di Reddit /r/CryptoCurrency (fallback)."""
        try:
            resp = self.session.get(
                self.REDDIT_URL,
                params={"limit": limit},
                timeout=15,
            )
            resp.raise_for_status()
            posts = resp.json().get("data", {}).get("children", [])
            titles = [p["data"]["title"] for p in posts if "data" in p]
            logger.info("Reddit: %d titoli recuperati.", len(titles))
            return titles
        except requests.RequestException as e:
            logger.warning("Reddit scraping fallito: %s", e)
            return []

    def fetch_crypto_news(self) -> list[str]:
        """Ritorna una lista aggregata di titoli di news crypto.

        Prova prima CryptoPanic, poi Reddit come fallback.
        """
        titles = self._fetch_cryptopanic()
        if len(titles) < 5:
            logger.info("Pochi titoli da CryptoPanic, aggiungo Reddit.")
            titles.extend(self._fetch_reddit())

        # De-duplicazione preservando l'ordine
        seen = set()
        unique = []
        for t in titles:
            if t and t not in seen:
                seen.add(t)
                unique.append(t)

        logger.info("Totale titoli news unici: %d", len(unique))
        return unique


# ═════════════════════════════════════════════════════════════════════════════
# LLM Client  (Groq – LLaMA 3)
# ═════════════════════════════════════════════════════════════════════════════

class LLMClient:
    """Client per Groq (LLaMA 3) via groq SDK.

    Il parametro `model` in `chat_completion` permette di scegliere
    il modello per ogni agente (es. llama3-8b per sentiment,
    llama3-70b per strategy).
    """

    def __init__(self):
        if not GROQ_API_KEY:
            raise ValueError(
                "GROQ_API_KEY non configurata. Impostala nel file .env"
            )
        self.client = Groq(api_key=GROQ_API_KEY)
        logger.info("LLMClient inizializzato (Groq). Default model: %s", GROQ_MODEL_FAST)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str = GROQ_MODEL_FAST,
    ) -> str:
        """Invia un prompt a Groq e restituisce la risposta testuale.

        Args:
            system_prompt: Istruzioni di sistema / ruolo dell'agente.
            user_prompt: Dati e richiesta specifica.
            model: Modello Groq da usare (default: llama3-8b-8192).

        Returns:
            Stringa con la risposta grezza del modello.
        """
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=1024,
            )

            text = response.choices[0].message.content.strip()
            logger.info(
                "Risposta LLM ricevuta (%d caratteri, model=%s).",
                len(text), model,
            )
            return text

        except Exception as e:
            logger.error("Errore chiamata Groq: %s", e)
            raise
