# Regole d'Oro del Progetto (Core Rules)

1. **Obiettivo:** Creare un bot di paper trading crypto basato su un'architettura multi-agente.
2. **Vincolo Economico Assoluto:** Il progetto DEVE avere un costo di esecuzione pari a zero. 
   - Non usare API a pagamento.
   - Per l'IA decisionale, usare chiamate ad API con tier gratuiti generosi (es. Groq per LLaMA 3 o Google Gemini Flash via API).
   - Per le notizie, usare fonti gratuite come CryptoPanic API e web scraping leggero su Reddit.
3. **Ambiente di Esecuzione:** Il codice girerà H24 su un server gratuito Oracle Cloud (Ubuntu ARM/AMD) privo di GPU. Pertanto, i modelli LLM NON devono girare in locale tramite Ollama, ma via chiamate API esterne per non bloccare il server.
4. **Trading:** Si opera SOLO in Testnet (Paper Trading) tramite libreria `ccxt` (es. Binance Testnet).
5. **Tecnologie:** - Backend/Agenti: Python 3
   - UI/Dashboard: Streamlit (esposto dal server)
   - Database: SQLite (locale, per salvare lo storico ordini)
6. **Gestione del Rischio:** L'IA deve SEMPRE impostare uno Stop Loss e un Take Profit per ogni ordine aperto.