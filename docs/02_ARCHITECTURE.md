# Architettura del Sistema

Il sistema è diviso in tre moduli principali che comunicano tramite un database SQLite locale.

1. **Modulo di Raccolta Dati (Cronjob / Loop continuo):**
   - Ogni 15 minuti, scarica i prezzi (candele 15m) delle crypto target tramite `ccxt`.
   - Calcola indicatori tecnici di base (RSI, MACD, EMA).
   - Ogni 2 ore, scarica il sentiment di mercato tramite API gratuite (CryptoPanic/Reddit).

2. **Motore Decisionale (LLM):**
   - Unisce i dati tecnici e il sentiment in un prompt testuale compatto.
   - Invia il prompt all'LLM (tramite API Groq/Gemini).
   - L'LLM restituisce un JSON strutturato con l'azione da compiere (BUY, SELL, HOLD) e i parametri di rischio.

3. **Modulo di Esecuzione e UI:**
   - L'Execution Agent piazza l'ordine sulla Testnet tramite `ccxt` e lo salva nel DB locale.
   - Streamlit legge il DB per mostrare: saldo portafoglio, posizioni aperte, storico operazioni e l'ultimo ragionamento dell'LLM.