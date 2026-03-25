#!/usr/bin/env python3
"""
poll_trades.py
Fetches congressional trade disclosures from capitoltrades.com,
filters for Signal's pilots, generates AI analysis via Claude,
emails a digest of new trades via SendGrid, and writes trades.json.
Runs via GitHub Actions every 15 minutes during market hours.
"""
import json
import os
import re
import time
import requests
from datetime import datetime, timezone

# ── Politician bioguide IDs on Capitol Trades ────────────────────────────────
PILOTS = {
    "K000389": "khanna",     # Ro Khanna
    "S001185": "sewell",     # Terri Sewell
    "M001153": "murkowski",  # Lisa Murkowski
    "M001136": "mcclain",    # Lisa McClain
}

PILOT_INFO = {
    "khanna": {
        "name": "Ro Khanna",
        "party": "Democrat",
        "state": "CA",
        "chamber": "House",
        "committees": ["Armed Services", "Oversight & Reform", "Budget"],
        "known_for": "Silicon Valley tech policy, defense modernization, progressive economic reform",
    },
    "sewell": {
        "name": "Terri Sewell",
        "party": "Democrat",
        "state": "AL",
        "chamber": "House",
        "committees": ["Ways and Means", "Select Intelligence"],
        "known_for": "Healthcare access, voting rights, rural broadband, intelligence oversight",
    },
    "murkowski": {
        "name": "Lisa Murkowski",
        "party": "Republican",
        "state": "AK",
        "chamber": "Senate",
        "committees": ["Appropriations", "Energy & Natural Resources", "HELP"],
        "known_for": "Energy policy, Alaska resource extraction, nuclear energy, bipartisan dealmaking",
    },
    "mcclain": {
        "name": "Lisa McClain",
        "party": "Republican",
        "state": "MI",
        "chamber": "House",
        "committees": ["Armed Services","Oversight & Reform","Transportation & Infrastructure"],
        "known_for": "Defense spending, Michigan manufacturing, infrastructure policy",
    },
}

# ── Values / infra tagging ───────────────────────────────────────────────────
ALUES_TAGS = {
    "Healthcare equity":   ["UNH","CVS","ABBV","HCA","ABC","MCK","AET","CI","HUM","MOH","CNC"],
    "Clean energy":        ["NEE","DTE","AEP","SO","ED","PCG","XEL","WEC","ETR","CNP"],
    "Clean nuclear":       ["CEG","CCJ","NNE","SMR","BWXT","BWX"],
    "AI infrastructure":   ["NVDA","AMD","INTC","MRVL","AVGO","QCOM","MU","AMAT","LRCX","KLAC"],
    "Semi sovereignty":    ["TSM","AMAT","ASML","LRCX","KLAC","MU","ON","TER"],
    "Grid infrastructure": ["EATON","ETN","HUBB","GNRC","VST","NRG","AES","BKR","FLS"],
    "Healthcare access":   ["ABBV","BMY","PFE","MRK","JNJ","LLY","REGN","GILD","AMGN","BIIB"],
    "Housing":             ["DHI","LEN","PHM","TOL","NVR","MDC","TMHC","MHO","SKY","CVCO"],
    "Defense tech":        ["LMT","RTX","NOC","GD","BAH","CACI","SAIC","LDOS","HII","TDG"],
}

INFRA_TICKERS = {
    "NVDA","AMD","INTC","MRVL","AVGO","QCOM","MU","AMAT","LRCX","KLAC","TSM","ASML","BWX","BWXT",
    "NNE","SMR","CCJ","NRG","VST","AES","HUBB","GNRC","SMCI","HPE",
}

# ── Config from environment ──────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SENDGRID_API_KEY  = os.environ.get("SENDGRID_API_KEY", "")
NOTIFY_EMAIL      = os.environ.get("NOTIFY_EMAIL", "raiyakind@gmail.com")
FROM_EMAIL        = os.environ.get("FROM_EMAIL", "raiyakind@gmail.com")
DASHBOARD_URL     = "https://boisterous-lolly-67f801.netlify.app"

BASE_URL = "https://www.capitoltrades.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

RSC_PATTERN = re.compile(
    r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)',
    re.DOTALL
)

# ── Helpers ──────────────────────────────────────────────────────────────────
def get_values_tag(ticker):
    ticker = ticker.upper()
    for tag, tickers in VALUES_TAGS.items():
        if ticker in tickers:
            return tag
    return None

def is_infra(ticker):
    return ticker.upper() in INFRA_TICKERS

def format_amount(value):
    if not value: return "Unknown"
    try: v = int(value)
    except: return str(value)
    if v <= 1000:    return "$1K"
    if v <= 15000:   return "$1K-$15K"
    if v <= 50000:   return "$15K-$50K"
    if v <= 100000:  return "$50K-$100K"
    if v <= 250000:  return "$100K-$250K"
    if v <= 500000:  return "$250K-$500K"
    if v <= 1000000: return "$500K-$1M"
    return "$1M+"

