"""
Alpha Vantage filters for XAUUSD — 3 layers of protection:
  1. Treasury yield trend  (rising yields = headwind for gold)
  2. Dollar Index (DX) strength
  3. Gold news sentiment
  4. Economic calendar — block signals near major events
Requires env var: ALPHA_VANTAGE_KEY
All functions fail gracefully (return NEUTRAL) if API unavailable.
"""

import os, requests, logging
from datetime import datetime, date

log = logging.getLogger(__name__)

AV_KEY  = os.getenv("ALPHA_VANTAGE_KEY")
AV_BASE = "https://www.alphavantage.co/query"

# Known FOMC decision dates 2026 — decision at 19:00 UTC
FOMC_2026 = {
    date(2026, 1, 29), date(2026, 3, 19), date(2026, 4, 30),
    date(2026, 6, 11), date(2026, 7, 30), date(2026, 9, 17),
    date(2026, 11, 5), date(2026, 12, 17),
}

_cache: dict = {}   # simple in-memory cache to avoid hitting rate limits


def _get(params: dict, cache_key: str, ttl_mins: int = 30):
    """Cached GET request to Alpha Vantage."""
    now = datetime.utcnow()
    if cache_key in _cache:
        val, ts = _cache[cache_key]
        if (now - ts).seconds < ttl_mins * 60:
            return val
    if not AV_KEY:
        return None
    try:
        params["apikey"] = AV_KEY
        r = requests.get(AV_BASE, params=params, timeout=15)
        val = r.json()
        _cache[cache_key] = (val, now)
        return val
    except Exception as e:
        log.warning("Alpha Vantage [%s]: %s", cache_key, e)
    return None


# ── 1. Treasury Yield Trend ───────────────────────────────────────────────────

def get_yield_trend() -> str:
    """
    10Y Treasury yield direction — the most important driver for gold.
    Rising yields = dollar strength + opportunity cost → BEARISH for gold.
    Returns: 'RISING' | 'FALLING' | 'NEUTRAL'
    """
    data = _get({"function": "TREASURY_YIELD", "interval": "daily",
                 "maturity": "10year"}, "yield_10y", ttl_mins=60)
    if not data:
        return "NEUTRAL"
    series = data.get("data", [])
    if len(series) < 5:
        return "NEUTRAL"
    # data is newest-first
    vals = [float(s["value"]) for s in series[:5] if s.get("value") not in (".", None)]
    if len(vals) < 3:
        return "NEUTRAL"
    change = vals[0] - vals[2]   # today vs 2 days ago
    if change >  0.05: return "RISING"
    if change < -0.05: return "FALLING"
    return "NEUTRAL"


# ── 2. Dollar Index ───────────────────────────────────────────────────────────

def get_dollar_strength() -> str:
    """
    EUR/USD hourly trend as DXY proxy.
    EUR/USD falling → USD strengthening → BEARISH for gold.
    Returns: 'STRONG' | 'WEAK' | 'NEUTRAL'
    """
    data = _get({"function": "FX_INTRADAY", "from_symbol": "EUR",
                 "to_symbol": "USD", "interval": "60min",
                 "outputsize": "compact"}, "eurusd_1h", ttl_mins=30)
    if not data:
        return "NEUTRAL"
    series = data.get("Time Series FX (60min)", {})
    if not series:
        return "NEUTRAL"
    closes = [float(v["4. close"])
              for _, v in sorted(series.items(), reverse=True)[:10]]
    closes.reverse()
    if len(closes) < 5:
        return "NEUTRAL"
    trend = closes[-1] - closes[-5]
    if trend < -0.0015: return "STRONG"   # USD up → gold headwind
    if trend >  0.0015: return "WEAK"     # USD down → gold tailwind
    return "NEUTRAL"


# ── 3. Gold news sentiment ────────────────────────────────────────────────────

def get_gold_sentiment() -> tuple:
    """
    Returns (label, score): 'Bullish'|'Bearish'|'Neutral', score -1..1
    """
    data = _get({"function": "NEWS_SENTIMENT", "tickers": "FOREX:XAUUSD",
                 "limit": "10", "sort": "LATEST"}, "gold_news", ttl_mins=20)
    if not data:
        return "Neutral", 0.0
    scores = []
    for article in data.get("feed", [])[:5]:
        for ts in article.get("ticker_sentiment", []):
            if "XAUUSD" in ts.get("ticker", ""):
                scores.append(float(ts.get("ticker_sentiment_score", 0)))
    if not scores:
        return "Neutral", 0.0
    avg = sum(scores) / len(scores)
    if avg >  0.15: return "Bullish", round(avg, 2)
    if avg < -0.15: return "Bearish", round(avg, 2)
    return "Neutral", round(avg, 2)


# ── 4. Economic calendar ──────────────────────────────────────────────────────

def is_high_impact_window() -> tuple:
    """
    Returns (True, event_name) if within danger window of a high-impact event.
    Bot should skip signals when this returns True.
    """
    now   = datetime.utcnow()
    today = now.date()
    dow   = now.weekday()   # 0=Mon … 4=Fri
    nm    = now.hour * 60 + now.minute  # minutes since midnight UTC

    def near(eh, em, pre=45, post=60):
        t = eh * 60 + em
        return (t - pre) <= nm <= (t + post)

    # FOMC (known 2026 dates, 19:00 UTC)
    if today in FOMC_2026 and near(19, 0, pre=60, post=90):
        return True, "FOMC Rate Decision ⚠️"

    # NFP — first Friday of each month, 13:30 UTC
    if dow == 4 and today.day <= 7 and near(13, 30, pre=60, post=60):
        return True, "Non-Farm Payrolls (NFP) ⚠️"

    # US CPI — 2nd week, usually Wednesday 13:30 UTC
    if dow == 2 and 8 <= today.day <= 14 and near(13, 30):
        return True, "US CPI ⚠️"

    # US PPI — 3rd week, Thursday 13:30 UTC
    if dow == 3 and 14 <= today.day <= 21 and near(13, 30):
        return True, "US PPI ⚠️"

    # GDP / Retail Sales — end of month, Wednesday 13:30 UTC
    if dow == 2 and today.day >= 24 and near(13, 30):
        return True, "US GDP / Retail Sales ⚠️"

    return False, ""


# ── Combined snapshot ─────────────────────────────────────────────────────────

def get_all_filters() -> dict:
    """Single call to get all filters. Cache-friendly."""
    blocked, event   = is_high_impact_window()
    yield_trend      = get_yield_trend()
    usd              = get_dollar_strength()
    sentiment, score = get_gold_sentiment()

    # Compute a bias: +1 pro-gold, -1 anti-gold, 0 neutral
    bias = 0
    if yield_trend == "FALLING": bias += 1
    if yield_trend == "RISING":  bias -= 1
    if usd == "WEAK":            bias += 1
    if usd == "STRONG":          bias -= 1
    if sentiment == "Bullish":   bias += 1
    if sentiment == "Bearish":   bias -= 1

    return {
        "blocked":     blocked,
        "block_event": event,
        "yield":       yield_trend,   # RISING/FALLING/NEUTRAL
        "usd":         usd,           # STRONG/WEAK/NEUTRAL
        "sentiment":   sentiment,     # Bullish/Bearish/Neutral
        "sent_score":  score,
        "gold_bias":   bias,          # -3..+3 overall market environment
    }
