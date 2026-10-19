"""
GainzAlgo V2 Alpha - XAUUSD Telegram Bot
- Session filter: London + NY only (08:00–21:00 UTC = 11:00–00:00 Lebanon)
- Dual-timeframe: 1h trend must agree with 15m signal
- Three alert levels: ⚡ Setup (1 cond), 📡 Signal (2 cond), 🚀 Strong (3+ cond)
- Daily brief at London open (08:00 UTC = 11:00 Lebanon)
"""

import os, time, logging
from datetime import datetime, date
import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TWELVE_API_KEY   = os.getenv("TWELVE_API_KEY")

CHECK_EVERY = 60 * 15   # 15 minutes

# Indicator settings
FAST_EMA   = 8
SLOW_EMA   = 21
TREND_EMA  = 200
RSI_PERIOD = 14
ATR_PERIOD = 14
ATR_MULT   = 2.0    # SL distance
RR_RATIO   = 2.5    # TP = SL × 2.5

# Thresholds
RSI_BUY_MIN    = 52
RSI_SELL_MAX   = 48
BODY_ATR_RATIO = 0.5

# Alert levels (how many 15m conditions needed)
SETUP_LEVEL  = 1   # ⚡ Setup forming — prepare
SIGNAL_LEVEL = 2   # 📡 Signal — can enter
STRONG_LEVEL = 3   # 🚀 Strong signal — high confidence

last_signal_key = {}   # tracks last alert level per direction to avoid spam
last_brief_date = None


# ── Session ───────────────────────────────────────────────────────────────────

def get_session() -> str:
    h = datetime.utcnow().hour
    london  = 8 <= h < 16
    newyork = 13 <= h < 21
    if london and newyork:
        return "London/NY Overlap 🔥"
    if london:
        return "London 🇬🇧"
    if newyork:
        return "New York 🇺🇸"
    return ""

def is_trading_day() -> bool:
    return datetime.utcnow().weekday() < 5

def is_london_open() -> bool:
    now = datetime.utcnow()
    return now.hour == 8 and now.minute < 15


# ── Math ──────────────────────────────────────────────────────────────────────

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
    ef = ema(prices, fast)
    es = ema(prices, slow)
    ml = [f - s for f, s in zip(ef, es)]
    return ml, ema(ml, signal)

def atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i]  - closes[i-1]))
        trs.append(tr)
    if not trs:
        return [1.0]
    avg = sum(trs[:period]) / min(period, len(trs))
    result = [avg]
    for tr in trs[period:]:
        result.append((result[-1] * (period - 1) + tr) / period)
    return result


# ── Data fetch ────────────────────────────────────────────────────────────────

def _fetch(interval: str, size: int = 250):
    url = (
        "https://api.twelvedata.com/time_series"
        f"?symbol=XAU/USD&interval={interval}&outputsize={size}"
        f"&apikey={TWELVE_API_KEY}"
    )
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    if "values" not in data:
        raise ValueError(f"API error: {data.get('message', data)}")
    values = list(reversed(data["values"]))
    opens  = [float(v["open"])  for v in values]
    highs  = [float(v["high"])  for v in values]
    lows   = [float(v["low"])   for v in values]
    closes = [float(v["close"]) for v in values]
    return opens, highs, lows, closes

def fetch_data():
    return _fetch("15min", 250)

def fetch_htf():
    """1-hour data for trend confirmation."""
    return _fetch("1h", 220)


# ── 1h trend ─────────────────────────────────────────────────────────────────

def get_htf_trend() -> str:
    """Return 'UP', 'DOWN', or 'NEUTRAL' based on 1h EMA200 + RSI."""
    try:
        _, _, _, closes_1h = fetch_htf()
        if len(closes_1h) < 205:
            return "NEUTRAL"
        et = ema(closes_1h, 200)
        rs = rsi(closes_1h, 14)
        price = closes_1h[-1]
        rsi_v = rs[-1]
        if price > et[-1] and rsi_v > 50:
            return "UP"
        if price < et[-1] and rsi_v < 50:
            return "DOWN"
    except Exception as e:
        log.warning("HTF fetch failed: %s", e)
    return "NEUTRAL"


