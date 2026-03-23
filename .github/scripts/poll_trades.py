#!/usr/bin/env python3
"""
poll_trades.py
Fetches congressional trade disclosures from housestockwatcher.com and
senatestockwatcher.com, filters for Signal's four pilots, enriches each
trade with values/infra tags, and writes trades.json to the repo root.

Runs via GitHub Actions every 15 minutes during market hours.
"""

import json
import requests
from datetime import datetime, timezone, timedelta

# ââ CONFIG ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

HOUSE_API  = "https://housestockwatcher.com/api"
SENATE_API = "https://senatestockwatcher.com/api"

# Exact names as they appear in STOCK Act filings
PILOTS = {
    "Terri Sewell":    "sewell",
    "Ro Khanna":       "khanna",
    "Lisa Murkowski":  "murkowski",
    "Lisa McClain":    "mcclain",
}

# Keywords that trigger values/infra tags based on ticker or company name
VALUES_TAGS = {
    "Healthcare equity":    ["UNH","CVS","ABBV","HCA","ABC","MCK","AET","CI","HUM","MOH","CNC"],
    "Clean energy":         ["NEE","DTE","AEP","SO","ED","PCG","XEL","WEC","ETR","CNP"],
    "Clean nuclear":        ["CEG","CCJ","NNE","SMR","BWXT","BWX"],
    "AI infrastructure":    ["NVDA","AMD","INTC","MRVL","AVGO","QCOM","MU","AMAT","LRCX","KLAC"],
    "Semi sovereignty":     ["TSM","AMAT","ASML","LRCX","KLAC","MU","ON","TER"],
    "Grid infrastructure":  ["EATON","ETN","HUBB","GNRC","VST","NRG","AES","BKR","FLS"],
    "Healthcare access":    ["ABBV","BMY","PFE","MRK","JNJ","LLY","AMGN","GILD","BIIB","REGN"],
    "Advanced nuclear":     ["BWX","BWXT","NNE","SMR","CCJ","UEC","DNN","URG"],
    "AI power & cooling":   ["VRT","SMCI","DELL","HPE","NTAP","FSLR","ENPH"],
}

INFRA_TICKERS = {
    "VRT","GEV","NEE","CEG","EATON","ETN","MU","MRVL","NVDA","AMD",
    "AVGO","QCOM","AMAT","LRCX","KLAC","TSM","ASML","BWX","BWXT",
    "NNE","SMR","CCJ","NRG","VST","AES","HUBB","GNRC","SMCI","HPE",
}

# ââ HELPERS âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def get_values_tag(ticker: str) -> str | None:
    ticker = ticker.upper()
    for tag, tickers in VALUES_TAGS.items():
        if ticker in tickers:
            return tag
    return None

def is_infra(ticker: str) -> bool:
    return ticker.upper() in INFRA_TICKERS

def parse_amount(raw: str) -> str:
    """Normalise the STOCK Act amount range string."""
    if not raw:
        return "Unknown"
    raw = raw.strip()
    # Already looks like $Xâ$Y
    if "$" in raw:
        return raw
    # Sometimes comes as numeric ranges like "1001-15000"
    try:
        parts = raw.replace(",","").split("-")
        lo, hi = int(parts[0]), int(parts[1])
        def fmt(n):
            if n >= 1_000_000: return f"${n//1_000_000}M"
            if n >= 1_000:     return f"${n//1_000}K"
            return f"${n}"
        return f"{fmt(lo)}â{fmt(hi)}"
    except Exception:
        return raw

def fetch_house() -> list[dict]:
    try:
        r = requests.get(f"{HOUSE_API}/trades_by_date", timeout=15)
        r.raise_for_status()
        return r.json() if isinstance(r.json(), list) else []
    except Exception as e:
        print(f"House API error: {e}")
        return []

def fetch_senate() -> list[dict]:
    try:
        r = requests.get(f"{SENATE_API}/all_transactions", timeout=15)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("data", [])
    except Exception as e:
        print(f"Senate API error: {e}")
        return []

