"""
Kalshi API Client — Prediction Market Trading
Docs: https://trading-api.kalshi.com/trade-api/v2
"""

import os, requests, logging
from typing import Optional

log = logging.getLogger(__name__)

KALSHI_BASE = "https://trading-api.kalshi.com/trade-api/v2"
KALSHI_DEMO = "https://demo-api.kalshi.co/trade-api/v2"

# Maps stock tickers → search keywords on Kalshi
TICKER_KEYWORDS = {
    # AI / Semiconductors
    "NVDA": ["nvidia", "AI", "semiconductor"],
    "AMD":  ["semiconductor", "AI chip"],
    "INTC": ["intel", "semiconductor"],
    "TSM":  ["TSMC", "semiconductor"],
    "MSFT": ["microsoft", "AI", "cloud"],
    "GOOGL":"google AI",
    "META": ["meta", "AI"],
    "AAPL": ["apple"],
    # Defense
    "LMT":  ["defense", "lockheed", "military"],
    "RTX":  ["defense", "raytheon"],
    "NOC":  ["defense", "northrop"],
    "GD":   ["defense", "military"],
    "BA":   ["boeing", "aerospace"],
    # Energy
    "XOM":  ["oil", "energy"],
    "CVX":  ["oil", "energy"],
    "COP":  ["oil", "energy"],
    # Finance
    "JPM":  ["federal reserve", "interest rate", "bank"],
    "BAC":  ["federal reserve", "interest rate"],
    "GS":   ["market crash", "recession"],
    # Biotech/Pharma
    "PFE":  ["vaccine", "pharma", "FDA"],
    "MRNA": ["vaccine", "FDA"],
    "JNJ":  ["pharma", "healthcare"],
    # Crypto ETFs
    "COIN": ["bitcoin", "crypto"],
    "MSTR": ["bitcoin"],
    "IBIT": ["bitcoin ETF"],
    "GBTC": ["bitcoin ETF"],
}


class KalshiClient:
    def __init__(self, email: str, password: str, demo: bool = False):
        self.base = KALSHI_DEMO if demo else KALSHI_BASE
        self.email = email
        self.password = password
        self.token: Optional[str] = None
        self.member_id: Optional[str] = None

    def login(self) -> bool:
        try:
            r = requests.post(
                f"{self.base}/login",
                json={"email": self.email, "password": self.password},
                timeout=15
            )
            if r.status_code == 200:
                data = r.json()
                self.token = data.get("token")
                self.member_id = data.get("member_id")
                log.info("Kalshi login OK — member %s", self.member_id)
                return True
            log.error("Kalshi login failed: %d %s", r.status_code, r.text[:200])
        except Exception as e:
            log.error("Kalshi login error: %s", e)
        return False

    @property
    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def get_balance(self) -> float:
        try:
            r = requests.get(f"{self.base}/portfolio/balance",
                             headers=self._headers, timeout=10)
            if r.status_code == 200:
                return r.json().get("balance", 0) / 100  # cents → dollars
        except Exception as e:
            log.error("Kalshi balance: %s", e)
        return 0.0

    def find_markets_for_ticker(self, ticker: str, limit: int = 3) -> list:
        """Search open Kalshi markets relevant to a stock ticker."""
        keywords = TICKER_KEYWORDS.get(ticker.upper(), [ticker.lower()])
        found = []
        seen = set()
        try:
            r = requests.get(
                f"{self.base}/markets",
                headers=self._headers,
                params={"limit": 100, "status": "open"},
                timeout=15
            )
            if r.status_code != 200:
                return []
            markets = r.json().get("markets", [])
            for kw in keywords:
                kw_lower = kw.lower()
                for m in markets:
                    title = (m.get("title", "") + " " + m.get("subtitle", "")).lower()
                    if kw_lower in title and m["ticker"] not in seen:
                        seen.add(m["ticker"])
                        found.append(m)
                if len(found) >= limit:
                    break
        except Exception as e:
            log.error("Kalshi market search: %s", e)
        return found[:limit]

    def place_order(self, ticker: str, side: str, count: int, yes_price_cents: int) -> dict:
        """
        Place a limit order.
        ticker: Kalshi market ticker (e.g. 'NVDA-25-300')
        side: 'yes' or 'no'
        count: number of contracts
        yes_price_cents: price in cents for YES side (1-99)
        """
        no_price = 100 - yes_price_cents
        payload = {
            "ticker": ticker,
            "side": side,
            "count": count,
            "type": "limit",
            "yes_price": yes_price_cents,
            "no_price": no_price,
        }
        try:
            r = requests.post(f"{self.base}/portfolio/orders",
                              headers=self._headers, json=payload, timeout=15)
            return r.json()
        except Exception as e:
            log.error("Kalshi order error: %s", e)
            return {"error": str(e)}

    def get_positions(self) -> list:
        try:
            r = requests.get(f"{self.base}/portfolio/positions",
                             headers=self._headers, timeout=10)
            if r.status_code == 200:
                return r.json().get("market_positions", [])
        except Exception as e:
            log.error("Kalshi positions: %s", e)
        return []


def fmt_kalshi_markets(markets: list, ticker: str) -> str:
    if not markets:
        return ""
    lines = [f"\n🎯 <b>KALSHI MARKETS — {ticker}</b>"]
    for m in markets:
        yes_price = m.get("yes_bid", m.get("last_price", "?"))
        lines.append(
            f"• {m.get('title', 'N/A')}\n"
            f"  Ticker: <code>{m['ticker']}</code> | YES: {yes_price}¢"
        )
    lines.append("📲 Trade at kalshi.com")
    return "\n".join(lines)
