"""
main.py – Orchestratore del Crypto Trading Bot.

Entry point: inizializza DB, crea gli agenti, schedula due job con APScheduler:
  • trading_cycle  (ogni 15 min): Data → Strategy → Execution + check ordini aperti
  • sentiment_cycle (ogni 2 ore):  Sentiment Agent

Gestisce graceful shutdown su SIGINT / SIGTERM.
"""

import signal
import sys
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from utils.config import (
    DATA_FETCH_INTERVAL_MIN,
    SENTIMENT_FETCH_INTERVAL_MIN,
    LOG_LEVEL,
    LOG_DIR,
)
from utils.db_manager import DatabaseManager
from utils.api_clients import ExchangeClient, NewsClient, LLMClient
from core_agents.data_agent import DataAgent
from core_agents.sentiment_agent import SentimentAgent
from core_agents.strategy_agent import StrategyAgent
from core_agents.execution_agent import ExecutionAgent

# ═════════════════════════════════════════════════════════════════════════════
# Logging Setup
# ═════════════════════════════════════════════════════════════════════════════

def setup_logging() -> None:
    """Configura logging su console + file rotativo."""
    log_format = (
        "%(asctime)s │ %(levelname)-8s │ %(name)-25s │ %(message)s"
    )
    date_format = "%Y-%m-%d %H:%M:%S"

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    root_logger.addHandler(console)

    # File handler (rotativo, max 5MB × 3 file)
    log_file = LOG_DIR / "bot.log"
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    root_logger.addHandler(file_handler)

    logging.info("Logging inizializzato. Livello: %s, File: %s", LOG_LEVEL, log_file)


logger = logging.getLogger("main")

# ═════════════════════════════════════════════════════════════════════════════
# Componenti globali (inizializzati in main)
# ═════════════════════════════════════════════════════════════════════════════

db: DatabaseManager
exchange: ExchangeClient
news_client: NewsClient
llm_client: LLMClient
data_agent: DataAgent
sentiment_agent: SentimentAgent
strategy_agent: StrategyAgent
execution_agent: ExecutionAgent
scheduler: BlockingScheduler


# ═════════════════════════════════════════════════════════════════════════════
# Job: Ciclo di Trading (ogni 15 min)
# ═════════════════════════════════════════════════════════════════════════════

def trading_cycle() -> None:
    """Pipeline completa: Data → Strategy → Execution + check ordini aperti."""
    cycle_start = datetime.now()
    logger.info("═" * 60)
    logger.info("TRADING CYCLE avviato – %s", cycle_start.strftime("%H:%M:%S"))
    logger.info("═" * 60)

    try:
        # Step 1: Raccolta dati + indicatori
        logger.info("▶ Step 1/3: DataAgent – Fetch candele e indicatori...")
        data_results = data_agent.run()
        for symbol, info in data_results.items():
            if "error" in info:
                logger.warning("  ⚠ %s: %s", symbol, info["error"])
            else:
                logger.info(
                    "  ✓ %s: close=%.2f, RSI=%.2f",
                    symbol, info.get("close", 0), info.get("rsi", 0),
                )

        # Step 2: Decisione strategica
        logger.info("▶ Step 2/3: StrategyAgent – Analisi e decisione LLM...")
        decisions = strategy_agent.run()
        for dec in decisions:
            logger.info(
                "  → %s: %s | %s",
                dec.get("symbol"), dec.get("action"),
                dec.get("reasoning", "")[:60],
            )

        # Step 3: Esecuzione ordini
        logger.info("▶ Step 3/3: ExecutionAgent – Esecuzione ordini...")
        exec_results = execution_agent.run(decisions)
        for res in exec_results:
            logger.info(
                "  → %s: %s (%s)",
                res.get("symbol"), res.get("action"), res.get("status"),
            )

        # Check ordini aperti
        logger.info("▶ Check ordini aperti...")
        closed = execution_agent.check_open_orders()
        if closed:
            for c in closed:
                logger.info(
                    "  ✓ Ordine #%d chiuso (%s), P&L=%.2f",
                    c["id"], c["reason"], c["pnl"],
                )
        else:
            logger.info("  Nessun ordine chiuso in questo ciclo.")

    except Exception as e:
        logger.error("ERRORE nel trading cycle: %s", e, exc_info=True)

    elapsed = (datetime.now() - cycle_start).total_seconds()
    logger.info("Trading cycle completato in %.1f secondi.\n", elapsed)


