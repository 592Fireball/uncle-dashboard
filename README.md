# The Daily Brief — uncle's stock dashboard

A daily report on 25 (expandable) companies: price moves, recent news, and a
plain-English AI summary for each. Runs once a day, displays on a simple web
dashboard.

## How it works

1. `companies.json` — the list of tickers to track. Edit this to add/remove companies.
2. `fetch_report.py` — pulls price + news for each ticker, asks Claude to summarize,
   saves the result to `reports/latest.json`.
3. `dashboard.html` — reads `reports/latest.json` and displays it as cards.

## One-time setup

1. Get a free API key from [finnhub.io](https://finnhub.io) (FINNHUB_API_KEY). Free tier: 60 calls/minute, includes real-time US quotes and company news — no paywalled endpoints to worry about.
2. Get an API key from [console.anthropic.com](https://console.anthropic.com) (ANTHROPIC_API_KEY).
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
   This also installs `yfinance`, which pulls analyst price targets from Yahoo Finance — free, no API key needed for that part.
4. Set your keys as environment variables (or copy `.env.example` to `.env` and load it):
   ```
   export FINNHUB_API_KEY=your_key_here
   export ANTHROPIC_API_KEY=your_key_here
   ```

## Run it

Generate today's report:
```
python fetch_report.py
```

View the dashboard (must be served, not opened directly as a file, so it can fetch the JSON):
```
python -m http.server 8000
```
Then open `http://localhost:8000/dashboard.html` in a browser.

## Automate it (run daily without you doing anything)

Add a cron job (Mac/Linux) so it runs every morning at 7am:
```
crontab -e
```
Add this line (edit the path to match where you put this folder):
```
0 7 * * * cd /path/to/uncle-dashboard && /usr/bin/python3 fetch_report.py >> log.txt 2>&1
```

## Adding companies later

For now: open `companies.json`, add the ticker symbol (e.g. `"NEW"`), save,
rerun `fetch_report.py`. It'll show up on the dashboard next refresh.

A self-service "add a company" box in the dashboard itself is a natural next
step once this is running — it just needs a tiny backend endpoint to write
back to `companies.json`, which we can add once the core pipeline is solid.

## Important note on the AI summaries

These are generated from automated data and recent news — they describe
what's happening, not "buy/sell" directives. Treat them as a starting point
for your uncle's own research, not investment advice. This is flagged in the
dashboard itself via the disclaimer line.

## Cost

- Finnhub free tier: 60 calls/minute, no daily cap mentioned for these endpoints. This pipeline uses 3 calls per company (quote, profile, news) — about 75 for 25 companies, taking under 2 minutes to run.
- Anthropic API: charged per use, roughly fractions of a cent per company summary. For 25 companies/day this should run well under $5/month.
