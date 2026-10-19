"""
GainzAlgo V2 Alpha - XAUUSD Telegram Bot
Session-aware: London (08:00-16:00 UTC) + New York (13:00-21:00 UTC) only.
Signals require 2+ confluent conditions to fire.
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
TWELVE_API_KEY   = os.getenv("TWELVE_API_KEY")

CHECK_EVERY = 60 * 15   # 15 minutes

# Indicator periods
FAST_EMA   = 8
SLOW_EMA   = 21
TREND_EMA  = 200
RSI_PERIOD = 14
ATR_PERIOD = 14

# Signal filters
ATR_MULT       = 2.0    # SL = ATR × 2.0  (gold needs room)
RR_RATIO       = 2.5    # TP = SL × 2.5   (higher reward)
RSI_BUY_MIN    = 52     # RSI must be above this for buys  (not just 50)
RSI_SELL_MAX   = 48     # RSI must be below this for sells
BODY_ATR_RATIO = 0.6    # Candle body must be > 60% of ATR to count as "strong"

last_signal_id = None


# ── Session filter ────────────────────────────────────────────────────────────

def get_session() -> str:
    """Return active session label, or empty string if outside trading hours."""
    h = datetime.utcnow().hour
    london   = 8 <= h < 16
    newyork  = 13 <= h < 21
    if london and newyork:
        return "London/NY Overlap 🔥"
    if london:
        return "London 🇬🇧"
    if newyork:
        return "New York 🇺🇸"
    return ""   # Asian / off-hours


def is_trading_day() -> bool:
    return datetime.utcnow().weekday() < 5  # Mon–Fri


# ── Indicator math ────────────────────────────────────────────────────────────

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
    e_fast    = ema(prices, fast)
    e_slow    = ema(prices, slow)
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


# ── Data fetch ────────────────────────────────────────────────────────────────

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


# ── Signal logic ──────────────────────────────────────────────────────────────

def check_signals():
    opens, highs, lows, closes = fetch_data()

    ef_vals = ema(closes, FAST_EMA)
    es_vals = ema(closes, SLOW_EMA)
    et_vals = ema(closes, TREND_EMA)
    rs_vals = rsi(closes, RSI_PERIOD)
    mm, ms  = macd(closes)
    at_vals = atr(highs, lows, closes, ATR_PERIOD)

    n = min(len(ef_vals), len(es_vals), len(et_vals),
            len(rs_vals), len(mm), len(ms), len(at_vals),
            len(closes), len(opens))

    ef = ef_vals[-n:]
    es = es_vals[-n:]
    et = et_vals[-n:]
    rs = rs_vals[-n:]
    mm = mm[-n:]
    ms = ms[-n:]
    at = at_vals[-n:]
    cl = closes[-n:]
    op = opens[-n:]

    price   = cl[-1]
    atr_val = at[-1]

    # Skip if ATR is abnormally high (news/spike — 2× median ATR)
    atr_median = sorted(at[-20:])[10]
    if atr_val > atr_median * 2.2:
        log.info("ATR spike — skipping (atr=%.2f median=%.2f)", atr_val, atr_median)
        return None, None, None, None, price, atr_val, rs[-1], 0, ""

    sl_dist = atr_val * ATR_MULT
    tp_dist = sl_dist * RR_RATIO

    # Conditions — each is a boolean
    above_trend = price > et[-1]
    below_trend = price < et[-1]

    ema_bull_cross = ef[-1] > es[-1] and ef[-2] <= es[-2]
    ema_bear_cross = ef[-1] < es[-1] and ef[-2] >= es[-2]

    macd_bull = mm[-1] > ms[-1] and mm[-2] <= ms[-2]
    macd_bear = mm[-1] < ms[-1] and mm[-2] >= ms[-2]

    rsi_bull = rs[-1] > RSI_BUY_MIN
    rsi_bear = rs[-1] < RSI_SELL_MAX

    body = cl[-1] - op[-1]
    strong_bull_candle = body >  atr_val * BODY_ATR_RATIO
    strong_bear_candle = body < -atr_val * BODY_ATR_RATIO

    # Confluence scoring — count how many conditions align
    buy_score = sum([above_trend, ema_bull_cross, macd_bull, rsi_bull, strong_bull_candle])
    sel_score = sum([below_trend, ema_bear_cross, macd_bear, rsi_bear, strong_bear_candle])

    def reasons(ema_c, macd_c, rsi_c, candle_c):
        parts = []
        if ema_c:   parts.append("EMA Cross")
        if macd_c:  parts.append("MACD")
        if rsi_c:   parts.append("RSI")
        if candle_c: parts.append("Strong Candle")
        return " + ".join(parts)

    # Require trend alignment AND at least 2 other conditions
    if above_trend and buy_score >= 3:
        reason = reasons(ema_bull_cross, macd_bull, rsi_bull, strong_bull_candle)
        stars  = "⭐⭐⭐" if buy_score >= 4 else "⭐⭐"
        return "BUY",  reason, price - sl_dist, price + tp_dist, price, atr_val, rs[-1], buy_score, stars

    if below_trend and sel_score >= 3:
        reason = reasons(ema_bear_cross, macd_bear, rsi_bear, strong_bear_candle)
        stars  = "⭐⭐⭐" if sel_score >= 4 else "⭐⭐"
        return "SELL", reason, price + sl_dist, price - tp_dist, price, atr_val, rs[-1], sel_score, stars

    return None, None, None, None, price, atr_val, rs[-1], 0, ""


# ── Telegram ──────────────────────────────────────────────────────────────────

def send(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        log.error("Telegram error: %s", e)


def fmt(direction, reason, sl, tp, entry, atr_val, rsi_val, score, stars, session):
    emoji = "🟢" if direction == "BUY" else "🔴"
    arrow = "📈" if direction == "BUY" else "📉"
    now   = datetime.utcnow().strftime("%H:%M UTC")
    rr    = RR_RATIO
    return (
        f"{emoji} <b>XAUUSD {direction} SIGNAL</b> {arrow}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⏰ {now}  |  {session}\n"
        f"💡 {reason}\n"
        f"🏆 Strength: {stars} ({score}/5)\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💰 Entry : <b>{entry:.2f}</b>\n"
        f"🛑 SL    : <b>{sl:.2f}</b>  ({atr_val * ATR_MULT:.1f} pts)\n"
        f"🎯 TP    : <b>{tp:.2f}</b>  (1:{rr})\n"
        f"📊 RSI   : {rsi_val:.1f}  |  ATR: {atr_val:.2f}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚠️ Confirm before entering!"
    )


# ── Main loop ─────────────────────────────────────────────────────────────────

def run():
    global last_signal_id
    log.info("GainzAlgo Bot started (session-aware)")
    send(
        "🤖 <b>GainzAlgo Bot Started ✅</b>\n"
        "XAUUSD — London + NY sessions only\n"
        "Requires 3+ confluent conditions to signal."
    )

    while True:
        if not is_trading_day():
            log.info("Weekend — sleeping 1h")
            time.sleep(3600)
            continue

        session = get_session()
        if not session:
            log.info("Outside trading session — sleeping 15min")
            time.sleep(CHECK_EVERY)
            continue

        try:
            direction, reason, sl, tp, price, atr_val, rsi_val, score, stars = check_signals()
            if direction:
                sig_id = f"{direction}-{datetime.utcnow().strftime('%Y%m%d%H%M')}"
                if sig_id != last_signal_id:
                    last_signal_id = sig_id
                    send(fmt(direction, reason, sl, tp, price, atr_val, rsi_val, score, stars, session))
                    log.info("Signal: %s %s @ %.2f score=%d", direction, reason, price, score)
            else:
                log.info("No signal | %s | Price=%.2f RSI=%.1f ATR=%.2f",
                         session, price, rsi_val, atr_val)
        except Exception as e:
            log.error("Error: %s", e)

        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    run()