# ═════════════════════════════════════════════════════════════════════════════
# Job: Ciclo Sentiment (ogni 2 ore)
# ═════════════════════════════════════════════════════════════════════════════

def sentiment_cycle() -> None:
    """Ciclo di analisi del sentiment."""
    logger.info("─" * 60)
    logger.info("SENTIMENT CYCLE avviato")
    logger.info("─" * 60)

    try:
        result = sentiment_agent.run()
        if "error" in result:
            logger.warning("  ⚠ Sentiment: %s", result["error"])
        else:
            logger.info(
                "  ✓ Sentiment: Score=%s/10, '%s'",
                result.get("score"), result.get("summary"),
            )
    except Exception as e:
        logger.error("ERRORE nel sentiment cycle: %s", e, exc_info=True)


# ═════════════════════════════════════════════════════════════════════════════
# Graceful Shutdown
# ═════════════════════════════════════════════════════════════════════════════

def graceful_shutdown(signum, frame) -> None:
    """Gestisce la chiusura pulita del bot."""
    sig_name = signal.Signals(signum).name
    logger.info("Segnale %s ricevuto. Arresto in corso...", sig_name)

    try:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler arrestato.")
    except Exception:
        pass

    try:
        db.close()
        logger.info("Database chiuso.")
    except Exception:
        pass

    logger.info("Bot arrestato con successo. Arrivederci! 👋")
    sys.exit(0)


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Entry point del Crypto Trading Bot."""
    global db, exchange, news_client, llm_client
    global data_agent, sentiment_agent, strategy_agent, execution_agent
    global scheduler

    # 1. Logging
    setup_logging()
    logger.info("🚀 Crypto Trading Bot – Avvio")

    # 2. Signal handlers (graceful shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    # 3. Inizializza componenti infrastrutturali
    logger.info("Inizializzazione componenti...")
    db = DatabaseManager()
    db.init_db()

    exchange = ExchangeClient()
    news_client = NewsClient()
    llm_client = LLMClient()

    # 4. Inizializza agenti
    data_agent = DataAgent(exchange=exchange, db=db)
    sentiment_agent = SentimentAgent(
        news_client=news_client, llm_client=llm_client, db=db
    )
    strategy_agent = StrategyAgent(llm_client=llm_client, db=db)
    execution_agent = ExecutionAgent(exchange=exchange, db=db)

    logger.info("Tutti i componenti inizializzati con successo.")

    # 5. Esecuzione immediata al primo avvio
    logger.info("Esecuzione iniziale dei cicli...")
    sentiment_cycle()
    trading_cycle()

    # 6. Scheduler APScheduler
    scheduler = BlockingScheduler()

    scheduler.add_job(
        trading_cycle,
        "interval",
        minutes=DATA_FETCH_INTERVAL_MIN,
        id="trading_cycle",
        name="Trading Cycle (Data → Strategy → Execution)",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        sentiment_cycle,
        "interval",
        minutes=SENTIMENT_FETCH_INTERVAL_MIN,
        id="sentiment_cycle",
        name="Sentiment Cycle",
        max_instances=1,
        coalesce=True,
    )

    logger.info(
        "Scheduler avviato: trading ogni %d min, sentiment ogni %d min.",
        DATA_FETCH_INTERVAL_MIN,
        SENTIMENT_FETCH_INTERVAL_MIN,
    )
    logger.info("Premi Ctrl+C per arrestare il bot.\n")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        graceful_shutdown(signal.SIGINT, None)


if __name__ == "__main__":
    main()
