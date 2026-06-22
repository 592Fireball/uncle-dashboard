"""
Daily Brief — fetch & summarize
--------------------------------
Pulls a quote, recent news, and analyst price targets for every ticker in
companies.json, asks Claude to write a short, plain-English "what's going on"
summary for each one (with explicit price-vs-target comparison), and saves the
result to reports/<date>.json (and reports/latest.json, which the dashboard reads).

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
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from urllib.parse import quote as urlquote

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


def get_news_google(ticker, name="", limit=4):
    """
    Fetches news from Google News RSS feed — no API key needed.
    Returns articles from diverse sources like Reuters, CNBC, MarketWatch, Bloomberg.
    Each item has: title, url, site.
    """
    try:
        query = urlquote(f"{ticker} {name} stock".strip())
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = root.findall(".//item")[:limit]
        news = []
        for item in items:
            title = item.findtext("title", "").split(" - ")[0].strip()  # strip appended source from title
            link = item.findtext("link", "")
            source_el = item.find("source")
            site = source_el.text if source_el is not None else ""
            if title:
                news.append({"title": title, "url": link, "site": site})
        return news
    except Exception:
        return []


def get_analyst_actions(ticker, limit=5):
    """Recent analyst upgrades/downgrades with firm names, via Yahoo Finance."""
    try:
        df = yf.Ticker(ticker).upgrades_downgrades
        if df is None or df.empty:
            return []
        recent = df.head(limit)
        actions = []
        for idx, row in recent.iterrows():
            date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
            actions.append({
                "date": date_str,
                "firm": row.get("Firm", ""),
                "toGrade": row.get("To Grade", ""),
                "fromGrade": row.get("From Grade", ""),
                "action": row.get("Action", ""),
            })
        return actions
    except Exception:
        return []


def summarize(client, ticker, quote, news, price_target):
    # news items may come from Finnhub (headline key) or Google RSS (title key)
    headlines = "\n".join(
        f"- {n.get('title') or n.get('headline', '')}" for n in news[:5]
    ) or "No recent news found."

    current_price = quote.get("c")
    price_change = quote.get("d")
    price_change_pct = quote.get("dp")

    # Build target comparison section
    target_comparison = ""
    if price_target and price_target.get("mean"):
        target = price_target["mean"]
        diff = current_price - target
        diff_pct = (diff / target * 100) if target else 0
        
        if abs(diff_pct) < 2:
            comparison = f"trading near its analyst target of ${target:.2f}"
        elif diff > 0:
            comparison = f"trading ${diff:.2f} ({diff_pct:.1f}%) ABOVE its analyst target of ${target:.2f}"
        else:
            comparison = f"trading ${abs(diff):.2f} ({abs(diff_pct):.1f}%) BELOW its analyst target of ${target:.2f}"
        
        target_comparison = f"\nPrice vs. Analyst Target: {comparison} ({price_target['numAnalysts']} analysts, consensus: {price_target['recommendation']})"

    prompt = f"""You're writing one short paragraph for a daily investing brief about {ticker}.

TODAY'S MOVEMENT:
- Current price: ${current_price}
- Change: ${price_change:+.2f} ({price_change_pct:+.2f}%)
- Day range: {quote.get('l')} - {quote.get('h')}

ANALYST TARGET DATA:{target_comparison}

RECENT HEADLINES:
{headlines}

Write 2-3 sentences that answer these questions in order:
1. How is the stock trading TODAY relative to the analyst target price? Is it above, below, or near the target?
   What does that positioning mean?
2. What happened today or recently (from the news) that moved it?
3. What's the overall picture for someone tracking this stock?

Do NOT say "buy" or "sell" — just describe the situation and let the reader decide. Make the 
price-vs-target comparison explicit and prominent."""

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
            # Finnhub gives company-specific news; Google RSS gives diverse sources.
            # We normalize both to {title, url, site} and merge them.
            finnhub_news = [
                {"title": n.get("headline", ""), "url": n.get("url", ""), "site": n.get("source", "")}
                for n in get_news(ticker)
                if n.get("headline")
            ]
            google_news = get_news_google(ticker, name)
            # Take up to 2 from Finnhub + up to 3 from Google, total ~5
            merged_news = finnhub_news[:2] + google_news[:3]
            price_target = get_price_target(ticker)
            analyst_actions = get_analyst_actions(ticker)
            ai_summary = summarize(client, ticker, quote, merged_news, price_target)

            results.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "price": quote.get("c"),
                    "change": quote.get("d"),
                    "changePercent": quote.get("dp"),
                    "priceTarget": price_target,
                    "analystActions": analyst_actions,
                    "summary": ai_summary,
                    "news": merged_news[:5],
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