def normalise_house(raw: dict, idx: int) -> dict | None:
    name = f"{raw.get('representative','').strip()}"
    pilot_key = find_pilot_key(name)
    if not pilot_key:
        return None
    ticker   = (raw.get("ticker") or "").upper().strip()
    if not ticker or ticker in ("N/A",""):
        return None
    tx_type  = (raw.get("type") or "").lower()
    if "purchase" in tx_type:  tx_type = "buy"
    elif "sale" in tx_type:    tx_type = "sell"
    elif "exchange" in tx_type: tx_type = "call"
    else: tx_type = "buy"
    return {
        "id":         f"h{idx}",
        "pilot":      pilot_key,
        "ticker":     ticker,
        "company":    raw.get("asset_description","ticker"),
        "type":       tx_type,
        "amount":     parse_amount(raw.get("amount","")),
        "transDate":  raw.get("transaction_date",""),
        "filedDate":  raw.get("disclosure_date",""),
        "isNew":      _is_new(raw.get("disclosure_date","")),
        "values":     get_values_tag(ticker),
        "infra":      is_infra(ticker),
        "chamber":    "house",
    }

def normalise_senate(raw: dict, idx: int) -> dict | None:
    name = f"{raw.get('first_name','')} {raw.get('last_name','')}".strip()
    pilot_key = find_pilot_key(name)
    if not pilot_key:
        return None
    ticker = (raw.get("ticker") or "").upper().strip()
    if not ticker or ticker in ("N/A",""):
        return None
    tx_type = (raw.get("type") or "").lower()
    if "purchase" in tx_type:   tx_type = "buy"
    elif "sale" in tx_type:     tx_type = "sell"
    elif "exchange" in tx_type: tx_type = "call"
    else: tx_type = "buy"
    return {
        "id":         f"s{idx}",
        "pilot":      pilot_key,
        "ticker":     ticker,
        "company":    raw.get("asset_description", ticker),
        "type":       tx_type,
        "amount":     parse_amount(raw.get("amount","")),
        "transDate":  raw.get("transaction_date",""),
        "filedDate":  raw.get("disclosure_date",""),
        "isNew":      _is_new(raw.get("disclosure_date","")),
        "values":     get_values_tag(ticker),
        "infra":      is_infra(ticker),
        "chamber":    "senate",
    }

def _is_new(disclosure_date: str) -> bool:
    """True if filed within the last 7 days."""
    try:
        d = datetime.strptime(disclosure_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days <= 7
    except Exception:
        return False

# ââ MAIN ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def main():
    print("Fetching house disclosures...")
    house_raw  = fetch_house()
    print(f"  {len(house_raw)} raw records")
    house_names = sorted(set(r.get('representative','') for r in house_raw if r.get('representative','').strip()))
    print(f"  House names seen: {house_names}")

    print("Fetching senate disclosures...")
    senate_raw = fetch_senate()
    print(f"  {len(senate_raw)} raw records")
    senate_names = sorted(set(f"{r.get('first_name','')} {r.get('last_name','')}" .strip() for r in senate_raw if r.get('last_name','')))
    print(f"  Senate names seen: {senate_names}")

    trades = []

    for i, row in enumerate(house_raw):
        t = normalise_house(row, i)
        if t:
            trades.append(t)

    for i, row in enumerate(senate_raw):
        t = normalise_senate(row, i)
        if t:
            trades.append(t)

    # Sort by filed date descending (newest first)
    trades.sort(key=lambda x: x.get("filedDate",""), reverse=True)

    # Cap at 50 most recent
    trades = trades[:50]

    print(f"Matched {len(trades)} trades for our 4 pilots")

    output = {
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "source": "housestockwatcher.com + senatestockwatcher.com",
        "pilots": list(PILOTS.keys()),
        "trades": trades,
    }

    with open("trades.json", "w") as f:
        json.dump(output, f, indent=2)

    print("trades.json written successfully")

if __name__ == "__main__":
    main()
