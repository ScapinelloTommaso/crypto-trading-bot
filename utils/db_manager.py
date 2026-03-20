"""
db_manager.py – Gestione database SQLite per il Crypto Trading Bot.
Schema con 5 tabelle: candles, indicators, sentiment_logs, decisions, orders.
Thread-safe per uso concorrente tra main loop e Streamlit.
"""

import sqlite3
import logging
from datetime import datetime
from typing import Optional

from utils.config import DB_PATH

logger = logging.getLogger(__name__)


class DatabaseManager:
    """CRUD manager per il database SQLite del bot."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")  # Migliore concorrenza
        logger.info("Connessione DB aperta: %s", db_path)

    # ── Inizializzazione Schema ──────────────────────────────────────────────

    def init_db(self) -> None:
        """Crea tutte le tabelle se non esistono."""
        cursor = self.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT    NOT NULL,
                timestamp   INTEGER NOT NULL,
                open        REAL    NOT NULL,
                high        REAL    NOT NULL,
                low         REAL    NOT NULL,
                close       REAL    NOT NULL,
                volume      REAL    NOT NULL,
                UNIQUE(symbol, timestamp)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS indicators (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT    NOT NULL,
                timestamp   INTEGER NOT NULL,
                rsi         REAL,
                macd        REAL,
                macd_signal REAL,
                macd_hist   REAL,
                ema_short   REAL,
                ema_long    REAL,
                UNIQUE(symbol, timestamp)
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sentiment_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL DEFAULT (datetime('now')),
                source      TEXT    NOT NULL,
                score       REAL    NOT NULL,
                summary     TEXT
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT    NOT NULL DEFAULT (datetime('now')),
                symbol          TEXT    NOT NULL,
                action          TEXT    NOT NULL,
                reasoning       TEXT,
                stop_loss       REAL,
                take_profit     REAL,
                raw_llm_response TEXT
            );
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL DEFAULT (datetime('now')),
                symbol      TEXT    NOT NULL,
                side        TEXT    NOT NULL,
                amount      REAL    NOT NULL,
                entry_price REAL,
                stop_loss   REAL    NOT NULL,
                take_profit REAL    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'open',
                pnl         REAL    DEFAULT 0.0,
                exchange_order_id TEXT
            );
        """)

        self.conn.commit()
        logger.info("Schema DB inizializzato con successo.")

    # ── INSERT ───────────────────────────────────────────────────────────────

    def insert_candles(self, symbol: str, candles: list[list]) -> int:
        """Inserisce candele OHLCV. Formato: [[timestamp, O, H, L, C, V], ...].
        Usa INSERT OR IGNORE per evitare duplicati.
        Ritorna il numero di righe inserite.
        """
        cursor = self.conn.cursor()
        cursor.executemany(
            """INSERT OR IGNORE INTO candles
               (symbol, timestamp, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(symbol, c[0], c[1], c[2], c[3], c[4], c[5]) for c in candles],
        )
        self.conn.commit()
        inserted = cursor.rowcount
        logger.debug("Inserite %d candele per %s", inserted, symbol)
        return inserted

    def insert_indicators(self, symbol: str, timestamp: int,
                          rsi: float, macd: float, macd_signal: float,
                          macd_hist: float, ema_short: float,
                          ema_long: float) -> None:
        """Inserisce una riga di indicatori (INSERT OR REPLACE)."""
        self.conn.execute(
            """INSERT OR REPLACE INTO indicators
               (symbol, timestamp, rsi, macd, macd_signal, macd_hist,
                ema_short, ema_long)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, timestamp, rsi, macd, macd_signal, macd_hist,
             ema_short, ema_long),
        )
        self.conn.commit()

    def insert_sentiment(self, source: str, score: float,
                         summary: str) -> None:
        """Inserisce un log di sentiment."""
        self.conn.execute(
            """INSERT INTO sentiment_logs (source, score, summary)
               VALUES (?, ?, ?)""",
            (source, score, summary),
        )
        self.conn.commit()

    def insert_decision(self, symbol: str, action: str, reasoning: str,
                        stop_loss: Optional[float], take_profit: Optional[float],
                        raw_llm_response: str) -> int:
        """Inserisce una decisione LLM. Ritorna l'id della riga."""
        cursor = self.conn.execute(
            """INSERT INTO decisions
               (symbol, action, reasoning, stop_loss, take_profit,
                raw_llm_response)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (symbol, action, reasoning, stop_loss, take_profit,
             raw_llm_response),
        )
        self.conn.commit()
        return cursor.lastrowid

    def insert_order(self, symbol: str, side: str, amount: float,
                     entry_price: Optional[float], stop_loss: float,
                     take_profit: float,
                     exchange_order_id: str = "") -> int:
        """Inserisce un ordine. Ritorna l'id della riga."""
        cursor = self.conn.execute(
            """INSERT INTO orders
               (symbol, side, amount, entry_price, stop_loss, take_profit,
                exchange_order_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (symbol, side, amount, entry_price, stop_loss, take_profit,
             exchange_order_id),
        )
        self.conn.commit()
        return cursor.lastrowid

    # ── UPDATE ───────────────────────────────────────────────────────────────

    def update_order_status(self, order_id: int, status: str,
                            pnl: float = 0.0,
                            entry_price: Optional[float] = None) -> None:
        """Aggiorna lo stato e il P&L di un ordine."""
        if entry_price is not None:
            self.conn.execute(
                """UPDATE orders
                   SET status = ?, pnl = ?, entry_price = ?
                   WHERE id = ?""",
                (status, pnl, entry_price, order_id),
            )
        else:
            self.conn.execute(
                """UPDATE orders SET status = ?, pnl = ? WHERE id = ?""",
                (status, pnl, order_id),
            )
        self.conn.commit()

    # ── SELECT ───────────────────────────────────────────────────────────────

    def get_open_orders(self) -> list[dict]:
        """Ritorna tutti gli ordini con status='open'."""
        rows = self.conn.execute(
            "SELECT * FROM orders WHERE status = 'open' ORDER BY timestamp DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_order_history(self, limit: int = 50) -> list[dict]:
        """Ritorna lo storico ordini, ordinato per timestamp desc."""
        rows = self.conn.execute(
            "SELECT * FROM orders ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_indicators(self, symbol: str) -> Optional[dict]:
        """Ritorna gli indicatori più recenti per un simbolo."""
        row = self.conn.execute(
            """SELECT * FROM indicators
               WHERE symbol = ?
               ORDER BY timestamp DESC LIMIT 1""",
            (symbol,),
        ).fetchone()
        return dict(row) if row else None

    def get_latest_sentiment(self) -> Optional[dict]:
        """Ritorna l'ultimo log di sentiment."""
        row = self.conn.execute(
            "SELECT * FROM sentiment_logs ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def get_latest_decision(self) -> Optional[dict]:
        """Ritorna l'ultima decisione LLM."""
        row = self.conn.execute(
            "SELECT * FROM decisions ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def get_portfolio_summary(self) -> dict:
        """Ritorna un riepilogo del portafoglio (totale P&L, operazioni)."""
        row = self.conn.execute(
            """SELECT
                   COUNT(*)                          AS total_trades,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS winning,
                   SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losing,
                   COALESCE(SUM(pnl), 0)             AS total_pnl
               FROM orders WHERE status = 'closed'"""
        ).fetchone()
        return dict(row) if row else {
            "total_trades": 0, "winning": 0, "losing": 0, "total_pnl": 0.0
        }

    def get_recent_candles(self, symbol: str, limit: int = 100) -> list[dict]:
        """Ritorna le ultime N candele per un simbolo, ordinate per timestamp asc."""
        rows = self.conn.execute(
            """SELECT timestamp, open, high, low, close, volume
               FROM candles
               WHERE symbol = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (symbol, limit),
        ).fetchall()
        # Inverti per avere ordine cronologico (asc)
        return [dict(r) for r in reversed(rows)]

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Chiude la connessione al database."""
        self.conn.close()
        logger.info("Connessione DB chiusa.")