# ── Signal scoring ────────────────────────────────────────────────────────────

def score_signals():
    opens, highs, lows, closes = fetch_data()

    ef_v = ema(closes, FAST_EMA)
    es_v = ema(closes, SLOW_EMA)
    et_v = ema(closes, TREND_EMA)
    rs_v = rsi(closes, RSI_PERIOD)
    mm, ms = macd(closes)
    at_v = atr(highs, lows, closes, ATR_PERIOD)

    n = min(len(ef_v), len(es_v), len(et_v), len(rs_v),
            len(mm), len(ms), len(at_v), len(closes), len(opens))

    ef = ef_v[-n:]; es = es_v[-n:]; et = et_v[-n:]
    rs = rs_v[-n:]; mm = mm[-n:]; ms = ms[-n:]
    at = at_v[-n:]; cl = closes[-n:]; op = opens[-n:]

    price   = cl[-1]
    atr_val = at[-1]

    # ATR spike filter
    atr_med = sorted(at[-20:])[10]
    if atr_val > atr_med * 2.2:
        return None, price, atr_val, rs[-1]

    sl = atr_val * ATR_MULT
    tp = sl * RR_RATIO

    body = cl[-1] - op[-1]

    conds_buy = {
        "EMA Cross":      ef[-1] > es[-1] and ef[-2] <= es[-2],
        "MACD Bull":      mm[-1] > ms[-1] and mm[-2] <= ms[-2],
        "RSI > 52":       rs[-1] > RSI_BUY_MIN,
        "Strong Candle":  body > atr_val * BODY_ATR_RATIO,
        "Above EMA200":   price > et[-1],
    }
    conds_sel = {
        "EMA Cross":      ef[-1] < es[-1] and ef[-2] >= es[-2],
        "MACD Bear":      mm[-1] < ms[-1] and mm[-2] >= ms[-2],
        "RSI < 48":       rs[-1] < RSI_SELL_MAX,
        "Strong Candle":  body < -atr_val * BODY_ATR_RATIO,
        "Below EMA200":   price < et[-1],
    }

    buy_hits = {k for k, v in conds_buy.items() if v}
    sel_hits = {k for k, v in conds_sel.items() if v}

    results = []
    for direction, hits, sl_price, tp_price in [
        ("BUY",  buy_hits, price - sl, price + tp),
        ("SELL", sel_hits, price + sl, price - tp),
    ]:
        score = len(hits)
        if score >= SETUP_LEVEL:
            reason = " + ".join(list(hits)[:3])
            results.append({
                "direction": direction,
                "score": score,
                "reason": reason,
                "sl": sl_price,
                "tp": tp_price,
                "entry": price,
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    best = results[0] if results else None
    return best, price, atr_val, rs[-1]


# ── Telegram ──────────────────────────────────────────────────────────────────

def send(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        log.error("Telegram: %s", e)


def fmt_signal(sig: dict, atr_val: float, rsi_val: float, session: str, htf: str) -> str:
    score = sig["score"]
    direction = sig["direction"]

    if score >= STRONG_LEVEL:
        header = f"🚀 <b>STRONG {direction} — XAUUSD</b>"
        stars  = "⭐⭐⭐"
    elif score >= SIGNAL_LEVEL:
        header = f"📡 <b>{direction} SIGNAL — XAUUSD</b>"
        stars  = "⭐⭐"
    else:
        header = f"⚡ <b>{direction} SETUP FORMING — XAUUSD</b>"
        stars  = "⭐  (wait for confirmation)"

    emoji = "🟢" if direction == "BUY" else "🔴"
    now   = datetime.utcnow().strftime("%H:%M UTC")
    htf_icon = "📈" if htf == "UP" else "📉" if htf == "DOWN" else "➡️"

    lines = [
        f"{emoji} {header}",
        f"━━━━━━━━━━━━━━━━",
        f"⏰ {now}  |  {session}",
        f"💡 {sig['reason']}",
        f"🏆 {stars}  |  1H Trend: {htf_icon} {htf}",
        f"━━━━━━━━━━━━━━━━",
    ]

    if score >= SIGNAL_LEVEL:
        lines += [
            f"💰 Entry : <b>{sig['entry']:.2f}</b>",
            f"🛑 SL    : <b>{sig['sl']:.2f}</b>  ({atr_val * ATR_MULT:.1f} pts)",
            f"🎯 TP    : <b>{sig['tp']:.2f}</b>  (1:{RR_RATIO})",
            f"📊 RSI   : {rsi_val:.1f}  |  ATR: {atr_val:.2f}",
        ]
    else:
        lines += [
            f"📍 Price  : {sig['entry']:.2f}",
            f"📊 RSI    : {rsi_val:.1f}  |  ATR: {atr_val:.2f}",
            f"👀 Watch for 1 more condition before entering",
        ]

    lines += [
        "━━━━━━━━━━━━━━━━",
        "⚠️ Confirm before entering!" if score >= SIGNAL_LEVEL else "⚡ Not a trade yet — setup only",
    ]
    return "\n".join(lines)


def fmt_brief(price: float, rsi_val: float, atr_val: float, htf: str) -> str:
    htf_icon = "📈 Bullish" if htf == "UP" else "📉 Bearish" if htf == "DOWN" else "➡️ Neutral"
    return (
        f"🌅 <b>LONDON SESSION OPEN</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"XAUUSD: <b>{price:.2f}</b>\n"
        f"1H Trend: {htf_icon}\n"
        f"RSI: {rsi_val:.1f}  |  ATR: {atr_val:.2f}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Active hours: 11:00–00:00 (Lebanon 🇱🇧)"
    )


# ── Main loop ─────────────────────────────────────────────────────────────────

def run():
    global last_signal_key, last_brief_date
    log.info("GainzAlgo Bot started (dual-timeframe)")
    send(
        "🤖 <b>GainzAlgo Bot Started ✅</b>\n"
        "XAUUSD — London + NY sessions only\n"
        "⚡ Setup alerts + 📡 Signals + 🚀 Strong signals\n"
        "Active: 11:00–00:00 Lebanon time 🇱🇧"
    )

    while True:
        if not is_trading_day():
            time.sleep(3600)
            continue

        session = get_session()
        today = date.today()

        # Daily brief at London open
        if is_london_open() and last_brief_date != today:
            last_brief_date = today
            try:
                sig, price, atr_val, rsi_val = score_signals()
                htf = get_htf_trend()
                send(fmt_brief(price, rsi_val, atr_val, htf))
            except Exception as e:
                log.error("Brief error: %s", e)

        if not session:
            time.sleep(CHECK_EVERY)
            continue

        try:
            htf = get_htf_trend()
            sig, price, atr_val, rsi_val = score_signals()

            if sig is None:
                log.info("ATR spike — skipped | Price=%.2f", price)
                time.sleep(CHECK_EVERY)
                continue

            direction = sig["direction"]
            score     = sig["score"]

            # Require 1h trend to agree for full signals
            htf_agrees = (direction == "BUY"  and htf in ("UP", "NEUTRAL")) or \
                         (direction == "SELL" and htf in ("DOWN", "NEUTRAL"))

            # For setups (score=1) allow regardless of HTF
            # For signals (score≥2) require HTF agreement
            if score >= SIGNAL_LEVEL and not htf_agrees:
                log.info("HTF disagrees (%s vs %s) — signal suppressed", direction, htf)
                time.sleep(CHECK_EVERY)
                continue

            # Avoid re-alerting same level in same 15-min candle
            sig_key = f"{direction}-{score}-{datetime.utcnow().strftime('%Y%m%d%H%M')}"
            prev    = last_signal_key.get(direction, "")
            same_lvl_recently = prev.startswith(f"{direction}-{score}-{datetime.utcnow().strftime('%Y%m%d%H')}")

            if not same_lvl_recently:
                last_signal_key[direction] = sig_key
                send(fmt_signal(sig, atr_val, rsi_val, session, htf))
                log.info("Alert: %s score=%d HTF=%s price=%.2f", direction, score, htf, price)
            else:
                log.info("No new alert | %s | score=%d price=%.2f RSI=%.1f",
                         session, score, price, rsi_val)

        except Exception as e:
            log.error("Error: %s", e)

        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    run()
