"""
Quiver Quant Watcher — Insider & Institutional Trade Monitor
Watches for new insider transactions + institution activity → Telegram alerts
"""

import os, time, json, logging
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
QUIVER_API_KEY   = os.getenv("QUIVER_API_KEY")

# Kalshi integration (optional — only active if credentials are set)
KALSHI_EMAIL    = os.getenv("KALSHI_EMAIL")
KALSHI_PASSWORD = os.getenv("KALSHI_PASSWORD")
KALSHI_DEMO     = os.getenv("KALSHI_DEMO", "false").lower() == "true"

_kalshi = None
def _get_kalshi():
    global _kalshi
    if not KALSHI_EMAIL or not KALSHI_PASSWORD:
        return None
    if _kalshi is None:
        from kalshi_client import KalshiClient
        _kalshi = KalshiClient(KALSHI_EMAIL, KALSHI_PASSWORD, demo=KALSHI_DEMO)
        if not _kalshi.login():
            _kalshi = None
    return _kalshi

CHECK_EVERY = 60 * 30   # check every 30 minutes

# ── Investors/Institutions to watch ──────────────────────────────────────────
# Add names you want to track (partial match, case-insensitive)
WATCH_INSIDERS = [
    # Congress members
    "Pelosi", "Burr", "Loeffler", "Tuberville",
    # Corporate insiders — add ticker + person
    # e.g. "Zuckerberg", "Musk", "Bezos",
]

WATCH_INSTITUTIONS = [
    "Berkshire",        # Warren Buffett
    "Bridgewater",      # Ray Dalio
    "Renaissance",      # Jim Simons
    "Soros",
    "Citadel",
    # Add any institution you want to follow
]

# Min transaction value to alert on (USD)
MIN_INSIDER_VALUE    = 50_000      # $50K minimum for insiders
MIN_INSTITUTION_VALUE = 1_000_000  # $1M minimum for institutions

# Track already-seen transactions (avoid duplicates)
seen_ids = set()

HEADERS = {
    "accept": "application/json",
    "X-CSRFToken": QUIVER_API_KEY,
    "Authorization": f"Token {QUIVER_API_KEY}",
}

BASE = "https://api.quiverquant.com/beta"


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


# ── Quiver API helpers ────────────────────────────────────────────────────────

