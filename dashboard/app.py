"""
app.py – Dashboard Streamlit per il Crypto Trading Bot.

Legge esclusivamente dal database SQLite (sola lettura).
Mostra: saldo, P&L, posizioni aperte, storico ordini,
ultime decisioni LLM, ultimo sentiment, grafici candlestick.

Avviare con: streamlit run dashboard/app.py
"""

import sys
from pathlib import Path

# Aggiungi la root del progetto al path per gli import
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

from utils.db_manager import DatabaseManager
from utils.api_clients import ExchangeClient
from utils.config import TRADING_PAIRS

# ═════════════════════════════════════════════════════════════════════════════
# Page Config
# ═════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Crypto Trading Bot – Dashboard",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ═════════════════════════════════════════════════════════════════════════════
# CSS Personalizzato
# ═════════════════════════════════════════════════════════════════════════════

st.markdown("""
<style>
    /* Metriche principali */
    div[data-testid="metric-container"] {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border: 1px solid #0f3460;
        border-radius: 12px;
        padding: 16px;
    }
    div[data-testid="metric-container"] label {
        color: #a8b2d1 !important;
    }
    div[data-testid="metric-container"] div[data-testid="stMetricValue"] {
        color: #ccd6f6 !important;
    }

    /* Card stile */
    .decision-card {
        background: linear-gradient(135deg, #0d1b2a 0%, #1b2838 100%);
        border: 1px solid #233554;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 12px;
    }
    .sentiment-card {
        background: linear-gradient(135deg, #1a1a2e 0%, #0d1b2a 100%);
        border: 1px solid #0f3460;
        border-radius: 12px;
        padding: 20px;
    }

    /* Header */
    .main-header {
        text-align: center;
        padding: 10px 0 20px 0;
    }

    /* Status badge */
    .badge-buy { color: #64ffda; font-weight: bold; }
    .badge-sell { color: #ff6b6b; font-weight: bold; }
    .badge-hold { color: #ffd93d; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# Connessioni (cache per performance)
# ═════════════════════════════════════════════════════════════════════════════

@st.cache_resource
def get_db() -> DatabaseManager:
    """Connessione DB condivisa (read-only pattern)."""
    db = DatabaseManager()
    return db


@st.cache_resource
def get_exchange() -> ExchangeClient:
    """Client exchange per fetch saldo live."""
    try:
        return ExchangeClient()
    except Exception:
        return None

# ═════════════════════════════════════════════════════════════════════════════
# Header
# ═════════════════════════════════════════════════════════════════════════════

st.markdown(
    '<div class="main-header">'
    '<h1>🤖 Crypto Trading Bot</h1>'
    '<p style="color: #8892b0;">Dashboard di monitoraggio – Paper Trading Testnet</p>'
    '</div>',
    unsafe_allow_html=True,
)

# ═════════════════════════════════════════════════════════════════════════════
# Dati
# ═════════════════════════════════════════════════════════════════════════════

db = get_db()
exchange = get_exchange()

# Fetch metriche
portfolio = db.get_portfolio_summary()
open_orders = db.get_open_orders()
order_history = db.get_order_history(limit=50)
latest_decision = db.get_latest_decision()
latest_sentiment = db.get_latest_sentiment()

# Saldo live dalla Testnet
usdt_balance = 0.0
if exchange:
    try:
        balance = exchange.fetch_balance()
        usdt_balance = float(balance.get("free", {}).get("USDT", 0))
    except Exception:
        usdt_balance = 0.0

# P&L aperto (ordini ancora aperti)
open_pnl = 0.0
if open_orders and exchange:
    for order in open_orders:
        try:
            ticker = exchange.fetch_ticker(order["symbol"])
            current_price = ticker["last"]
            entry = order.get("entry_price", 0)
            amount = order.get("amount", 0)
            if order["side"] == "buy":
                open_pnl += (current_price - entry) * amount
            else:
                open_pnl += (entry - current_price) * amount
        except Exception:
            pass

# ═════════════════════════════════════════════════════════════════════════════
# Metriche Principali
# ═════════════════════════════════════════════════════════════════════════════

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("💰 Saldo USDT", f"${usdt_balance:,.2f}")
with col2:
    st.metric(
        "📈 P&L Aperto",
        f"${open_pnl:,.2f}",
        delta=f"{open_pnl:+,.2f}" if open_pnl != 0 else None,
    )
with col3:
    st.metric(
        "📊 P&L Totale (Chiuso)",
        f"${portfolio['total_pnl']:,.2f}",
        delta=f"{portfolio['total_pnl']:+,.2f}" if portfolio["total_pnl"] != 0 else None,
    )
with col4:
    win_rate = (
        f"{portfolio['winning'] / portfolio['total_trades'] * 100:.0f}%"
        if portfolio["total_trades"] > 0
        else "N/A"
    )
    st.metric(
        "🎯 Win Rate",
        win_rate,
        delta=f"{portfolio['winning']}W / {portfolio['losing']}L"
        if portfolio["total_trades"] > 0
        else None,
    )

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# Grafici Candlestick (Plotly)
# ═════════════════════════════════════════════════════════════════════════════

st.subheader("📊 Grafici Candlestick")

chart_tabs = st.tabs(TRADING_PAIRS)

for tab, symbol in zip(chart_tabs, TRADING_PAIRS):
    with tab:
        candles = db.get_recent_candles(symbol, limit=100)

        if candles:
            df_candles = pd.DataFrame(candles)
            df_candles["datetime"] = pd.to_datetime(
                df_candles["timestamp"], unit="ms"
            )

            fig = go.Figure(data=[
                go.Candlestick(
                    x=df_candles["datetime"],
                    open=df_candles["open"],
                    high=df_candles["high"],
                    low=df_candles["low"],
                    close=df_candles["close"],
                    increasing_line_color="#64ffda",
                    decreasing_line_color="#ff6b6b",
                    increasing_fillcolor="#64ffda",
                    decreasing_fillcolor="#ff6b6b",
                )
            ])

            fig.update_layout(
                title=f"{symbol} – Candele 15m",
                xaxis_title="Tempo",
                yaxis_title="Prezzo (USDT)",
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(13,27,42,0.8)",
                xaxis_rangeslider_visible=False,
                height=450,
                margin=dict(l=50, r=20, t=50, b=40),
                font=dict(color="#a8b2d1"),
            )

            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info(f"Nessuna candela disponibile per {symbol}.")

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# Layout: Decisioni LLM + Sentiment (colonne)
# ═════════════════════════════════════════════════════════════════════════════

col_left, col_right = st.columns([3, 2])

with col_left:
    st.subheader("🧠 Ultime Decisioni dello Strategy Agent")

    if latest_decision:
        action = latest_decision.get("action", "HOLD")
        badge_class = {
            "BUY": "badge-buy",
            "SELL": "badge-sell",
            "HOLD": "badge-hold",
        }.get(action, "badge-hold")

        st.markdown(
            f'<div class="decision-card">'
            f'<p><strong>Timestamp:</strong> {latest_decision.get("timestamp", "N/A")}</p>'
            f'<p><strong>Asset:</strong> {latest_decision.get("symbol", "N/A")} · '
            f'<span class="{badge_class}">{action}</span></p>'
            f'<p><strong>Motivazione:</strong> {latest_decision.get("reasoning", "N/A")}</p>'
            f'<p><strong>SL:</strong> {latest_decision.get("stop_loss", "N/A")} · '
            f'<strong>TP:</strong> {latest_decision.get("take_profit", "N/A")}</p>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Mostra il JSON grezzo in un expander
        with st.expander("📄 Risposta LLM Raw"):
            st.code(latest_decision.get("raw_llm_response", "N/A"), language="json")
    else:
        st.info("Nessuna decisione registrata ancora.")

    # Storico decisioni recenti (tabella)
    st.markdown("##### 📋 Storico Decisioni Recenti")
    decisions_rows = db.conn.execute(
        "SELECT timestamp, symbol, action, reasoning, stop_loss, take_profit "
        "FROM decisions ORDER BY timestamp DESC LIMIT 10"
    ).fetchall()

    if decisions_rows:
        df_dec = pd.DataFrame(
            [dict(r) for r in decisions_rows],
            columns=["timestamp", "symbol", "action", "reasoning", "stop_loss", "take_profit"],
        )
        df_dec.columns = ["Timestamp", "Asset", "Azione", "Motivazione", "SL", "TP"]
        st.dataframe(df_dec, width="stretch", hide_index=True)
    else:
        st.info("Nessuna decisione nello storico.")


with col_right:
    st.subheader("📡 Ultimo Sentiment")

    if latest_sentiment:
        score = latest_sentiment.get("score", 5)
        # Colore basato sullo score
        if score >= 7:
            color, emoji = "#64ffda", "🟢"
        elif score >= 4:
            color, emoji = "#ffd93d", "🟡"
        else:
            color, emoji = "#ff6b6b", "🔴"

        st.markdown(
            f'<div class="sentiment-card">'
            f'<h2 style="color: {color}; text-align: center;">'
            f'{emoji} {score}/10</h2>'
            f'<p style="text-align: center; color: #a8b2d1;">'
            f'{latest_sentiment.get("summary", "N/A")}</p>'
            f'<p style="text-align: center; font-size: 0.8em; color: #495670;">'
            f'Fonte: {latest_sentiment.get("source", "N/A")} · '
            f'{latest_sentiment.get("timestamp", "N/A")}</p>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.info("Nessun dato di sentiment disponibile.")

    # Storico sentiment recente
    st.markdown("##### 📊 Storico Sentiment")
    sentiment_rows = db.conn.execute(
        "SELECT timestamp, score, summary FROM sentiment_logs "
        "ORDER BY timestamp DESC LIMIT 10"
    ).fetchall()

    if sentiment_rows:
        df_sent = pd.DataFrame(
            [dict(r) for r in sentiment_rows],
            columns=["timestamp", "score", "summary"],
        )
        df_sent.columns = ["Timestamp", "Score", "Sintesi"]
        st.dataframe(df_sent, width="stretch", hide_index=True)
    else:
        st.info("Nessun dato nello storico sentiment.")


st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# Posizioni Aperte
# ═════════════════════════════════════════════════════════════════════════════

st.subheader("📌 Posizioni Aperte")

if open_orders:
    df_open = pd.DataFrame(open_orders)
    cols_display = ["id", "timestamp", "symbol", "side", "amount",
                    "entry_price", "stop_loss", "take_profit"]
    cols_present = [c for c in cols_display if c in df_open.columns]
    df_open = df_open[cols_present]
    df_open.columns = [c.replace("_", " ").title() for c in cols_present]
    st.dataframe(df_open, width="stretch", hide_index=True)
else:
    st.info("Nessuna posizione aperta al momento.")

# ═════════════════════════════════════════════════════════════════════════════
# Storico Ordini
# ═════════════════════════════════════════════════════════════════════════════

st.subheader("📜 Storico Ordini")

if order_history:
    df_history = pd.DataFrame(order_history)
    cols_display = ["id", "timestamp", "symbol", "side", "amount",
                    "entry_price", "stop_loss", "take_profit", "status", "pnl"]
    cols_present = [c for c in cols_display if c in df_history.columns]
    df_history = df_history[cols_present]
    df_history.columns = [c.replace("_", " ").title() for c in cols_present]
    st.dataframe(df_history, width="stretch", hide_index=True)
else:
    st.info("Nessun ordine nello storico.")

# ═════════════════════════════════════════════════════════════════════════════
# Footer + Auto-Refresh
# ═════════════════════════════════════════════════════════════════════════════

st.divider()
st.markdown(
    f'<p style="text-align: center; color: #495670; font-size: 0.85em;">'
    f'🤖 Crypto Trading Bot – Paper Trading · '
    f'Ultimo aggiornamento: {datetime.now().strftime("%H:%M:%S")}'
    f'</p>',
    unsafe_allow_html=True,
)

# Auto-refresh ogni 60 secondi
st.markdown(
    '<meta http-equiv="refresh" content="60">',
    unsafe_allow_html=True,
)
