"""
strategy_agent.py – Agente decisionale (il "cervello" LLM).

Responsabilità:
- Legge indicatori tecnici e sentiment dal database
- Compone un prompt strutturato per Gemini Flash
- Forza l'LLM a restituire SOLO un JSON valido
- Estrae e valida la decisione (BUY / SELL / HOLD)
- Salva la decisione nel database

System prompt di riferimento: docs/03_AGENTS_PROMPTS.md §1
"""

import json
import re
import time
import logging
from typing import Optional

from utils.config import TRADING_PAIRS, RISK_PER_TRADE
from utils.api_clients import LLMClient
from utils.db_manager import DatabaseManager

logger = logging.getLogger(__name__)

# ── System Prompt (da docs/03_AGENTS_PROMPTS.md §1) ──────────────────────────
STRATEGY_SYSTEM_PROMPT = (
    "Sei un trader professionista specializzato in criptovalute a breve termine "
    "(operazioni intraday).\n"
    "Compito: Analizzare i dati tecnici e il sentiment forniti dal sistema per "
    "decidere se aprire una posizione.\n"
    "Regole:\n"
    f"- Rischia al massimo il {RISK_PER_TRADE*100:.0f}% del portafoglio per operazione.\n"
    "- Inserisci SEMPRE un prezzo di Stop Loss e Take Profit.\n"
    "- Rispondi ESCLUSIVAMENTE con un JSON valido, senza testo aggiuntivo.\n"
    "- Il JSON deve avere esattamente questo formato:\n"
    '{\n'
    '  "azione": "BUY" | "SELL" | "HOLD",\n'
    '  "asset": "<SYMBOL>",\n'
    '  "motivazione": "<spiegazione breve>",\n'
    '  "stop_loss": <prezzo numerico>,\n'
    '  "take_profit": <prezzo numerico>\n'
    '}\n'
    "- Se l'azione è HOLD, imposta stop_loss e take_profit a 0.\n"
    "- Non racchiudere il JSON in code fences o markdown."
)

# Azioni valide accettate dal parser
VALID_ACTIONS = {"BUY", "SELL", "HOLD"}