def get_congress_trades():
    """Recent Congressional trading disclosures."""
    try:
        r = requests.get(f"{BASE}/live/congresstrading",
                         headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.error("Congress API: %s", e)
    return []


def get_insider_trades(ticker=None):
    """SEC Form 4 insider transactions."""
    try:
        url = f"{BASE}/live/insiders" if not ticker else f"{BASE}/historical/insiders/{ticker}"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.error("Insider API: %s", e)
    return []


def get_institution_holdings():
    """13F institutional holdings changes."""
    try:
        r = requests.get(f"{BASE}/live/institutions",
                         headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.error("Institution API: %s", e)
    return []


# ── Signal formatting ─────────────────────────────────────────────────────────

def fmt_congress(trade: dict) -> str:
    name     = trade.get("Representative", "Unknown")
    ticker   = trade.get("Ticker", "N/A")
    tx_type  = trade.get("Transaction", "N/A")
    amount   = trade.get("Amount", "N/A")
    date     = trade.get("TransactionDate", "N/A")
    party    = trade.get("Party", "")
    emoji    = "🟢" if "Purchase" in str(tx_type) else "🔴"

    return (
        f"🏛️ <b>CONGRESS TRADE ALERT</b> {emoji}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👤 {name} ({party})\n"
        f"📊 Ticker : <b>{ticker}</b>\n"
        f"💼 Action : <b>{tx_type}</b>\n"
        f"💰 Amount : {amount}\n"
        f"📅 Date   : {date}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚠️ Disclosed trade — verify before following"
    )


def fmt_insider(trade: dict) -> str:
    name     = trade.get("Name", "Unknown")
    ticker   = trade.get("Ticker", "N/A")
    tx_type  = trade.get("AcquiredDisposed", "N/A")
    shares   = trade.get("Shares", 0)
    price    = trade.get("Price", 0)
    value    = float(shares or 0) * float(price or 0)
    date     = trade.get("Date", "N/A")
    emoji    = "🟢" if tx_type == "A" else "🔴"
    action   = "BOUGHT" if tx_type == "A" else "SOLD"

    return (
        f"📋 <b>INSIDER TRANSACTION</b> {emoji}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👤 {name}\n"
        f"📊 Ticker : <b>{ticker}</b>\n"
        f"💼 Action : <b>{action}</b>\n"
        f"📦 Shares : {shares:,}\n"
        f"💵 Price  : ${price}\n"
        f"💰 Value  : ${value:,.0f}\n"
        f"📅 Date   : {date}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚠️ SEC Form 4 filing — verify before following"
    )


def fmt_institution(holding: dict) -> str:
    inst     = holding.get("Institution", "Unknown")
    ticker   = holding.get("Ticker", "N/A")
    shares   = holding.get("Shares", 0)
    change   = holding.get("Change", 0)
    date     = holding.get("Date", "N/A")
    emoji    = "🟢" if float(change or 0) > 0 else "🔴"
    action   = "INCREASED" if float(change or 0) > 0 else "REDUCED"

    return (
        f"🏦 <b>INSTITUTION ACTIVITY</b> {emoji}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🏢 {inst}\n"
        f"📊 Ticker : <b>{ticker}</b>\n"
        f"💼 Action : <b>{action} position</b>\n"
        f"📦 Shares : {shares:,}\n"
        f"📈 Change : {change:+,}\n"
        f"📅 Date   : {date}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚠️ 13F filing — may be 45 days delayed"
    )


# ── Watchers ──────────────────────────────────────────────────────────────────

def watch_congress():
    trades = get_congress_trades()
    found  = 0
    for t in trades:
        name = t.get("Representative", "")
        uid  = f"congress-{name}-{t.get('Ticker')}-{t.get('TransactionDate')}"
        if uid in seen_ids:
            continue
        # Check if in watchlist
        if not any(w.lower() in name.lower() for w in WATCH_INSIDERS):
            continue
        seen_ids.add(uid)
        msg = fmt_congress(t)
        ticker = t.get("Ticker", "")
        if ticker:
            k = _get_kalshi()
            if k:
                from kalshi_client import fmt_kalshi_markets
                markets = k.find_markets_for_ticker(ticker)
                msg += fmt_kalshi_markets(markets, ticker)
        send(msg)
        found += 1
    return found


def watch_insiders():
    trades = get_insider_trades()
    found  = 0
    for t in trades:
        name   = t.get("Name", "")
        shares = float(t.get("Shares") or 0)
        price  = float(t.get("Price") or 0)
        value  = shares * price
        uid    = f"insider-{name}-{t.get('Ticker')}-{t.get('Date')}"

        if uid in seen_ids:
            continue
        if value < MIN_INSIDER_VALUE:
            continue
        if not any(w.lower() in name.lower() for w in WATCH_INSIDERS):
            continue
        seen_ids.add(uid)
        msg = fmt_insider(t)
        ticker = t.get("Ticker", "")
        if ticker:
            k = _get_kalshi()
            if k:
                from kalshi_client import fmt_kalshi_markets
                markets = k.find_markets_for_ticker(ticker)
                msg += fmt_kalshi_markets(markets, ticker)
        send(msg)
        found += 1
    return found


def watch_institutions():
    holdings = get_institution_holdings()
    found    = 0
    for h in holdings:
        inst   = h.get("Institution", "")
        change = float(h.get("Change") or 0)
        uid    = f"inst-{inst}-{h.get('Ticker')}-{h.get('Date')}"

        if uid in seen_ids:
            continue
        if abs(change) < MIN_INSTITUTION_VALUE:
            continue
        if not any(w.lower() in inst.lower() for w in WATCH_INSTITUTIONS):
            continue
        seen_ids.add(uid)
        send(fmt_institution(h))
        found += 1
    return found


# ── Main loop ─────────────────────────────────────────────────────────────────

def run():
    log.info("Quiver Watcher started")
    send(
        "🔭 <b>Quiver Watcher Started ✅</b>\n"
        "Monitoring insider & institutional trades...\n"
        f"Watching: {', '.join(WATCH_INSIDERS[:3])}...\n"
        f"Institutions: {', '.join(WATCH_INSTITUTIONS[:3])}..."
    )

    while True:
        # Only run on weekdays
        if datetime.utcnow().weekday() >= 5:
            time.sleep(3600)
            continue

        try:
            c = watch_congress()
            i = watch_insiders()
            n = watch_institutions()
            log.info("Checked — Congress: %d, Insiders: %d, Institutions: %d new", c, i, n)
        except Exception as e:
            log.error("Error: %s", e)

        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    run()
