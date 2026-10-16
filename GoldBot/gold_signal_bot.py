"""
GainzAlgo V2 Alpha - XAUUSD Telegram Bot
Zero heavy dependencies — only 'requests' (pre-installed on PythonAnywhere)
"""

import os, time, logging
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
CHECK_EVERY    = 60 * 15      # 15 minutes

# GainzAlgo settings
FAST_EMA   = 8
SLOW_EMA   = 21
TREND_EMA  = 200
RSI_PERIOD = 14
ATR_PERIOD = 14
ATR_MULT   = 1.5
RR_RATIO   = 2.0

last_signal_id = None


# ── Pure-Python indicator math (no pandas/numpy needed) ──────────────────────

def ema(prices, period):
    k = 2 / (period + 1)
    result = [prices[0]]
    for p in prices[1:]:
        result.append(p * k + result[-1] * (1 - k))
    return result


def rsi(prices, period=14):
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    if len(gains) < period:
        return [50.0]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    result = []
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        rs = avg_g / avg_l if avg_l != 0 else 100
        result.append(100 - 100 / (1 + rs))
    return result if result else [50.0]


def macd(prices, fast=12, slow=26, signal=9):
    e_fast   = ema(prices, fast)
    e_slow   = ema(prices, slow)
    macd_line = [f - s for f, s in zip(e_fast, e_slow)]
    sig_line  = ema(macd_line, signal)
    return macd_line, sig_line


def atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i]  - closes[i - 1]))
        trs.append(tr)
    if not trs:
        return [1.0]
    avg = sum(trs[:period]) / min(period, len(trs))
    result = [avg]
    for tr in trs[period:]:
        result.append((result[-1] * (period - 1) + tr) / period)
    return result


# ── Fetch OHLCV from Twelve Data (free API, reliable, supports XAU/USD) ──────

def fetch_data():
    url = (
        "https://api.twelvedata.com/time_series"
        f"?symbol=XAU/USD&interval=15min&outputsize=250"
        f"&apikey={TWELVE_API_KEY}"
    )
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    if "values" not in data:
        raise ValueError(f"API error: {data.get('message', data)}")

    values = list(reversed(data["values"]))   # oldest first
    if len(values) < TREND_EMA + 10:
        raise ValueError(f"Not enough data: {len(values)} candles")

    opens  = [float(v["open"])  for v in values]
    highs  = [float(v["high"])  for v in values]
    lows   = [float(v["low"])   for v in values]
    closes = [float(v["close"]) for v in values]
    return opens, highs, lows, closes


# ── Signal logic ─────────────────────────────────────────────────────────────

def check_signals():
    opens, highs, lows, closes = fetch_data()

    ema_fast_vals  = ema(closes, FAST_EMA)
    ema_slow_vals  = ema(closes, SLOW_EMA)
    ema_trend_vals = ema(closes, TREND_EMA)
    rsi_vals       = rsi(closes, RSI_PERIOD)
    macd_main, macd_sig = macd(closes)
    atr_vals       = atr(highs, lows, closes, ATR_PERIOD)

    # align lengths — use last N common elements
    n = min(len(ema_fast_vals), len(ema_slow_vals), len(ema_trend_vals),
            len(rsi_vals), len(macd_main), len(macd_sig), len(atr_vals),
            len(closes), len(opens))

    ef  = ema_fast_vals[-n:]
    es  = ema_slow_vals[-n:]
    et  = ema_trend_vals[-n:]
    rs  = rsi_vals[-n:]
    mm  = macd_main[-n:]
    ms  = macd_sig[-n:]
    at  = atr_vals[-n:]
    cl  = closes[-n:]
    op  = opens[-n:]

    # indices: -1 = last closed candle, -2 = previous
    price   = cl[-1]
    atr_val = at[-1]
    sl_dist = atr_val * ATR_MULT
    tp_dist = sl_dist * RR_RATIO

    # Candle body strength
    body_now  = cl[-1] - op[-1]
    body_prev = cl[-2] - op[-2]
    strong_bull = body_now  >  atr_val * 0.5
    strong_bear = body_now  < -atr_val * 0.5

    # BUY 1: trend up + EMA cross + RSI crosses above 50
    buy1 = (price > et[-1] and
            ef[-1] > es[-1] and ef[-2] <= es[-2] and
            rs[-1] > 50 and rs[-2] <= 50)

    # BUY 2: trend up + MACD bullish cross + strong bull candle
    macd_bull = mm[-1] > ms[-1] and mm[-2] <= ms[-2]
    buy2 = (price > et[-1] and macd_bull and strong_bull and rs[-1] < 70)

    # SELL 1: trend down + EMA cross + RSI crosses below 50
    sell1 = (price < et[-1] and
             ef[-1] < es[-1] and ef[-2] >= es[-2] and
             rs[-1] < 50 and rs[-2] >= 50)

    # SELL 2: trend down + MACD bearish cross + strong bear candle
    macd_bear = mm[-1] < ms[-1] and mm[-2] >= ms[-2]
    sell2 = (price < et[-1] and macd_bear and strong_bear and rs[-1] > 30)

    if buy1:
        return "BUY",  "EMA Cross + RSI > 50",       price - sl_dist, price + tp_dist, price, atr_val, rs[-1]
    if buy2:
        return "BUY",  "MACD Cross + Strong Candle",  price - sl_dist, price + tp_dist, price, atr_val, rs[-1]
    if sell1:
        return "SELL", "EMA Cross + RSI < 50",        price + sl_dist, price - tp_dist, price, atr_val, rs[-1]
    if sell2:
        return "SELL", "MACD Cross + Strong Candle",  price + sl_dist, price - tp_dist, price, atr_val, rs[-1]

    return None, None, None, None, price, atr_val, rs[-1]


# ── Telegram ─────────────────────────────────────────────────────────────────

def send(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID,
                                 "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        log.error("Telegram error: %s", e)


def fmt(direction, reason, sl, tp, entry, atr_val, rsi_val):
    emoji = "🟢" if direction == "BUY" else "🔴"
    arrow = "📈" if direction == "BUY" else "📉"
    now   = datetime.utcnow().strftime("%H:%M UTC")
    return (
        f"{emoji} <b>XAUUSD {direction} SIGNAL</b> {arrow}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⏰ {now}\n"
        f"💡 {reason}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💰 Entry : <b>{entry:.2f}</b>\n"
        f"🛑 SL    : <b>{sl:.2f}</b>\n"
        f"🎯 TP    : <b>{tp:.2f}</b>\n"
        f"📊 RSI   : {rsi_val:.1f} | ATR: {atr_val:.2f}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚠️ Confirm before entering!"
    )


# ── Main loop ─────────────────────────────────────────────────────────────────

def run():
    global last_signal_id
    log.info("GainzAlgo Bot started")
    send("🤖 <b>GainzAlgo Bot Started ✅</b>\nWatching XAUUSD for signals...")

    while True:
        try:
            direction, reason, sl, tp, price, atr_val, rsi_val = check_signals()
            if direction:
                sig_id = f"{direction}-{datetime.utcnow().strftime('%Y%m%d%H%M')}"
                if sig_id != last_signal_id:
                    last_signal_id = sig_id
                    send(fmt(direction, reason, sl, tp, price, atr_val, rsi_val))
                    log.info("Signal sent: %s %s @ %.2f", direction, reason, price)
            else:
                log.info("No signal | Price: %.2f | RSI: %.1f", price, rsi_val)
        except Exception as e:
            log.error("Error: %s", e)

        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    run()
