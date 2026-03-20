"""
config.py – Configurazione centralizzata del Crypto Trading Bot.
Carica le variabili d'ambiente da .env e definisce le costanti globali.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ── Carica .env dalla root del progetto ──────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

# ── API Keys ─────────────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET: str = os.getenv("BINANCE_SECRET", "")
CRYPTOPANIC_API_KEY: str = os.getenv("CRYPTOPANIC_API_KEY", "")

# ── Database ─────────────────────────────────────────────────────────────────
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH: str = str(DATA_DIR / "trading_bot.db")

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ── Trading ──────────────────────────────────────────────────────────────────
TRADING_PAIRS: list[str] = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
CANDLE_TIMEFRAME: str = "15m"
CANDLE_LIMIT: int = 100                   # Numero di candele da scaricare per fetch

# ── Risk Management ─────────────────────────────────────────────────────────
RISK_PER_TRADE: float = 0.02              # Max 2% del portafoglio per operazione

# ── Scheduling ───────────────────────────────────────────────────────────────
DATA_FETCH_INTERVAL_MIN: int = 15         # Intervallo fetch dati + ciclo trading
SENTIMENT_FETCH_INTERVAL_MIN: int = 120   # Intervallo fetch sentiment (2 ore)

# ── LLM (Groq) ───────────────────────────────────────────────────────────────
GROQ_MODEL_FAST: str = os.getenv("GROQ_MODEL_FAST", "llama-3.1-8b-instant")      # SentimentAgent
GROQ_MODEL_STRONG: str = os.getenv("GROQ_MODEL_STRONG", "llama-3.3-70b-versatile")  # StrategyAgent
