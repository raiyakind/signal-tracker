#!/usr/bin/env python3
"""
poll_trades.py
Fetches congressional trade disclosures from capitoltrades.com,
filters for Signal's four pilots, enriches each trade with values/infra tags,
and writes trades.json to the repo root.

Runs via GitHub Actions every 15 minutes during market hours.
"""

import json
import re
import requests
from datetime import datetime, timezone

# ── CONFIG ─────────────────────────────────────────────────────────────────

# Politician bioguide IDs on Capitol Trades
PILOTS = {
    "K000389": "khanna",     # Ro Khanna
    "S001185": "sewell",     # Terri Sewell
    "M001153": "murkowski",  # Lisa Murkowski
    "M001136": "mcclain",    # Lisa McClain
}

# Keywords that trigger values/infra tags based on ticker or company name
VALUES_TAGS = {
    "Healthcare equity":    ["UNH","CVS","ABBV","HCA","ABC","MCK","AET","CI","HUM","MOH","CNC"],
    "Clean energy":         ["NEE","DTE","AEP","SO","ED","PCG","XEL","WEC","ETR","CNP"],
    "Clean nuclear":        ["CEG","CCJ","NNE","SMR","BWXT","BWX"],
    "AI infrastructure":    ["NVDA","AMD","INTC","MRVL","AVGO","QCOM","MU","AMAT","LRCX","KLAC"],
    "Semi sovereignty":     ["TSM","AMAT","ASML","LRCX","KLAC","MU","ON","TER"],
    "Grid infrastructure":  ["EATON","ETN","HUBB","GNRC","VST","NRG","AES","BKR","FLS"],
    "Healthcare access":    ["ABBV","BMY","PFE","MRK","JNJ","LLY","REGN","GILD","AMGN","BIIB"],
    "Housing":              ["DHI","LEN","PHM","TOL","NVR","MDC","TMHC","MHO","SKY","CVCO"],
    "Defense tech":         ["LMT","RTX","NOC","GD","BAH","CACI","SAIC","LDOS","HII","TDG"],
    "AI infrastructure":    ["NVDA","AMD","INTC","MRVL","AVGO","QCOM","MU","AMAT","LRCX","KLAC",
                             "SMCI","HPE"],
}

INFRA_TICKERS = {
    "NVDA","AMD","INTC","MRVL","AVGO","QCOM","MU","AMAT","LRCX","KLAC","TSM","ASML","BWX","BWXT",
    "NNE","SMR","CCJ","NRG","VST","AES","HUBB","GNRC","SMCI","HPE",
}

BASE_URL = "https://www.capitoltrades.com"

# ── HELPERS ─────────────────────────────────────────────────────────────────

def get_values_tag(ticker: str) -> str | None:
    ticker = ticker.upper()
    for tag, tickers in VALUES_TAGS.items():
        if ticker in tickers:
            return tag
    return None

def is_infra(ticker: str) -> bool:
    return ticker.upper() in INFRA_TICKERS

def format_amount(value) -> str:
    """Convert numeric dollar value to a human-readable range string."""
    if not value:
        return "Unknown"
    try:
        v = int(value)
    except (ValueError, TypeError):
        return str(value)
    if v <= 1000:      return "$1K"
    if v <= 15000:     return "$1K–$15K"
    if v <= 50000:     return "$15K–$50K"
    if v <= 100000:    return "$50K–$100K"
    if v <= 250000:    return "$100K–$250K"
    if v <= 500000:    return "$250K–$500K"
    if v <= 1000000:   return "$500K–$1M"
    return "$1M+"

def _is_new(pub_date: str) -> bool:
    """Return True if filed within the last 7 days."""
    if not pub_date:
        return False
    try:
        d = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - d).days <= 7
    except Exception:
        return False

# ── FETCHING ─────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Regex to extract RSC push payload strings from the HTML
# Matches: self.__next_f.push([1,"<escaped-json-string>"])
RSC_PATTERN = re.compile(
    r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)',
    re.DOTALL
)

def extract_trades_from_html(html: str) -> list[dict]:
    """Parse Capitol Trades RSC payload and return raw trade dicts."""
    decoder = json.JSONDecoder()
    trades = []

    for match in RSC_PATTERN.finditer(html):
        raw_str = match.group(1)
        # Decode the escaped JSON string
        try:
            decoded = json.loads('"' + raw_str + '"')
        except Exception:
            continue

        # Find all trade objects (they start with {"_issuerId":)
        for m in re.finditer(r'\{"_issuerId":', decoded):
            try:
                obj, _ = decoder.raw_decode(decoded, m.start())
                if "_txId" in obj and "txDate" in obj:
                    trades.append(obj)
            except Exception:
                pass

    return trades

def fetch_pilot_trades(bioguide_id: str, pilot_key: str) -> list[dict]:
    """Fetch and normalise trades for one pilot from Capitol Trades."""
    url = f"{BASE_URL}/trades?politician={bioguide_id}&pageSize=100&page=1"
    print(f"  Fetching {pilot_key} ({bioguide_id}) from {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"  Error fetching {pilot_key}: {e}")
        return []

    raw_trades = extract_trades_from_html(r.text)
    print(f"  {len(raw_trades)} raw trades found for {pilot_key}")

    results = []
    for i, raw in enumerate(raw_trades):
        t = normalise_trade(raw, pilot_key, i)
        if t:
            results.append(t)
    print(f"  {len(results)} valid trades after normalisation")
    return results

def normalise_trade(raw: dict, pilot_key: str, idx: int) -> dict | None:
    """Map a raw Capitol Trades object to our trades.json schema."""
    issuer = raw.get("issuer") or {}
    ticker_raw = issuer.get("issuerTicker") or ""
    # Ticker comes as "META:US" — strip exchange suffix
    ticker = ticker_raw.split(":")[0].upper().strip()
    if not ticker or ticker in ("N/A", ""):
        return None

    tx_type = (raw.get("txType") or "").lower()
    if tx_type not in ("buy", "sell", "call", "put", "exchange"):
        tx_type = "buy"

    pub_date = (raw.get("pubDate") or "")
    filed_date = pub_date[:10] if pub_date else ""

    return {
        "id":         f"ct{raw.get('_txId', idx)}",
        "pilot":      pilot_key,
        "ticker":     ticker,
        "company":    issuer.get("issuerName") or ticker,
        "type":       tx_type,
        "amount":     format_amount(raw.get("value")),
        "transDate":  raw.get("txDate") or "",
        "filedDate":  filed_date,
        "isNew":      _is_new(pub_date),
        "values":     get_values_tag(ticker),
        "infra":      is_infra(ticker),
        "chamber":    raw.get("chamber") or "house",
    }

# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    all_trades = []

    for bioguide_id, pilot_key in PILOTS.items():
        trades = fetch_pilot_trades(bioguide_id, pilot_key)
        all_trades.extend(trades)

    # Sort by transaction date descending
    all_trades.sort(key=lambda t: t.get("transDate") or "", reverse=True)

    print(f"\nMatched {len(all_trades)} trades across {len(PILOTS)} pilots")

    output = {
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "source": "capitoltrades.com",
        "pilots": list(set(t["pilot"] for t in all_trades)) if all_trades else list(PILOTS.values()),
        "trades": all_trades,
    }

    with open("trades.json", "w") as f:
        json.dump(output, f, indent=2)
    print("trades.json written successfully")

if __name__ == "__main__":
    main()