def _is_new(pub_date):
    if not pub_date: return False
    try:
        d = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - d).days <= 7
    except: return False

# ── Capitol Trades scraping ──────────────────────────────────────────────────
def extract_trades_from_html(html):
    decoder = json.JSONDecoder()
    trades = []
    for match in RSC_PATTERN.finditer(html):
        raw_str = match.group(1)
        try: decoded = json.loads('"' + raw_str + '"')
        except: continue
        for m in re.finditer(r'\{"_issuerId":', decoded):
            try:
                obj, _ = decoder.raw_decode(decoded, m.start())
                if "_txId" in obj and "txDate" in obj:
                    trades.append(obj)
            except: pass
    return trades

def fetch_pilot_trades(bioguide_id, pilot_key):
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
        if t: results.append(t)
    print(f"  {len(results)} valid trades after normalisation")
    return results

def normalise_trade(raw, pilot_key, idx):
    issuer = raw.get("issuer") or {}
    ticker_raw = issuer.get("issuerTicker") or ""
    ticker = ticker_raw.split(":")[0].upper().strip()
    if not ticker or ticker in ("N/A", ""): return None
    tx_type = (raw.get("txType") or "").lower()
    if tx_type not in ("buy", "sell", "call", "put", "exchange"): tx_type = "buy"
    pub_date = (raw.get("pubDate") or "")
    filed_date = pub_date[:10] if pub_date else ""
    return {
        "id":        f"ct{raw.get('_txId', idx)}",
        "pilot":     pilot_key,
        "ticker":    ticker,
        "company":   issuer.get("issuerName") or ticker,
        "type":      tx_type,
        "amount":    format_amount(raw.get("value")),
        "transDate": raw.get("txDate") or "",
        "filedDate": filed_date,
        "isNew":     _is_new(pub_date),
        "values":    get_values_tag(ticker),
        "infra":     is_infra(ticker),
        "chamber":   raw.get("chamber") or "house",
        "analysis":  None,
    }

# ── AI analysis via Claude Haiku ─────────────────────────────────────────────
def generate_analysis(trade):
    if not ANTHROPIC_API_KEY:
        print("  No ANTHROPIC_API_KEY - skipping analysis")
        return ""

    info = PILOT_INFO.get(trade.get("pilot", ""), {})
    name = info.get("name", trade.get("pilot", "Unknown"))
    committees = ", ".join(info.get("committees", []))
    known_for = info.get("known_for", "")

    prompt = f"""You are a policy and markets analyst covering congressional trading disclosures.

Analyze this STOCK Act disclosure:
- Politician: {name} ({info.get('party','')}, {info.get('state','')}) - {info.get('chamber','')}
- Committees: {committees}
- Known for: {known_for}
- Transaction: {trade.get('type','').upper()} {trade.get('ticker')} ({trade.get('company')})
- Reported amount: {trade.get('amount')}
- Transaction date: {trade.get('transDate')} | Filed: {trade.get('filedDate')}
- Signal values tag: {trade.get('values') or 'None tagged'}

Write 3 tight paragraphs:
1. Why this politician likely has informational edge on this company or sector - tie to their committees, district, or policy track record.
2. Current legislative or macro environment relevant to this trade - be specific about bills, regulatory moves, or sector trends.
3. Investment thesis: is this a bullish signal to follow? Near-term catalyst and longer-term structural story.

Be direct and specific. No hedging. Under 220 words total."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"  Analysis error for {trade.get('ticker')}: {e}")
        return ""

# ── Email via SendGrid ────────────────────────────────────────────────────────
def send_email_digest(new_trades):
    if not SENDGRID_API_KEY or not new_trades:
        return

    cards = ""
    for t in new_trades:
        info = PILOT_INFO.get(t.get("pilot", ""), {})
        name = info.get("name", t.get("pilot", "Unknown"))
        is_buy = t.get("type") == "buy"
        action_color = "#16a34a" if is_buy else "#dc2626"
        action_bg    = "#f0fdf4" if is_buy else "#fef2f2"

        values_badge = ""
        if t.get("values"):
            values_badge = f'<span style="display:inline-block;background:#dbeafe;color:#1e40af;border-radius:4px;padding:2px 8px;font-size:12px;margin-top:6px">{t["values"]}</span>'

        analysis_html = ""
        if t.get("analysis"):
            paragraphs = t["analysis"].replace("\r\n", "\n").split("\n\n")
            analysis_html = "".join(f"<p style='margin:8px 0;line-height:1.6'>{p}</p>" for p in paragraphs if p.strip())

        cards += f"""
