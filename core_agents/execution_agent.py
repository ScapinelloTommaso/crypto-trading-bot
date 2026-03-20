"""
execution_agent.py – Agente di esecuzione ordini sulla Testnet.

Responsabilità:
- Riceve le decisioni dallo StrategyAgent
- Controlla la sicurezza (hard check SL/TP)
- Calcola la size dell'ordine rispettando il risk management (max 2%)
- Piazza ordini market + SL/TP sulla Binance Testnet
- Registra tutto nel database SQLite
- Monitora gli ordini aperti e aggiorna P&L

VINCOLO ASSOLUTO: Nessun ordine viene inviato se SL e TP sono assenti
o incoerenti rispetto al prezzo di entrata.
"""

import logging
from typing import Optional

from utils.config import RISK_PER_TRADE
from utils.api_clients import ExchangeClient
from utils.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


class OrderSafetyError(Exception):
    """Eccezione per ordini che non superano il controllo di sicurezza SL/TP."""
    pass


class ExecutionAgent:
    """Agente di esecuzione: decisione → validazione SL/TP → ordine Testnet → DB."""

    def __init__(self, exchange: ExchangeClient, db: DatabaseManager):
        self.exchange = exchange
        self.db = db

    def run(self, decisions: list[dict]) -> list[dict]:
        """Esegue le decisioni ricevute dallo StrategyAgent.

        Args:
            decisions: Lista di dict, ognuno con chiavi:
                       symbol, action, reasoning, stop_loss, take_profit

        Returns:
            Lista di risultati per ogni decisione processata.
        """
        results = []

        for decision in decisions:
            action = decision.get("action", "HOLD").upper()

            if action == "HOLD":
                logger.info(
                    "[ExecutionAgent] %s → HOLD, nessun ordine.",
                    decision.get("symbol"),
                )
                results.append({
                    "symbol": decision.get("symbol"),
                    "action": "HOLD",
                    "status": "skipped",
                })
                continue

            try:
                result = self._execute_order(decision)
                results.append(result)
            except OrderSafetyError as e:
                logger.error("[ExecutionAgent] BLOCCATO: %s", e)
                results.append({
                    "symbol": decision.get("symbol"),
                    "action": action,
                    "status": "blocked_safety",
                    "error": str(e),
                })
            except Exception as e:
                logger.error(
                    "[ExecutionAgent] Errore esecuzione %s: %s",
                    decision.get("symbol"), e,
                )
                results.append({
                    "symbol": decision.get("symbol"),
                    "action": action,
                    "status": "error",
                    "error": str(e),
                })

        return results

    def _execute_order(self, decision: dict) -> dict:
        """Esegue un singolo ordine con tutti i controlli di sicurezza.

        Flusso:
        1. Hard check SL/TP (BLOCCANTE)
        2. Fetch prezzo corrente
        3. Validazione coerenza SL/TP vs prezzo
        4. Calcolo size (max RISK_PER_TRADE del balance)
        5. Ordine market
        6. Log su DB

        Raises:
            OrderSafetyError: Se SL/TP non superano i controlli.
        """
        symbol = decision["symbol"]
        action = decision["action"].upper()
        stop_loss = decision.get("stop_loss", 0)
        take_profit = decision.get("take_profit", 0)
        side = "buy" if action == "BUY" else "sell"

        # ═══════════════════════════════════════════════════════════════
        # HARD CHECK #1: SL e TP devono essere presenti e > 0
        # ═══════════════════════════════════════════════════════════════
        if not stop_loss or stop_loss <= 0:
            raise OrderSafetyError(
                f"Stop Loss assente o non valido ({stop_loss}) per {symbol}. "
                f"Ordine BLOCCATO."
            )

        if not take_profit or take_profit <= 0:
            raise OrderSafetyError(
                f"Take Profit assente o non valido ({take_profit}) per {symbol}. "
                f"Ordine BLOCCATO."
            )

        # ═══════════════════════════════════════════════════════════════
        # Fetch prezzo corrente
        # ═══════════════════════════════════════════════════════════════
        ticker = self.exchange.fetch_ticker(symbol)
        current_price = ticker["last"]

        if not current_price or current_price <= 0:
            raise OrderSafetyError(
                f"Prezzo corrente non disponibile per {symbol}. Ordine BLOCCATO."
            )

        # ═══════════════════════════════════════════════════════════════
        # HARD CHECK #2: Coerenza SL/TP rispetto al prezzo e al lato
        # ═══════════════════════════════════════════════════════════════
        if action == "BUY":
            # Per un BUY: SL deve essere SOTTO il prezzo, TP deve essere SOPRA
            if stop_loss >= current_price:
                raise OrderSafetyError(
                    f"BUY {symbol}: Stop Loss ({stop_loss:.2f}) >= prezzo "
                    f"corrente ({current_price:.2f}). Deve essere inferiore. "
                    f"Ordine BLOCCATO."
                )
            if take_profit <= current_price:
                raise OrderSafetyError(
                    f"BUY {symbol}: Take Profit ({take_profit:.2f}) <= prezzo "
                    f"corrente ({current_price:.2f}). Deve essere superiore. "
                    f"Ordine BLOCCATO."
                )

        elif action == "SELL":
            # Per un SELL: SL deve essere SOPRA il prezzo, TP deve essere SOTTO
            if stop_loss <= current_price:
                raise OrderSafetyError(
                    f"SELL {symbol}: Stop Loss ({stop_loss:.2f}) <= prezzo "
                    f"corrente ({current_price:.2f}). Deve essere superiore. "
                    f"Ordine BLOCCATO."
                )
            if take_profit >= current_price:
                raise OrderSafetyError(
                    f"SELL {symbol}: Take Profit ({take_profit:.2f}) >= prezzo "
                    f"corrente ({current_price:.2f}). Deve essere inferiore. "
                    f"Ordine BLOCCATO."
                )

        # ═══════════════════════════════════════════════════════════════
        # Calcolo size dell'ordine (Risk Management)
        # ═══════════════════════════════════════════════════════════════
        amount = self._calculate_order_size(
            symbol, current_price, stop_loss, side
        )

        if amount <= 0:
            raise OrderSafetyError(
                f"Size ordine calcolata <= 0 per {symbol}. "
                f"Balance insufficiente. Ordine BLOCCATO."
            )

        # ═══════════════════════════════════════════════════════════════
        # Piazzamento ordine market
        # ═══════════════════════════════════════════════════════════════
        logger.info(
            "[ExecutionAgent] Piazzo ordine MARKET %s %s, amount=%.6f, "
            "SL=%.2f, TP=%.2f",
            side.upper(), symbol, amount, stop_loss, take_profit,
        )

        order = self.exchange.create_market_order(symbol, side, amount)
        entry_price = float(order.get("average", order.get("price", current_price)))
        exchange_order_id = str(order.get("id", ""))

        # ═══════════════════════════════════════════════════════════════
        # Salva l'ordine nel DB
        # ═══════════════════════════════════════════════════════════════
        db_order_id = self.db.insert_order(
            symbol=symbol,
            side=side,
            amount=amount,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            exchange_order_id=exchange_order_id,
        )

        logger.info(
            "[ExecutionAgent] Ordine piazzato con successo. "
            "DB_ID=%d, Exchange_ID=%s, Entry=%.2f",
            db_order_id, exchange_order_id, entry_price,
        )

        return {
            "symbol": symbol,
            "action": action,
            "status": "executed",
            "db_order_id": db_order_id,
            "exchange_order_id": exchange_order_id,
            "entry_price": entry_price,
            "amount": amount,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }

    def _calculate_order_size(
        self, symbol: str, current_price: float,
        stop_loss: float, side: str,
    ) -> float:
        """Calcola la quantità da tradare rispettando il risk management.

        Formula:
            risk_amount = balance_USDT * RISK_PER_TRADE
            price_risk  = |current_price - stop_loss|
            amount      = risk_amount / price_risk

        In questo modo, se lo SL viene colpito, la perdita massima è
        pari al RISK_PER_TRADE% del portafoglio.
        """
        balance = self.exchange.fetch_balance()

        # Cerca il saldo USDT disponibile
        usdt_free = float(balance.get("free", {}).get("USDT", 0))
        if usdt_free <= 0:
            logger.warning("[ExecutionAgent] Saldo USDT = 0.")
            return 0.0

        risk_amount = usdt_free * RISK_PER_TRADE
        price_risk = abs(current_price - stop_loss)

        if price_risk <= 0:
            logger.warning("[ExecutionAgent] Price risk = 0, impossibile calcolare size.")
            return 0.0

        amount = risk_amount / price_risk

        logger.info(
            "[ExecutionAgent] Size calc: balance=%.2f USDT, risk=%.2f, "
            "price_risk=%.2f, amount=%.6f %s",
            usdt_free, risk_amount, price_risk, amount,
            symbol.split("/")[0],
        )
        return amount

    def check_open_orders(self) -> list[dict]:
        """Controlla lo stato degli ordini aperti e aggiorna il DB.

        Per ogni ordine con status='open':
        - Confronta il prezzo corrente con SL e TP
        - Se il prezzo ha raggiunto SL o TP, marca l'ordine come 'closed'
          e calcola il P&L

        Returns:
            Lista di ordini aggiornati.
        """
        open_orders = self.db.get_open_orders()
        updated = []

        for order in open_orders:
            try:
                result = self._check_single_order(order)
                if result:
                    updated.append(result)
            except Exception as e:
                logger.error(
                    "[ExecutionAgent] Errore check ordine %d: %s",
                    order["id"], e,
                )

        if updated:
            logger.info(
                "[ExecutionAgent] %d ordini chiusi in questo check.", len(updated)
            )
        return updated

    def _check_single_order(self, order: dict) -> Optional[dict]:
        """Controlla un singolo ordine vs prezzo corrente."""
        symbol = order["symbol"]
        side = order["side"]
        entry_price = order["entry_price"]
        stop_loss = order["stop_loss"]
        take_profit = order["take_profit"]

        ticker = self.exchange.fetch_ticker(symbol)
        current_price = ticker["last"]

        if side == "buy":
            # BUY: TP raggiunto se prezzo >= TP, SL raggiunto se prezzo <= SL
            if current_price >= take_profit:
                pnl = (take_profit - entry_price) * order["amount"]
                self.db.update_order_status(order["id"], "closed", pnl)
                logger.info("[ExecutionAgent] TP raggiunto per ordine %d, P&L=%.2f", order["id"], pnl)
                return {"id": order["id"], "status": "closed", "reason": "take_profit", "pnl": pnl}
            elif current_price <= stop_loss:
                pnl = (stop_loss - entry_price) * order["amount"]
                self.db.update_order_status(order["id"], "closed", pnl)
                logger.info("[ExecutionAgent] SL raggiunto per ordine %d, P&L=%.2f", order["id"], pnl)
                return {"id": order["id"], "status": "closed", "reason": "stop_loss", "pnl": pnl}

        elif side == "sell":
            # SELL: TP raggiunto se prezzo <= TP, SL raggiunto se prezzo >= SL
            if current_price <= take_profit:
                pnl = (entry_price - take_profit) * order["amount"]
                self.db.update_order_status(order["id"], "closed", pnl)
                logger.info("[ExecutionAgent] TP raggiunto per ordine %d, P&L=%.2f", order["id"], pnl)
                return {"id": order["id"], "status": "closed", "reason": "take_profit", "pnl": pnl}
            elif current_price >= stop_loss:
                pnl = (entry_price - stop_loss) * order["amount"]
                self.db.update_order_status(order["id"], "closed", pnl)
                logger.info("[ExecutionAgent] SL raggiunto per ordine %d, P&L=%.2f", order["id"], pnl)
                return {"id": order["id"], "status": "closed", "reason": "stop_loss", "pnl": pnl}

        return None  # Ordine ancora aperto