class StrategyAgent:
    """Agente strategico: indicatori + sentiment → prompt → LLM → decisione JSON."""

    def __init__(self, llm_client: LLMClient, db: DatabaseManager):
        self.llm = llm_client
        self.db = db

    def run(self) -> list[dict]:
        """Esegue un ciclo decisionale per tutte le coppie di trading.

        Per ogni coppia:
        1. Legge indicatori più recenti dal DB
        2. Legge ultimo sentiment dal DB
        3. Compone user prompt con i dati
        4. Chiama Gemini Flash → estrae JSON
        5. Valida la decisione
        6. Salva nel DB

        Returns:
            Lista di decisioni, una per coppia:
            [{"symbol": "BTC/USDT", "action": "BUY", ...}, ...]
        """
        decisions = []

        # Sentiment (condiviso tra tutte le coppie)
        sentiment = self.db.get_latest_sentiment()

        for i, symbol in enumerate(TRADING_PAIRS):
            try:
                decision = self._decide_for_symbol(symbol, sentiment)
                decisions.append(decision)
            except Exception as e:
                logger.error(
                    "[StrategyAgent] Errore decisione %s: %s", symbol, e
                )
                decisions.append({
                    "symbol": symbol,
                    "action": "HOLD",
                    "reasoning": f"Errore: {e}",
                    "stop_loss": 0,
                    "take_profit": 0,
                    "error": str(e),
                })

            # Delay tra le chiamate LLM per evitare rate limit (429)
            if i < len(TRADING_PAIRS) - 1:
                logger.debug("Rate limit delay: attesa 4 secondi...")
                time.sleep(4)

        return decisions

    def _decide_for_symbol(
        self, symbol: str, sentiment: Optional[dict]
    ) -> dict:
        """Genera una decisione per una singola coppia."""

        # 1. Recupera indicatori dal DB
        indicators = self.db.get_latest_indicators(symbol)
        if not indicators:
            logger.warning("[StrategyAgent] Nessun indicatore per %s, HOLD.", symbol)
            return self._hold_decision(symbol, "Nessun dato tecnico disponibile.")

        # 2. Componi user prompt con dati strutturati
        user_prompt = self._build_user_prompt(symbol, indicators, sentiment)

        # 3. Chiamata LLM (modello potente per decisioni)
        from utils.config import GROQ_MODEL_STRONG
        raw_response = self.llm.chat_completion(
            system_prompt=STRATEGY_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=GROQ_MODEL_STRONG,
        )

        # 4. Estrai e valida JSON
        parsed = self._extract_json(raw_response)
        validated = self._validate_decision(parsed, symbol)

        # 5. Salva decisione nel DB
        self.db.insert_decision(
            symbol=validated["symbol"],
            action=validated["action"],
            reasoning=validated["reasoning"],
            stop_loss=validated["stop_loss"],
            take_profit=validated["take_profit"],
            raw_llm_response=raw_response,
        )

        logger.info(
            "[StrategyAgent] %s → %s (SL=%.2f, TP=%.2f) | %s",
            symbol,
            validated["action"],
            validated["stop_loss"],
            validated["take_profit"],
            validated["reasoning"][:80],
        )

        return validated

    def _build_user_prompt(
        self,
        symbol: str,
        indicators: dict,
        sentiment: Optional[dict],
    ) -> str:
        """Compone il prompt testuale con i dati tecnici e di sentiment."""

        # Blocco indicatori
        tech_block = (
            f"== Dati Tecnici per {symbol} ==\n"
            f"RSI (14):        {indicators.get('rsi', 'N/A'):.2f}\n"
            f"MACD:            {indicators.get('macd', 'N/A'):.4f}\n"
            f"MACD Signal:     {indicators.get('macd_signal', 'N/A'):.4f}\n"
            f"MACD Histogram:  {indicators.get('macd_hist', 'N/A'):.4f}\n"
            f"EMA Short (9):   {indicators.get('ema_short', 'N/A'):.2f}\n"
            f"EMA Long (21):   {indicators.get('ema_long', 'N/A'):.2f}\n"
        )

        # Blocco sentiment
        if sentiment:
            sent_block = (
                f"\n== Sentiment di Mercato ==\n"
                f"Score:   {sentiment.get('score', 'N/A')}/10\n"
                f"Sintesi: {sentiment.get('summary', 'N/A')}\n"
            )
        else:
            sent_block = "\n== Sentiment di Mercato ==\nNon disponibile.\n"

        return (
            f"{tech_block}{sent_block}\n"
            f"Sulla base di questi dati, decidi se aprire una posizione su {symbol}. "
            f"Rispondi SOLO con il JSON richiesto."
        )

    def _extract_json(self, raw: str) -> dict:
        """Estrae un oggetto JSON dalla risposta LLM.

        Implementa 4 livelli di fallback per gestire risposte non pulite:
        1. Parsing diretto dell'intera stringa
        2. Rimozione code fences (```json ... ```)
        3. Ricerca del primo blocco { ... } con regex
        4. Fallback a HOLD se tutto fallisce

        Returns:
            Dict con almeno le chiavi: azione, asset, motivazione, stop_loss, take_profit

        Raises:
            ValueError: Solo se la risposta è completamente illeggibile.
        """
        # Livello 1: parsing diretto
        try:
            return json.loads(raw.strip())
        except json.JSONDecodeError:
            pass

        # Livello 2: rimuovi code fences
        cleaned = re.sub(r"```(?:json)?\s*", "", raw)
        cleaned = cleaned.strip().rstrip("`")
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Livello 3: cerca il blocco JSON più grande con { ... }
        # Usa un pattern che gestisce JSON annidati
        matches = re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw, re.DOTALL)
        for match in matches:
            try:
                data = json.loads(match)
                if "azione" in data or "action" in data:
                    return data
            except json.JSONDecodeError:
                continue

        # Livello 4: la risposta è illeggibile
        raise ValueError(
            f"Impossibile estrarre JSON dalla risposta LLM: {raw[:300]}"
        )

    def _validate_decision(self, data: dict, symbol: str) -> dict:
        """Valida e normalizza la decisione LLM in un formato standard.

        Gestisce chiavi sia in italiano che in inglese.
        """
        # Normalizza chiave azione
        action = (
            data.get("azione")
            or data.get("action", "HOLD")
        ).upper().strip()

        if action not in VALID_ACTIONS:
            logger.warning(
                "[StrategyAgent] Azione non valida '%s', fallback HOLD.", action
            )
            action = "HOLD"

        # Normalizza asset
        asset = data.get("asset", symbol)

        # Reasoning
        reasoning = str(
            data.get("motivazione")
            or data.get("reasoning")
            or data.get("motivation")
            or "Nessuna motivazione fornita."
        )

        # SL / TP
        try:
            stop_loss = float(data.get("stop_loss", 0))
        except (TypeError, ValueError):
            stop_loss = 0.0

        try:
            take_profit = float(data.get("take_profit", 0))
        except (TypeError, ValueError):
            take_profit = 0.0

        return {
            "symbol": asset,
            "action": action,
            "reasoning": reasoning,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }

    @staticmethod
    def _hold_decision(symbol: str, reason: str) -> dict:
        """Genera una decisione HOLD di default."""
        return {
            "symbol": symbol,
            "action": "HOLD",
            "reasoning": reason,
            "stop_loss": 0,
            "take_profit": 0,
        }
