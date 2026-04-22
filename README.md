# Lidl Plus

Receipt extraction and grocery analytics for Lidl Plus exports.

## What this repo contains

- `auth.py` — authenticate and refresh tokens
- `main.py` — download tickets using `token.json`
- `csv_sql_export.py` — rebuild CSV/SQLite exports from `tickets.json`
- `analytics_html.py` — spend and basket analytics
- `product_prices_html.py` — banana and milk price charts
- `healthiness_html.py` — heuristic healthiness index

## Setup

```bash
pip install -r requirements.txt
```

Create `.env` with your refresh token and login details, then run:

```bash
python auth.py
python main.py
python csv_sql_export.py
```

## Generated outputs

- `export/receipts.db`
- `export/receipts.csv`
- `export/stores.csv`
- `export/articles.csv`
- `export/analytics.html`
- `export/product_prices.html`
- `export/healthiness.html`
- `export/product_taxonomy.json`

## Notes

- `.env`, `token.json`, `tickets.json`, the virtualenv, and raw export files are ignored by git.
- The dashboards use the committed taxonomy JSON and the SQLite export.
- MS Edge is preferred for auth if available.