<div style="border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;margin:20px 0">
  <div style="background:{action_bg};padding:14px 18px;border-bottom:1px solid #e5e7eb">
    <div style="display:flex;justify-content:space-between;align-items:flex-start">
      <strong style="font-size:15px">{name}</strong>
      <span style="background:{action_color};color:#fff;border-radius:5px;padding:2px 10px;font-size:12px;font-weight:700">{t.get('type','').upper()}</span>
    </div>
    <div style="font-size:20px;font-weight:700;margin-top:6px">{t.get('ticker')} <span style="font-size:14px;font-weight:400;color:#6b7280">{t.get('company')}</span></div>
    <div style="font-size:13px;color:#6b7280;margin-top:4px">{t.get('amount')} &nbsp;·&nbsp; Traded {t.get('transDate')} &nbsp;·&nbsp; Filed {t.get('filedDate')}</div>
    {values_badge}
  </div>
  <div style="padding:16px 18px;background:#fafafa;font-family:sans-serif">
    <div style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">AI Analysis</div>
    <div style="font-size:14px;color:#111">{analysis_html or '<p style="color:#9ca3af;margin:0">Analysis unavailable.</p>'}</div>
  </div>
</div>"""

    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:20px;background:#f3f4f6;font-family:sans-serif">
<div style="max-width:640px;margin:0 auto">
  <div style="background:#111;color:#fff;border-radius:10px;padding:20px 24px;margin-bottom:4px">
    <div style="font-size:22px;font-weight:800;letter-spacing:-.5px">Signal</div>
    <div style="color:#9ca3af;font-size:14px;margin-top:4px">{len(new_trades)} new congressional trade{'s' if len(new_trades)!=1 else ''} detected</div>
  </div>
  {cards}
  <div style="text-align:center;margin-top:28px">
    <a href="{DASHBOARD_URL}" style="background:#2563eb;color:#fff;text-decoration:none;padding:12px 28px;border-radius:7px;font-size:14px;font-weight:700;display:inline-block">View Dashboard</a>
  </div>
  <p style="color:#d1d5db;font-size:11px;text-align:center;margin-top:20px">Signal &nbsp;·&nbsp; Congressional trading intelligence &nbsp;·&nbsp; Data: capitoltrades.com</p>
</div>
</body></html>"""

    payload = {
        "personalizations": [{"to": [{"email": NOTIFY_EMAIL}]}],
        "from": {"email": FROM_EMAIL, "name": "Signal"},
        "subject": f"Signal: {len(new_trades)} new congressional trade{'s' if len(new_trades)!=1 else ''} detected",
        "content": [{"type": "text/html", "value": html}],
    }

    try:
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        print(f"  Email sent to {NOTIFY_EMAIL} - status {resp.status_code}")
        if resp.status_code >= 400:
            print(f"  SendGrid error: {resp.text}")
    except Exception as e:
        print(f"  Email send error: {e}")

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    # Load existing trades to preserve analyses and detect new entries
    existing_by_id = {}
    try:
        with open("trades.json") as f:
            existing_data = json.load(f)
            for t in existing_data.get("trades", []):
                existing_by_id[t["id"]] = t
        print(f"Loaded {len(existing_by_id)} existing trades from trades.json")
    except (FileNotFoundError, json.JSONDecodeError):
        print("No existing trades.json - starting fresh")

    # Fetch fresh trades from Capitol Trades
    all_trades = []
    for bioguide_id, pilot_key in PILOTS.items():
        trades = fetch_pilot_trades(bioguide_id, pilot_key)
        all_trades.extend(trades)
    all_trades.sort(key=lambda t: t.get("transDate") or "", reverse=True)

    # Identify truly new trades
    new_trade_ids = set(t["id"] for t in all_trades) - set(existing_by_id.keys())
    new_trades = []
    print(f"\nMatched {len(all_trades)} total trades - {len(new_trade_ids)} new")

    # Generate / preserve analyses
    for trade in all_trades:
        existing = existing_by_id.get(trade["id"])
        if existing and existing.get("analysis"):
            # Carry forward existing analysis - don't regenerate
            trade["analysis"] = existing["analysis"]
        else:
            # New or previously unanalyzed trade - generate now
            print(f"  Analysing {trade['ticker']} ({trade['pilot']})...")
            trade["analysis"] = generate_analysis(trade)
            time.sleep(0.4)  # gentle rate limiting

        if trade["id"] in new_trade_ids:
            new_trades.append(trade)

    # Email digest for genuinely new trades
    if new_trades:
        print(f"\nSending email digest for {len(new_trades)} new trades...")
        send_email_digest(new_trades)
    else:
        print("\nNo new trades - skipping email")

    # Write output
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
