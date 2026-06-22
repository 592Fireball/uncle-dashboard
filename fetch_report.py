"""
Daily Brief — fetch & summarize
--------------------------------
Pulls a quote, recent news, and analyst price targets for every ticker in
companies.json, asks Claude to write a short, plain-English "what's going on"
summary for each one, and saves the result to reports/<date>.json (and
reports/latest.json, which the dashboard reads).

Run manually:
    python fetch_report.py

Run daily on a schedule (cron example, 7am every day):
    0 7 * * * cd /path/to/uncle-dashboard && /usr/bin/python3 fetch_report.py >> log.txt 2>&1

Requires two API keys, set as environment variables (see .env.example):
    FINNHUB_API_KEY     - from finnhub.io (free tier: 60 calls/min, quotes + news)
    ANTHROPIC_API_KEY   - from console.anthropic.com

Analyst price targets come from Yahoo Finance via the yfinance library —
no API key needed for that part, it's free and unofficial but reliable.
"""

import json
import os
import sys
import time
from datetime import date, timedelta

import requests
import anthropic
import yfinance as yf

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

FINNHUB_BASE = "https://finnhub.io/api/v1"
REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")
COMPANIES_FILE = os.path.join(os.path.dirname(__file__), "companies.json")

DISCLAIMER = (
    "Informational summary only, generated from automated data and news — "
    "not financial advice. Verify anything important before acting on it."
)


def load_companies():
    with open(COMPANIES_FILE) as f:
        return json.load(f)


def get_quote(ticker):
    url = f"{FINNHUB_BASE}/quote"
    resp = requests.get(url, params={"symbol": ticker, "token": FINNHUB_API_KEY}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    # Finnhub returns all zeros for an invalid/unsupported symbol instead of an error
    if not data or data.get("c") in (None, 0):
        return None
    return data


def get_company_name(ticker):
    url = f"{FINNHUB_BASE}/stock/profile2"
    resp = requests.get(url, params={"symbol": ticker, "token": FINNHUB_API_KEY}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("name", ticker) if data else ticker


def get_news(ticker, days_back=4):
    url = f"{FINNHUB_BASE}/company-news"
    end = date.today()
    start = end - timedelta(days=days_back)
    resp = requests.get(
        url,
        params={
            "symbol": ticker,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "token": FINNHUB_API_KEY,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_price_target(ticker):
    """Analyst price target consensus, via Yahoo Finance (free, no key needed)."""
    try:
        info = yf.Ticker(ticker).info
        mean = info.get("targetMeanPrice")
        if not mean:
            return None
        return {
            "mean": mean,
            "high": info.get("targetHighPrice"),
            "low": info.get("targetLowPrice"),
            "numAnalysts": info.get("numberOfAnalystOpinions"),
            "recommendation": info.get("recommendationKey"),  # e.g. "buy", "hold", "sell"
        }
    except Exception:
        return None


def summarize(client, ticker, quote, news, price_target):
    headlines = "\n".join(f"- {n.get('headline','')}" for n in news[:5]) or "No recent news found."

    target_line = "Not available."
    if price_target:
        target_line = (
            f"Mean target ${price_target['mean']} (range ${price_target['low']}-"
            f"${price_target['high']}, {price_target['numAnalysts']} analysts, "
            f"consensus rating: {price_target['recommendation']})"
        )

    prompt = f"""You're writing one short paragraph for a daily investing brief about {ticker}.

Price data:
- Current price: {quote.get('c')}
- Change: {quote.get('d')} ({quote.get('dp')}%)
- Day range: {quote.get('l')} - {quote.get('h')}
- Previous close: {quote.get('pc')}

Analyst price target consensus: {target_line}

Recent headlines:
{headlines}

Write 2-3 plain-English sentences: what's notable today, and why it might matter to someone
tracking this stock. If there's an analyst price target, mention how the current price compares
to it. Mention both upside and downside considerations if relevant. Do NOT phrase this as a
directive ("buy"/"sell") recommendation — describe what's happening and the considerations, and
let the reader draw their own conclusion. No preamble, just the paragraph."""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def main():
    if not FINNHUB_API_KEY or not ANTHROPIC_API_KEY:
        sys.exit(
            "Missing API keys. Set FINNHUB_API_KEY and ANTHROPIC_API_KEY as environment "
            "variables (see .env.example)."
        )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    companies = load_companies()
    results = []

    for ticker in companies:
        print(f"Fetching {ticker}...")
        try:
            quote = get_quote(ticker)
            if not quote:
                print(f"  no quote data for {ticker}, skipping")
                continue
            name = get_company_name(ticker)
            news = get_news(ticker)
            price_target = get_price_target(ticker)
            ai_summary = summarize(client, ticker, quote, news, price_target)

            results.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "price": quote.get("c"),
                    "change": quote.get("d"),
                    "changePercent": quote.get("dp"),
                    "priceTarget": price_target,
                    "summary": ai_summary,
                    "news": [
                        {"title": n.get("headline"), "url": n.get("url"), "site": n.get("source")}
                        for n in news[:3]
                    ],
                }
            )
        except Exception as e:
            print(f"  error on {ticker}: {e}")
        time.sleep(1.1)  # Finnhub free tier: 60 calls/min, stay comfortably under that

    report = {
        "date": str(date.today()),
        "disclaimer": DISCLAIMER,
        "companies": results,
    }

    os.makedirs(REPORTS_DIR, exist_ok=True)
    dated_path = os.path.join(REPORTS_DIR, f"{report['date']}.json")
    latest_path = os.path.join(REPORTS_DIR, "latest.json")

    with open(dated_path, "w") as f:
        json.dump(report, f, indent=2)
    with open(latest_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nDone. Wrote {len(results)} companies to {dated_path}")


if __name__ == "__main__":
    main()
