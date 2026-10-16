"""
GainzAlgo V2 Alpha - XAUUSD Telegram Signal Bot
Sends BUY/SELL signals for Gold directly to your Telegram
"""

import os
import time
import logging
from datetime import datetime
import pandas as pd
import pandas_ta as ta
import yfinance as yf
import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger(__name__)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")   # your bot token
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") # your chat id

SYMBOL      = "XAUUSD=X"   # Yahoo Finance Gold symbol
INTERVAL    = "15m"        # M15 candle
CHECK_EVERY = 60 * 15      # check every 15 minutes (seconds)

# GainzAlgo V2 Alpha settings
FAST_EMA    = 8
SLOW_EMA    = 21
TREND_EMA   = 200
RSI_PERIOD  = 14
RSI_OB      = 70
RSI_OS      = 30
ATR_PERIOD  = 14
ATR_MULT    = 1.5
RR_RATIO    = 2.0          # Take Profit = SL x 2

last_signal = {"type": None, "time": None}  # avoid duplicate alerts
# ──────────────────────────────────────────────────────────────────────────────


def send_telegram(message: str):
    """Send message to Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            log.error("Telegram error: %s", r.text)
    except Exception as e:
        log.error("Telegram send failed: %s", e)


def get_data() -> pd.DataFrame:
    """Fetch XAUUSD OHLCV data from Yahoo Finance."""
    df = yf.download(SYMBOL, period="5d", interval=INTERVAL, progress=False, auto_adjust=True)
    if df.empty or len(df) < TREND_EMA + 5:
        raise ValueError("Not enough data from Yahoo Finance")
    df.columns = [c.lower() for c in df.columns]
    return df


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA, RSI, MACD, ATR indicators."""
    df["ema_fast"]  = ta.ema(df["close"], length=FAST_EMA)
    df["ema_slow"]  = ta.ema(df["close"], length=SLOW_EMA)
    df["ema_trend"] = ta.ema(df["close"], length=TREND_EMA)
    df["rsi"]       = ta.rsi(df["close"], length=RSI_PERIOD)
    df["atr"]       = ta.atr(df["high"], df["low"], df["close"], length=ATR_PERIOD)

    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    df["macd"]        = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]

    return df.dropna()


def check_signals(df: pd.DataFrame):
    """
    Evaluate the 4 GainzAlgo conditions on the last closed candle.
    Returns ('BUY'|'SELL'|None, reason, sl, tp)
    """
    c  = df.iloc[-1]   # last closed candle
    p  = df.iloc[-2]   # previous candle

    price     = float(c["close"])
    atr_val   = float(c["atr"])
    sl_dist   = atr_val * ATR_MULT
    tp_dist   = sl_dist * RR_RATIO

    # ── BUY 1: EMA cross + RSI crosses above 50 + above EMA200 ──────────────
    buy1 = (
        price > float(c["ema_trend"]) and
        float(c["ema_fast"]) > float(c["ema_slow"]) and
        float(p["ema_fast"]) <= float(p["ema_slow"]) and
        float(c["rsi"]) > 50 and float(p["rsi"]) <= 50
    )

    # ── BUY 2: MACD bullish cross + strong candle + above EMA200 ────────────
    strong_bull = (float(c["close"]) - float(c["open"])) > atr_val * 0.5
    macd_bull   = (
        float(c["macd"]) > float(c["macd_signal"]) and
        float(p["macd"]) <= float(p["macd_signal"])
    )
    buy2 = (
        price > float(c["ema_trend"]) and
        macd_bull and
        strong_bull and
        float(c["rsi"]) < RSI_OB
    )

    # ── SELL 1: EMA cross + RSI crosses below 50 + below EMA200 ─────────────
    sell1 = (
        price < float(c["ema_trend"]) and
        float(c["ema_fast"]) < float(c["ema_slow"]) and
        float(p["ema_fast"]) >= float(p["ema_slow"]) and
        float(c["rsi"]) < 50 and float(p["rsi"]) >= 50
    )

    # ── SELL 2: MACD bearish cross + strong candle + below EMA200 ───────────
    strong_bear = (float(c["open"]) - float(c["close"])) > atr_val * 0.5
    macd_bear   = (
        float(c["macd"]) < float(c["macd_signal"]) and
        float(p["macd"]) >= float(p["macd_signal"])
    )
    sell2 = (
        price < float(c["ema_trend"]) and
        macd_bear and
        strong_bear and
        float(c["rsi"]) > RSI_OS
    )

    # ── Determine signal ─────────────────────────────────────────────────────
    if buy1:
        return "BUY",  "EMA Cross + RSI > 50",        price - sl_dist, price + tp_dist, price
    if buy2:
        return "BUY",  "MACD Cross + Strong Candle",  price - sl_dist, price + tp_dist, price
    if sell1:
        return "SELL", "EMA Cross + RSI < 50",        price + sl_dist, price - tp_dist, price
    if sell2:
        return "SELL", "MACD Cross + Strong Candle",  price + sl_dist, price - tp_dist, price

    return None, None, None, None, price


def format_message(direction: str, reason: str, sl: float, tp: float, entry: float) -> str:
    emoji = "🟢" if direction == "BUY" else "🔴"
    arrow = "📈" if direction == "BUY" else "📉"
    now   = datetime.utcnow().strftime("%H:%M UTC")

    return (
        f"{emoji} <b>XAUUSD {direction} SIGNAL</b> {arrow}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⏰ Time   : {now}\n"
        f"💡 Reason : {reason}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💰 Entry  : <b>{entry:.2f}</b>\n"
        f"🛑 SL     : <b>{sl:.2f}</b>\n"
        f"🎯 TP     : <b>{tp:.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚖️ Risk    : ATR x1.5 | RR 1:2\n"
        f"⚠️ Always confirm before entering!"
    )


def run():
    log.info("GainzAlgo V2 Alpha Bot started — watching XAUUSD %s", INTERVAL)
    send_telegram("🤖 <b>GainzAlgo Bot Started</b>\nWatching XAUUSD for signals...")

    while True:
        try:
            df   = get_data()
            df   = calculate_indicators(df)
            direction, reason, sl, tp, price = check_signals(df)

            if direction:
                # avoid sending same signal twice in a row
                sig_id = f"{direction}-{df.index[-1]}"
                if last_signal["time"] != sig_id:
                    last_signal["type"] = direction
                    last_signal["time"] = sig_id
                    msg = format_message(direction, reason, sl, tp, price)
                    send_telegram(msg)
                    log.info("Signal sent: %s %s", direction, reason)
            else:
                log.info("No signal | Price: %.2f | RSI: %.1f", price, float(df.iloc[-1]["rsi"]))

        except Exception as e:
            log.error("Error: %s", e)

        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    run()
