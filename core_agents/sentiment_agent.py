"""
sentiment_agent.py – Agente per l'analisi del sentiment di mercato.

Responsabilità:
- Raccoglie titoli di news crypto da CryptoPanic / Reddit
- Invia i titoli a Gemini Flash con il system prompt documentato
- Estrae un punteggio 1-10 e una sintesi ≤ 20 parole
- Salva il risultato nel database SQLite

System prompt di riferimento: docs/03_AGENTS_PROMPTS.md §2
"""

import json
import re
import logging

from utils.api_clients import NewsClient, LLMClient
from utils.db_manager import DatabaseManager

logger = logging.getLogger(__name__)

# ── System Prompt (da docs/03_AGENTS_PROMPTS.md §2) ─────────────────────────
SENTIMENT_SYSTEM_PROMPT = (
    "Ruolo: Analista di mercato.\n"
    "Compito: Leggere i titoli delle ultime 2 ore estratti da CryptoPanic e "
    "Reddit e restituire un punteggio da 1 (Panico/Bearish) a 10 "
    "(Euforia/Bullish) con una sintesi di massimo 20 parole.\n"
    "Rispondi ESCLUSIVAMENTE con un JSON valido nel formato:\n"
    '{"score": <int 1-10>, "summary": "<sintesi max 20 parole>"}\n'
    "Non aggiungere testo prima o dopo il JSON."
)


class SentimentAgent:
    """Agente di analisi del sentiment: news → LLM → score + sintesi → DB."""

    def __init__(self, news_client: NewsClient, llm_client: LLMClient,
                 db: DatabaseManager):
        self.news = news_client
        self.llm = llm_client
        self.db = db

    def run(self) -> dict:
        """Esegue un ciclo di analisi del sentiment.

        1. Fetch titoli news da CryptoPanic + Reddit
        2. Compone il prompt con i titoli
        3. Invia a Gemini Flash → parsing JSON → estrae score e summary
        4. Salva nel DB

        Returns:
            Dict {"score": float, "summary": str} oppure
            {"error": str} in caso di errore.
        """
        try:
            # 1. Raccolta titoli
            titles = self.news.fetch_crypto_news()
            if not titles:
                logger.warning("[SentimentAgent] Nessun titolo recuperato.")
                return {"error": "Nessun titolo di news disponibile."}

            # 2. Componi user prompt
            titles_text = "\n".join(f"- {t}" for t in titles[:25])
            user_prompt = (
                "Ecco i titoli di news crypto delle ultime ore:\n\n"
                f"{titles_text}\n\n"
                "Analizza il sentiment complessivo e rispondi con il JSON richiesto."
            )

            # 3. Chiamata LLM (modello veloce per sentiment)
            from utils.config import GROQ_MODEL_FAST
            raw_response = self.llm.chat_completion(
                system_prompt=SENTIMENT_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                model=GROQ_MODEL_FAST,
            )

            # 4. Parsing (con estrazione JSON robusta)
            parsed = self._parse_response(raw_response)

            # 5. Salvataggio nel DB
            self.db.insert_sentiment(
                source="cryptopanic+reddit",
                score=parsed["score"],
                summary=parsed["summary"],
            )

            logger.info(
                "[SentimentAgent] Score=%d, Summary='%s'",
                parsed["score"], parsed["summary"],
            )
            return parsed

        except Exception as e:
            logger.error("[SentimentAgent] Errore: %s", e)
            return {"error": str(e)}

    def _parse_response(self, raw: str) -> dict:
        """Estrae score e summary dalla risposta LLM.

        Gestisce i casi in cui l'LLM aggiunge testo discorsivo
        prima/dopo il JSON, o usa code fences (```json ... ```).

        Returns:
            {"score": int, "summary": str}

        Raises:
            ValueError: Se non riesce a estrarre un JSON valido.
        """
        # Tentativo 1: parsing diretto
        try:
            data = json.loads(raw.strip())
            return self._validate_sentiment(data)
        except json.JSONDecodeError:
            pass

        # Tentativo 2: rimuovi code fences e riprova
        cleaned = re.sub(r"```(?:json)?\s*", "", raw)
        cleaned = cleaned.strip().rstrip("`")
        try:
            data = json.loads(cleaned)
            return self._validate_sentiment(data)
        except json.JSONDecodeError:
            pass

        # Tentativo 3: cerca il primo blocco {...} nella risposta
        match = re.search(r"\{[^}]+\}", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                return self._validate_sentiment(data)
            except json.JSONDecodeError:
                pass

        # Tentativo 4: regex fallback per estrarre score numerico
        score_match = re.search(r'"?score"?\s*[:=]\s*(\d+)', raw)
        summary_match = re.search(r'"?summary"?\s*[:=]\s*"([^"]+)"', raw)
        if score_match:
            score = int(score_match.group(1))
            summary = summary_match.group(1) if summary_match else "N/A"
            return self._validate_sentiment({"score": score, "summary": summary})

        raise ValueError(
            f"Impossibile estrarre sentiment dalla risposta LLM: {raw[:200]}"
        )

    @staticmethod
    def _validate_sentiment(data: dict) -> dict:
        """Valida e normalizza i dati di sentiment."""
        score = int(data.get("score", 5))
        score = max(1, min(10, score))  # Clamp 1-10
        summary = str(data.get("summary", "N/A"))[:100]  # Max 100 chars
        return {"score": score, "summary": summary}
