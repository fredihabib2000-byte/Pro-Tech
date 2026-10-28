"""
GainzAlgo V3 - XAUUSD Telegram Bot
- Session filter: London + NY only (08:00–21:00 UTC = 11:00–00:00 Lebanon)
- Triple-timeframe: 4H macro + 1H trend + 15min entry
- Volume Analysis: OBV trend + Volume Surge detection
- State-based conditions → regular signals in trending markets
- Three levels: ⚡ Setup (2 cond), 📡 Signal (3 cond), 🚀 Strong (4+ cond)
- Daily brief at London open (11:00 Lebanon)
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

CHECK_EVERY = 60 * 15

ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY")   # optional — improves accuracy

FAST_EMA   = 8
SLOW_EMA   = 21
TREND_EMA  = 200
RSI_PERIOD = 14
ATR_PERIOD = 14
ATR_MULT   = 1.8     # tighter stop → better RR
RR_RATIO   = 3.0     # 3:1 RR → profitable even at 35% win rate
ATR_MIN    = 2.5     # skip dead/choppy markets below this ATR

SETUP_LEVEL  = 2
SIGNAL_LEVEL = 3
STRONG_LEVEL = 4

PIVOT_ZONE  = 0.35   # within X × ATR of a pivot level = "at key level"

# Minimum score to actually send an alert (Setup=2 is detected but NOT sent)
ALERT_MIN_SCORE = 3

# Spam guard: don't re-alert same direction+reason within N minutes
SPAM_MINUTES = 90

# Ranging filter: EMA spread / ATR must exceed this to confirm a trend
EMA_TREND_MIN = 0.25

last_alert = {}   # direction → (reason_key, timestamp)
last_brief_date = None


# ── Session ───────────────────────────────────────────────────────────────────

def get_session() -> str:
    h = datetime.utcnow().hour
    london  = 8 <= h < 16
    newyork = 13 <= h < 21
    if london and newyork: return "London/NY Overlap 🔥"
    if london:             return "London 🇬🇧"
    if newyork:            return "New York 🇺🇸"
    return ""

def is_trading_day() -> bool:
    return datetime.utcnow().weekday() < 5

def is_london_open() -> bool:
    now = datetime.utcnow()
    return now.hour == 8 and now.minute < 15


# ── Math ──────────────────────────────────────────────────────────────────────

def ema(prices, period):
    k = 2 / (period + 1)
    r = [prices[0]]
    for p in prices[1:]:
        r.append(p * k + r[-1] * (1 - k))
    return r

def rsi(prices, period=14):
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    if len(gains) < period: return [50.0]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    result = []
    for i in range(period, len(gains)):
        ag = (ag * (period-1) + gains[i]) / period
        al = (al * (period-1) + losses[i]) / period
        rs = ag / al if al else 100
        result.append(100 - 100 / (1 + rs))
    return result or [50.0]

def macd(prices, fast=12, slow=26, signal=9):
    ef = ema(prices, fast); es = ema(prices, slow)
    ml = [f - s for f, s in zip(ef, es)]
    return ml, ema(ml, signal)

def atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i]-lows[i],
                       abs(highs[i]-closes[i-1]),
                       abs(lows[i]-closes[i-1])))
    if not trs: return [1.0]
    avg = sum(trs[:period]) / min(period, len(trs))
    r = [avg]
    for tr in trs[period:]:
        r.append((r[-1]*(period-1) + tr) / period)
    return r

def bollinger(prices, period=20, std_mult=2.0):
    """Returns (upper, middle, lower) as lists aligned to prices."""
    if len(prices) < period:
        p = prices[-1]
        return [p]*len(prices), [p]*len(prices), [p]*len(prices)
    upper, middle, lower = [], [], []
    for i in range(len(prices)):
        if i < period - 1:
            upper.append(prices[i]); middle.append(prices[i]); lower.append(prices[i])
            continue
        window = prices[i-period+1:i+1]
        m = sum(window) / period
        sd = (sum((x - m) ** 2 for x in window) / period) ** 0.5
        upper.append(m + std_mult * sd)
        middle.append(m)
        lower.append(m - std_mult * sd)
    return upper, middle, lower

def stoch_rsi(prices, rsi_period=14, stoch_period=14, smooth_k=3, smooth_d=3):
    """Returns (K, D) smoothed stochastic RSI, values 0–100."""
    rs = rsi(prices, rsi_period)
    if len(rs) < stoch_period:
        return [50.0], [50.0]
    raw_k = []
    for i in range(stoch_period - 1, len(rs)):
        window = rs[i-stoch_period+1:i+1]
        lo, hi = min(window), max(window)
        raw_k.append(50.0 if hi == lo else (rs[i] - lo) / (hi - lo) * 100)
    def sma(arr, n):
        if len(arr) < n: return arr
        return [sum(arr[i:i+n])/n for i in range(len(arr)-n+1)]
    k = sma(raw_k, smooth_k) or raw_k
    d = sma(k, smooth_d) or k
    return k, d

def obv(closes, volumes):
    """On Balance Volume — rising OBV = institutional accumulation."""
    if not volumes or len(closes) < 2:
        return [0.0]
    result = [0.0]
    for i in range(1, len(closes)):
        v = volumes[i] if i < len(volumes) else 0
        if closes[i] > closes[i-1]:
            result.append(result[-1] + v)
        elif closes[i] < closes[i-1]:
            result.append(result[-1] - v)
        else:
            result.append(result[-1])
    return result

def vol_surge(volumes, period=20, mult=1.5):
    """True if latest volume is mult × average of last period bars."""
    if not volumes or len(volumes) < period + 1:
        return False
    avg = sum(volumes[-period-1:-1]) / period
    return avg > 0 and volumes[-1] > avg * mult

def pivot_levels(highs, lows, closes):
    """
    Classic daily pivot points from yesterday's candles.
    Uses last 96 15min bars (~24h) as 'yesterday'.
    Returns dict with PP, R1, R2, S1, S2.
    """
    n = len(closes)
    if n < 200:
        return {}
    # Yesterday = candles from -192 to -96 (the 24h before the last 96 bars)
    start, end = max(0, n - 192), n - 96
    ph = max(highs[start:end])
    pl = min(lows[start:end])
    pc = closes[end - 1]
    pp = (ph + pl + pc) / 3
    return {
        "PP": pp,
        "R1": 2 * pp - pl,
        "R2": pp + (ph - pl),
        "S1": 2 * pp - ph,
        "S2": pp - (ph - pl),
    }


# ── Data ──────────────────────────────────────────────────────────────────────

def _fetch(interval: str, size: int = 250):
    url = (f"https://api.twelvedata.com/time_series"
           f"?symbol=XAU/USD&interval={interval}&outputsize={size}"
           f"&apikey={TWELVE_API_KEY}")
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    if "values" not in data:
        raise ValueError(f"API error: {data.get('message', data)}")
    vals = list(reversed(data["values"]))
    return ([float(v["open"])              for v in vals],
            [float(v["high"])              for v in vals],
            [float(v["low"])               for v in vals],
            [float(v["close"])             for v in vals],
            [float(v.get("volume") or 0)   for v in vals])

def fetch_data():  return _fetch("15min", 250)
def fetch_htf():   return _fetch("1h",    220)
def fetch_4h():    return _fetch("4h",    120)


# ── Multi-timeframe trend ─────────────────────────────────────────────────────

def get_htf_trend() -> tuple:
    """Returns (trend_1h, trend_4h) — each is 'UP', 'DOWN', or 'NEUTRAL'."""
    trend_1h = "NEUTRAL"
    trend_4h = "NEUTRAL"
    try:
        _, _, _, c, _ = fetch_htf()
        if len(c) >= 205:
            et = ema(c, 200); rs = rsi(c, 14)
            if c[-1] > et[-1] and rs[-1] > 50: trend_1h = "UP"
            elif c[-1] < et[-1] and rs[-1] < 50: trend_1h = "DOWN"
    except Exception as e:
        log.warning("1H fetch: %s", e)
    try:
        _, _, _, c4, _ = fetch_4h()
        if len(c4) >= 55:
            et4 = ema(c4, 50); rs4 = rsi(c4, 14)
            if c4[-1] > et4[-1] and rs4[-1] > 50: trend_4h = "UP"
            elif c4[-1] < et4[-1] and rs4[-1] < 50: trend_4h = "DOWN"
    except Exception as e:
        log.warning("4H fetch: %s", e)
    return trend_1h, trend_4h


# ── Signal engine ─────────────────────────────────────────────────────────────

def score_signals(price, opens, highs, lows, closes, volumes=None):
    ef_v = ema(closes, FAST_EMA)
    es_v = ema(closes, SLOW_EMA)
    et_v = ema(closes, TREND_EMA)
    rs_v = rsi(closes, RSI_PERIOD)
    mm_v, ms_v = macd(closes)
    at_v = atr(highs, lows, closes, ATR_PERIOD)
    bb_u, bb_m, bb_l = bollinger(closes, 20, 2.0)
    sk_v, sd_v = stoch_rsi(closes)

    n = min(len(ef_v), len(es_v), len(et_v), len(rs_v),
            len(mm_v), len(ms_v), len(at_v), len(bb_u),
            len(sk_v), len(sd_v), len(closes), len(opens))

    ef = ef_v[-n:]; es = es_v[-n:]; et = et_v[-n:]
    rs = rs_v[-n:]; mm = mm_v[-n:]; ms = ms_v[-n:]
    at = at_v[-n:]; cl = closes[-n:]; op = opens[-n:]
    bu = bb_u[-n:]; bm = bb_m[-n:]; bl = bb_l[-n:]
    sk = sk_v[-n:]; sd = sd_v[-n:]

    atr_val = at[-1]
    atr_med = sorted(at[-20:])[10]
    if atr_val > atr_med * 2.2:
        return None, atr_val, rs[-1]   # ATR spike — skip
    if atr_val < ATR_MIN:
        return None, atr_val, rs[-1]   # dead/choppy market — skip

    body = cl[-1] - op[-1]

    # Pivot levels — near-level trades get +1 score bonus
    pivots = pivot_levels(highs, lows, closes)
    pivot_zone = atr_val * PIVOT_ZONE
    near_support    = any(abs(cl[-1] - pivots[k]) <= pivot_zone for k in ("S1", "S2", "PP") if k in pivots)
    near_resistance = any(abs(cl[-1] - pivots[k]) <= pivot_zone for k in ("R1", "R2", "PP") if k in pivots)
    # Find nearest pivot name and distance for display
    nearest_pivot = ""
    nearest_dist  = 9999
    for k, v in pivots.items():
        d = abs(cl[-1] - v)
        if d < nearest_dist:
            nearest_dist = d; nearest_pivot = f"{k} {v:.1f}"

    # ── Volume analysis ───────────────────────────────────────────────────────
    surge = False
    obv_rising = False
    obv_falling = False
    if volumes and len(volumes) >= 22:
        surge = vol_surge(volumes, period=20, mult=1.5)
        ob = obv(closes, volumes)
        # OBV trend: compare last 5 bars
        if len(ob) >= 6:
            obv_rising  = ob[-1] > ob[-6]
            obv_falling = ob[-1] < ob[-6]

    # ── BUY conditions (ALL state-based — fire whenever conditions hold) ──
    buy_conds = {
        "Above EMA200":  cl[-1] > et[-1],
        "EMA Bullish":   ef[-1] > es[-1],           # fast above slow
        "MACD Positive": mm[-1] > ms[-1],            # MACD above signal line
        "RSI Bullish":   rs[-1] > 50,
        "Strong Candle": body > atr_val * 0.4,
    }
    # BB: price above middle band (uptrend) or bouncing off lower band
    if cl[-1] > bm[-1]:
        buy_conds["BB Mid↑"] = True
    if cl[-2] <= bl[-2] and cl[-1] > bl[-1]:        # just bounced off lower band
        buy_conds["BB Bounce 🔄"] = True
    # StochRSI: oversold → buy zone
    if sk[-1] < 25:
        buy_conds["StochRSI Oversold"] = True
    if sk[-1] > sd[-1] and sk[-2] <= sd[-2] and sk[-2] < 40:  # K crosses D from low
        buy_conds["StochRSI Cross ✨"] = True

    # Pivot: near support = great BUY entry (+1 bonus)
    if near_support:
        buy_conds["Near Support 🎯"] = True

    # Volume: institutional accumulation signals
    if surge and obv_rising:
        buy_conds["Volume Surge 📈"] = True
    elif obv_rising:
        buy_conds["OBV Rising 📈"] = True

    # Bonus cross events (+1 if a cross just happened)
    if ef[-1] > es[-1] and ef[-2] <= es[-2]:        # EMA just crossed
        buy_conds["EMA Cross ✨"] = True
    if mm[-1] > ms[-1] and mm[-2] <= ms[-2]:        # MACD just crossed
        buy_conds["MACD Cross ✨"] = True
    if rs[-1] > 50 and rs[-2] <= 50:                # RSI just crossed
        buy_conds["RSI Cross ✨"] = True

    # ── SELL conditions ──
    sell_conds = {
        "Below EMA200":  cl[-1] < et[-1],
        "EMA Bearish":   ef[-1] < es[-1],
        "MACD Negative": mm[-1] < ms[-1],
        "RSI Bearish":   rs[-1] < 50,
        "Strong Candle": body < -atr_val * 0.4,
    }
    # BB: price below middle band or rejecting upper band
    if cl[-1] < bm[-1]:
        sell_conds["BB Mid↓"] = True
    if cl[-2] >= bu[-2] and cl[-1] < bu[-1]:        # just rejected from upper band
        sell_conds["BB Reject 🔄"] = True
    # StochRSI: overbought → sell zone
    if sk[-1] > 75:
        sell_conds["StochRSI Overbought"] = True
    if sk[-1] < sd[-1] and sk[-2] >= sd[-2] and sk[-2] > 60:  # K crosses D from high
        sell_conds["StochRSI Cross ✨"] = True

    # Pivot: near resistance = great SELL entry (+1 bonus)
    if near_resistance:
        sell_conds["Near Resistance 🎯"] = True

    # Volume: distribution signals
    if surge and obv_falling:
        sell_conds["Volume Surge 📉"] = True
    elif obv_falling:
        sell_conds["OBV Falling 📉"] = True

    if ef[-1] < es[-1] and ef[-2] >= es[-2]:
        sell_conds["EMA Cross ✨"] = True
    if mm[-1] < ms[-1] and mm[-2] >= ms[-2]:
        sell_conds["MACD Cross ✨"] = True
    if rs[-1] < 50 and rs[-2] >= 50:
        sell_conds["RSI Cross ✨"] = True

    sl = atr_val * ATR_MULT
    tp = sl * RR_RATIO

    results = []
    for direction, conds, sl_p, tp_p in [
        ("BUY",  buy_conds,  price - sl, price + tp),
        ("SELL", sell_conds, price + sl, price - tp),
    ]:
        hits  = [k for k, v in conds.items() if v]
        score = len(hits)
        if score >= SETUP_LEVEL:
            reason = " + ".join(hits[:3])
            bb_pct = (cl[-1] - bl[-1]) / (bu[-1] - bl[-1]) if (bu[-1] - bl[-1]) > 0 else 0.5
            results.append({
                "direction": direction,
                "score": score,
                "reason": reason,
                "reason_key": "|".join(sorted(hits)),
                "sl": sl_p, "tp": tp_p, "entry": price,
                "atr": atr_val, "rsi": rs[-1],
                "stoch_k": sk[-1], "bb_pct": bb_pct,
                "nearest_pivot": nearest_pivot,
                "vol_surge": surge,
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[0] if results else None, atr_val, rs[-1]


# ── Telegram ──────────────────────────────────────────────────────────────────

def send(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID,
                                 "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        log.error("Telegram: %s", e)


def fmt_signal(sig: dict, session: str, trends: tuple, af: dict = None) -> str:
    score = sig["score"]; d = sig["direction"]
    if   score >= STRONG_LEVEL: header = f"🚀 <b>STRONG {d} — XAUUSD</b>"; stars = "⭐⭐⭐"
    elif score >= SIGNAL_LEVEL: header = f"📡 <b>{d} SIGNAL — XAUUSD</b>";  stars = "⭐⭐"
    else:                       header = f"⚡ <b>{d} SETUP — XAUUSD</b>";   stars = "⭐  (not a trade yet)"

    htf_1h, htf_4h = trends if isinstance(trends, tuple) else (trends, "NEUTRAL")
    icon_1h = "📈" if htf_1h == "UP" else "📉" if htf_1h == "DOWN" else "➡️"
    icon_4h = "📈" if htf_4h == "UP" else "📉" if htf_4h == "DOWN" else "➡️"
    vol_txt = "  🔥 Vol!" if sig.get("vol_surge") else ""
    emoji   = "🟢" if d == "BUY" else "🔴"
    now     = datetime.utcnow().strftime("%H:%M UTC")

    macro = ""
    if af:
        y_icon = "📈" if af["yield"] == "RISING" else "📉" if af["yield"] == "FALLING" else "➡️"
        u_icon = "💪" if af["usd"] == "STRONG" else "🔽" if af["usd"] == "WEAK" else "➡️"
        s_icon = "🟢" if af["sentiment"] == "Bullish" else "🔴" if af["sentiment"] == "Bearish" else "⚪"
        bias   = af["gold_bias"]
        macro  = f"\n📰 News: {s_icon} {af['sentiment']}  |  10Y: {y_icon} {af['yield']}  |  USD: {u_icon} {af['usd']}  |  Bias: {bias:+d}/3"

    lines = [
        f"{emoji} {header}",
        f"━━━━━━━━━━━━━━━━",
        f"⏰ {now}  |  {session}",
        f"💡 {sig['reason']}",
        f"🏆 {stars}  |  4H: {icon_4h} {htf_4h}  |  1H: {icon_1h} {htf_1h}{vol_txt}{macro}",
        f"━━━━━━━━━━━━━━━━",
    ]
    if score >= SIGNAL_LEVEL:
        sk = sig.get("stoch_k", 50); bb = sig.get("bb_pct", 0.5)
        sk_icon = "🔴 OB" if sk > 75 else "🟢 OS" if sk < 25 else "⚪"
        bb_icon = "🔴 Top" if bb > 0.85 else "🟢 Bot" if bb < 0.15 else f"{bb:.0%}"
        sl_pts = sig['atr'] * ATR_MULT
        be_price = (sig['entry'] + sl_pts) if d == "BUY" else (sig['entry'] - sl_pts)
        pivot_txt = f"\n📌 Pivot  : {sig['nearest_pivot']}" if sig.get("nearest_pivot") else ""
        lines += [
            f"💰 Entry : <b>{sig['entry']:.2f}</b>",
            f"🛑 SL    : <b>{sig['sl']:.2f}</b>  ({sl_pts:.1f} pts)",
            f"🎯 TP    : <b>{sig['tp']:.2f}</b>  (1:{RR_RATIO})",
            f"🔐 BE    : move SL → entry when price hits <b>{be_price:.2f}</b>{pivot_txt}",
            f"📊 RSI: {sig['rsi']:.1f}  |  StochRSI: {sk:.0f} {sk_icon}  |  BB: {bb_icon}",
            f"━━━━━━━━━━━━━━━━",
            f"⚠️ Confirm before entering!",
        ]
    else:
        lines += [
            f"📍 Price: {sig['entry']:.2f}  |  RSI: {sig['rsi']:.1f}",
            f"👀 Watch — wait for 1 more condition",
        ]
    return "\n".join(lines)


def fmt_brief(price: float, rsi_v: float, atr_v: float, trends: tuple, af: dict = None) -> str:
    htf_1h, htf_4h = trends if isinstance(trends, tuple) else (trends, "NEUTRAL")
    htf_icon = "📈 Bullish" if htf_1h == "UP" else "📉 Bearish" if htf_1h == "DOWN" else "➡️ Neutral"
    htf_4h_icon = "📈 Bullish" if htf_4h == "UP" else "📉 Bearish" if htf_4h == "DOWN" else "➡️ Neutral"
    macro = ""
    if af:
        y_icon = "📈" if af["yield"] == "RISING" else "📉" if af["yield"] == "FALLING" else "➡️"
        u_icon = "💪" if af["usd"] == "STRONG" else "🔽" if af["usd"] == "WEAK" else "➡️"
        s_icon = "🟢" if af["sentiment"] == "Bullish" else "🔴" if af["sentiment"] == "Bearish" else "⚪"
        blocked_txt = f"\n⚠️ NEWS BLOCK: {af['block_event']}" if af["blocked"] else ""
        macro = (
            f"\n10Y Yield : {y_icon} {af['yield']}\n"
            f"USD       : {u_icon} {af['usd']}\n"
            f"News      : {s_icon} {af['sentiment']} ({af['sent_score']:+.2f})\n"
            f"Gold Bias : {af['gold_bias']:+d} / 3{blocked_txt}"
        )
    return (
        f"🌅 <b>LONDON SESSION OPEN — XAUUSD</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Price : <b>{price:.2f}</b>\n"
        f"4H    : {htf_4h_icon}\n"
        f"1H    : {htf_icon}\n"
        f"RSI   : {rsi_v:.1f}  |  ATR: {atr_v:.2f}{macro}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Signals: 11:00–00:00 (Lebanon 🇱🇧)"
    )


# ── Main loop ─────────────────────────────────────────────────────────────────

def run():
    global last_alert, last_brief_date
    log.info("GainzAlgo started")
    send(
        "🤖 <b>GainzAlgo V3 Started ✅</b>\n"
        "XAUUSD — London + NY sessions (11:00–00:00 Lebanon 🇱🇧)\n"
        "📡 Signal (3+ cond)  🚀 Strong (4+ cond)\n"
        "Filters: 4H + 1H + Trend + Volume"
    )

    while True:
        if not is_trading_day():
            time.sleep(3600)
            continue

        today = date.today()

        # Daily brief at London open
        if is_london_open() and last_brief_date != today:
            last_brief_date = today
            try:
                _, highs, lows, closes, _ = fetch_data()
                price = closes[-1]
                at_v  = atr(highs, lows, closes, ATR_PERIOD)[-1]
                rs_v  = rsi(closes, RSI_PERIOD)[-1]
                trends = get_htf_trend()
                af    = None
                if ALPHA_VANTAGE_KEY:
                    from alpha_filters import get_all_filters
                    af = get_all_filters()
                send(fmt_brief(price, rs_v, at_v, trends, af))
                log.info("Daily brief sent")
            except Exception as e:
                log.error("Brief error: %s", e)

        session = get_session()
        if not session:
            time.sleep(CHECK_EVERY)
            continue

        try:
            opens, highs, lows, closes, volumes = fetch_data()
            price  = closes[-1]
            trends = get_htf_trend()

            # Alpha Vantage filters (optional — only if key is set)
            af = None
            if ALPHA_VANTAGE_KEY:
                try:
                    from alpha_filters import get_all_filters
                    af = get_all_filters()
                    # Block during high-impact news events
                    if af["blocked"]:
                        log.info("News block: %s — skipping", af["block_event"])
                        time.sleep(CHECK_EVERY)
                        continue
                except Exception as e:
                    log.warning("Alpha filters error: %s", e)

            sig, atr_val, rsi_val = score_signals(price, opens, highs, lows, closes, volumes)

            if sig is None:
                log.info("ATR filter — skipping")
                time.sleep(CHECK_EVERY)
                continue

            d     = sig["direction"]
            score = sig["score"]
            rkey  = sig["reason_key"]
            htf_1h, htf_4h = trends

            # Minimum score: don't send Setup alerts (score=2) — wait for real Signal
            if score < ALERT_MIN_SCORE:
                log.info("Score %d < %d — setup only, not sent", score, ALERT_MIN_SCORE)
                time.sleep(CHECK_EVERY)
                continue

            # Ranging filter: EMA spread must show a real trend
            ef_last = ema(closes, FAST_EMA)[-1]
            es_last = ema(closes, SLOW_EMA)[-1]
            ema_spread = abs(ef_last - es_last) / atr_val if atr_val else 0
            if ema_spread < EMA_TREND_MIN:
                log.info("Ranging market (EMA spread %.2f < %.2f) — skipping", ema_spread, EMA_TREND_MIN)
                time.sleep(CHECK_EVERY)
                continue

            # 4H macro filter: ALL signals require 4H agreement or neutral
            ok_4h = (d == "BUY"  and htf_4h in ("UP", "NEUTRAL")) or \
                    (d == "SELL" and htf_4h in ("DOWN", "NEUTRAL"))
            if not ok_4h:
                log.info("4H disagrees (%s vs %s) — suppressed", d, htf_4h)
                time.sleep(CHECK_EVERY)
                continue

            # 1H filter: ALL signals require 1H agreement or neutral
            htf_ok = (d == "BUY"  and htf_1h in ("UP", "NEUTRAL")) or \
                     (d == "SELL" and htf_1h in ("DOWN", "NEUTRAL"))
            if not htf_ok:
                log.info("1H disagrees (%s vs %s) — suppressed", d, htf_1h)
                time.sleep(CHECK_EVERY)
                continue

            # Alpha Vantage macro filter: suppress BUY when environment is bearish for gold
            if af and score >= SIGNAL_LEVEL:
                bias = af.get("gold_bias", 0)
                if d == "BUY"  and bias <= -2:
                    log.info("Macro bias anti-gold (%d) — BUY suppressed", bias)
                    time.sleep(CHECK_EVERY)
                    continue
                if d == "SELL" and bias >= 2:
                    log.info("Macro bias pro-gold (%d) — SELL suppressed", bias)
                    time.sleep(CHECK_EVERY)
                    continue

            # Spam guard: don't re-alert same conditions within SPAM_MINUTES
            prev_rkey, prev_ts = last_alert.get(d, ("", 0))
            age_mins = (time.time() - prev_ts) / 60
            if rkey == prev_rkey and age_mins < SPAM_MINUTES:
                log.info("Same conditions %s — %d min ago — skip", d, int(age_mins))
                time.sleep(CHECK_EVERY)
                continue

            last_alert[d] = (rkey, time.time())
            send(fmt_signal(sig, session, trends, af))
            log.info("Alert: %s score=%d 4H=%s 1H=%s @ %.2f", d, score, htf_4h, htf_1h, price)

        except Exception as e:
            log.error("Error: %s", e)

        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    run()
