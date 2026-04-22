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

1. Copy the example env file:

```bash
cp .env.example .env
```

2. Edit `.env`:

- `LANGUAGE` — UI language used by the Lidl login flow (`hu` by default)
- `COUNTRY` — country code used by the Lidl login flow (`HU` by default)
- `REFRESH_TOKEN` — optional at first; if you already have one, paste it here

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Run the auth flow:

```bash
python auth.py
```

If `REFRESH_TOKEN` is empty or expired, `auth.py` opens the browser and guides you through login. It then saves fresh tokens to `.env` and `token.json`.

5. Download tickets and rebuild exports:

```bash
python main.py
python csv_sql_export.py
```

`main.py` reads `token.json`; `csv_sql_export.py` rebuilds the CSV and SQLite exports from `tickets.json`.

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
