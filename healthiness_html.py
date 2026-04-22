#!/usr/bin/env python3
"""Generate a heuristic healthiness index HTML dashboard."""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "export" / "receipts.db"
TAXONOMY_PATH = (
    BASE_DIR / "export" / "product_taxonomy.json"
)
OUTPUT_PATH = BASE_DIR / "export" / "healthiness.html"

BASELINE_MONTHS = ["2022-07", "2022-08", "2022-09"]
HEALTHY_SHARE_THRESHOLD = 0.70

CATEGORY_BASE_SCORES = {
    "fruit": 0.95,
    "vegetables": 1.00,
    "plant_based_alternatives": 0.82,
    "dairy": 0.58,
    "beverages": 0.52,
    "pantry": 0.45,
    "bakery": 0.38,
    "meat": 0.33,
    "ready_meals": 0.18,
    "sweets_snacks": 0.08,
    "household": None,
    "fees": None,
}

PRODUCT_TYPE_SCORES = {
    "banana_kg": 1.00,
    "banana_bunch": 1.00,
    "apple_kg": 0.95,
    "narancs_kg": 0.92,
    "rolled_oats": 0.90,
    "bulgur": 0.85,
    "hummus": 0.88,
    "walnuts": 0.86,
    "eggs": 0.72,
    "cow_milk": 0.58,
    "oat_milk": 0.82,
    "greek_yogurt": 0.68,
    "sour_cream": 0.42,
    "gouda_cheese": 0.42,
    "trappista_cheese": 0.42,
    "mozzarella": 0.54,
    "wholemeal_toast_bread": 0.68,
    "fruit_muesli": 0.52,
    "instant_mix": 0.25,
    "mineral_water": 1.00,
    "szensavm_a_viz": 0.95,
    "almale": 0.38,
    "narancsle": 0.32,
    "kavetejszin": 0.55,
    "rice_pudding_cinnamon": 0.18,
    "rice_pudding_chocolate": 0.16,
    "rice_pudding_cherry": 0.16,
    "rice_pudding_strawberry": 0.16,
    "apple_triangle_pastry": 0.10,
    "cheese_puff_pastry": 0.12,
    "cinnamon_pastry_roll": 0.12,
    "cinnamon_roll": 0.12,
    "jalapeno_pastry_roll": 0.12,
    "plum_pastry": 0.12,
    "poppy_seed_guba": 0.12,
    "blueberry_cheesecake": 0.05,
    "strawberry_ice_cream_bar": 0.05,
    "lasagne_bolognese": 0.25,
    "cooked_ham": 0.25,
    "ham": 0.25,
    "poultry_sausage": 0.28,
    "chicken": 0.48,
    "deposit_fee": None,
    "paper_tissues": None,
    "shopping_bag": None,
}


@dataclass
class MonthSummary:
    month: str
    considered_spend: float
    raw_score: float | None
    healthy_share: float | None
    index: float | None


def fmt_money(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}".replace(",", " ") + " Ft"


def fmt_number(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}".replace(",", " ")


def fmt_percent(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.1f}%"


def safe_json(value) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def month_range(start: str, end: str) -> list[str]:
    year, month = map(int, start.split("-"))
    end_year, end_month = map(int, end.split("-"))
    months = []
    while (year, month) <= (end_year, end_month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return months


def query_rows(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(conn.execute(query, params))


def load_taxonomy() -> dict:
    if not TAXONOMY_PATH.exists():
        raise FileNotFoundError(f"Taxonomy not found: {TAXONOMY_PATH}")
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.upper()


def item_score(category: str, product_type: str, description: str) -> float | None:
    if product_type in PRODUCT_TYPE_SCORES:
        return PRODUCT_TYPE_SCORES[product_type]
    return CATEGORY_BASE_SCORES.get(category, 0.40)


def article_amount(quantity: float | None, unit_price: float) -> float:
    return unit_price * (quantity if quantity is not None else 1.0)


def linear_trend(values: list[float | None]) -> list[float | None]:
    points = [(idx, float(value)) for idx, value in enumerate(values) if value is not None]
    if len(points) < 2:
        return [None for _ in values]

    n = len(points)
    sum_x = sum(x for x, _ in points)
    sum_y = sum(y for _, y in points)
    sum_xx = sum(x * x for x, _ in points)
    sum_xy = sum(x * y for x, y in points)
    denom = n * sum_xx - sum_x * sum_x
    if denom == 0:
        return [None for _ in values]

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return [
        None if value is None else round(intercept + slope * idx, 2)
        for idx, value in enumerate(values)
    ]


def build_data() -> dict:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    taxonomy = load_taxonomy()
    item_lookup = {
        item["description"]: {
            "category": item["category"],
            "product_type": item["product_type"],
        }
        for item in taxonomy["items"]
    }
    categories = taxonomy["categories"]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        latest_month_row = query_rows(
            conn,
            "SELECT MAX(substr(date, 1, 7)) AS ym FROM receipts WHERE date >= '2021-08-01'",
        )[0]
        latest_month = latest_month_row["ym"]
        if latest_month is None:
            raise RuntimeError("No receipts found")
        rows = query_rows(
            conn,
            """
            SELECT substr(r.date, 1, 7) AS ym,
                   a.description AS description,
                   a.quantity AS quantity,
                   a.unit_price AS unit_price
            FROM articles a
            JOIN receipts r ON r.id = a.receipt_id
            WHERE r.date >= '2021-08-01'
              AND a.unit_price IS NOT NULL
            ORDER BY ym
            """,
        )
    finally:
        conn.close()

    monthly: dict[str, dict] = defaultdict(
        lambda: {
            "considered_spend": 0.0,
            "weighted_spend": 0.0,
            "healthy_spend": 0.0,
            "category_spend": defaultdict(float),
            "category_weighted": defaultdict(float),
        }
    )

    for row in rows:
        meta = item_lookup.get(row["description"])
        if not meta:
            continue
        score = item_score(meta["category"], meta["product_type"], row["description"])
        if score is None:
            continue
        amount = article_amount(float(row["quantity"]) if row["quantity"] is not None else None, float(row["unit_price"]))
        if amount <= 0:
            continue
        ym = row["ym"]
        monthly[ym]["considered_spend"] += amount
        monthly[ym]["weighted_spend"] += amount * score
        if score >= HEALTHY_SHARE_THRESHOLD:
            monthly[ym]["healthy_spend"] += amount
        monthly[ym]["category_spend"][meta["category"]] += amount
        monthly[ym]["category_weighted"][meta["category"]] += amount * score

    all_months = month_range("2021-08", latest_month)
    month_summaries: list[MonthSummary] = []
    baseline_raw_scores = []
    for month in all_months:
        info = monthly.get(month)
        considered = float(info["considered_spend"]) if info else 0.0
        raw_score = None
        healthy_share = None
        if info and considered > 0:
            raw_score = info["weighted_spend"] / considered * 100
            healthy_share = info["healthy_spend"] / considered * 100
        month_summaries.append(
            MonthSummary(
                month=month,
                considered_spend=considered,
                raw_score=raw_score,
                healthy_share=healthy_share,
                index=None,
            )
        )
        if month in BASELINE_MONTHS and raw_score is not None:
            baseline_raw_scores.append(raw_score)

    if not baseline_raw_scores:
        raise RuntimeError("Could not compute baseline healthiness score")
    baseline_raw_score = mean(baseline_raw_scores)

    for summary in month_summaries:
        if summary.raw_score is not None:
            summary.index = summary.raw_score / baseline_raw_score * 100

    latest_summary = next((s for s in reversed(month_summaries) if s.raw_score is not None), None)
    if latest_summary is None:
        raise RuntimeError("No monthly healthiness summaries were produced")

    latest_info = monthly[latest_summary.month]
    latest_category_rows = []
    for category, spend in sorted(
        latest_info["category_spend"].items(), key=lambda item: (-item[1], item[0])
    ):
        weighted = latest_info["category_weighted"][category]
        raw_score = weighted / spend * 100 if spend else None
        latest_category_rows.append(
            {
                "category": category,
                "spend": spend,
                "share": spend / latest_summary.considered_spend * 100 if latest_summary.considered_spend else None,
                "raw_score": raw_score,
                "weighted": weighted,
            }
        )

    group_overrides = [
        {"product_type": key, "score": value}
        for key, value in PRODUCT_TYPE_SCORES.items()
        if value is not None and key in {
            "banana_kg",
            "banana_bunch",
            "apple_kg",
            "rolled_oats",
            "bulgur",
            "hummus",
            "walnuts",
            "cow_milk",
            "oat_milk",
            "greek_yogurt",
            "sour_cream",
            "gouda_cheese",
            "trappista_cheese",
            "wholemeal_toast_bread",
            "fruit_muesli",
            "instant_mix",
            "mineral_water",
            "szensavm_a_viz",
            "almale",
            "narancsle",
            "lasagne_bolognese",
        }
    ]

    return {
        "months": [s.month for s in month_summaries],
        "raw_scores": [s.raw_score for s in month_summaries],
        "health_index": [s.index for s in month_summaries],
        "health_index_trend": linear_trend([s.index for s in month_summaries]),
        "healthy_share": [s.healthy_share for s in month_summaries],
        "considered_spend": [s.considered_spend for s in month_summaries],
        "latest": latest_summary,
        "baseline_raw_score": baseline_raw_score,
        "baseline_months": BASELINE_MONTHS,
        "latest_category_rows": latest_category_rows[:10],
        "category_scores": [
            {"category": category, "score": score}
            for category, score in CATEGORY_BASE_SCORES.items()
            if score is not None
        ],
        "group_overrides": group_overrides,
        "categories": categories,
    }


def render_html(data: dict) -> str:
    latest = data["latest"]
    cards = [
        ("Latest health index", f"{latest.index:.1f}" if latest.index is not None else "—"),
        ("Latest raw score", f"{latest.raw_score:.1f}%" if latest.raw_score is not None else "—"),
        ("Latest healthy share", f"{latest.healthy_share:.1f}%" if latest.healthy_share is not None else "—"),
        ("Considered spend", fmt_money(latest.considered_spend)),
    ]
    card_html = "".join(
        f"""
        <div class="card">
          <div class="label">{html.escape(label)}</div>
          <div class="value">{html.escape(value)}</div>
        </div>
        """
        for label, value in cards
    )

    category_rows = [
        f"<tr><td>{html.escape(row['category'])}</td><td>{row['score']:.2f}</td></tr>"
        for row in data["category_scores"]
    ]
    override_rows = [
        f"<tr><td>{html.escape(row['product_type'])}</td><td>{row['score']:.2f}</td></tr>"
        for row in data["group_overrides"]
    ]
    latest_category_rows = []
    for row in data["latest_category_rows"]:
        latest_category_rows.append(
            "<tr>"
            f"<td>{html.escape(row['category'])}</td>"
            f"<td>{fmt_money(row['spend'])}</td>"
            f"<td>{fmt_percent(row['share'])}</td>"
            f"<td>{fmt_percent(row['raw_score'])}</td>"
            "</tr>"
        )

    template = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lidl Plus Healthiness Index</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {{
      --bg: #0b1020;
      --panel: #131a2e;
      --border: rgba(148,163,184,.18);
      --text: #ecf2ff;
      --muted: #94a3b8;
      --accent: #7dd3fc;
      --accent-2: #34d399;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      background: linear-gradient(180deg, #08101e 0%, #0d1426 100%);
      color: var(--text);
    }}
    .wrap {{ max-width: 1400px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; }}
    .sub {{ color: var(--muted); margin-bottom: 22px; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-bottom: 18px; }}
    .card, .panel {{
      background: rgba(19,26,46,.92);
      border: 1px solid var(--border);
      border-radius: 18px;
      box-shadow: 0 12px 28px rgba(0,0,0,.18);
    }}
    .card {{ padding: 18px; }}
    .label {{ color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: .08em; }}
    .value {{ font-size: 28px; font-weight: 700; margin-top: 8px; }}
    .grid {{ display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-top: 16px; }}
    .panel {{ padding: 18px; }}
    .panel h2 {{ margin: 0 0 10px; font-size: 18px; }}
    .panel p {{ margin: 8px 0 0; color: var(--muted); }}
    .chart-box {{ height: 360px; }}
    .section {{ margin-top: 18px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
      font-size: 14px;
    }}
    th, td {{
      padding: 10px 8px;
      border-bottom: 1px solid var(--border);
      text-align: left;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    .note {{ color: var(--muted); font-size: 13px; line-height: 1.5; }}
    @media (max-width: 1100px) {{
      .cards, .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Lidl Plus Healthiness Index</h1>
    <div class="sub">Heuristic category-based score · normalized to Jul-Sep 2022 = 100</div>
    <div class="cards">__CARD_HTML__</div>

    <div class="grid">
      <div class="panel">
        <h2>Healthiness index</h2>
        <div class="chart-box"><canvas id="indexChart"></canvas></div>
        <p>Spend-weighted category score normalized to the Jul-Sep 2022 baseline.</p>
      </div>
      <div class="panel">
        <h2>Raw score and healthy share</h2>
        <div class="chart-box"><canvas id="scoreChart"></canvas></div>
        <p>Raw score is the spend-weighted average health score. Healthy share is spend on items scored at least 0.70.</p>
      </div>
    </div>

    <div class="grid section">
      <div class="panel">
        <h2>Category weights</h2>
        <table>
          <thead><tr><th>Category</th><th>Score</th></tr></thead>
          <tbody>__CATEGORY_ROWS__</tbody>
        </table>
      </div>
      <div class="panel">
        <h2>Key product overrides</h2>
        <table>
          <thead><tr><th>Product group</th><th>Score</th></tr></thead>
          <tbody>__OVERRIDE_ROWS__</tbody>
        </table>
      </div>
    </div>

    <div class="panel section">
      <h2>Latest month breakdown</h2>
      <table>
        <thead>
          <tr>
            <th>Category</th>
            <th>Spend</th>
            <th>Share</th>
            <th>Avg score</th>
          </tr>
        </thead>
        <tbody>
          __LATEST_CATEGORY_ROWS__
        </tbody>
      </table>
    </div>

    <div class="panel section">
      <h2>Method</h2>
      <div class="note">
        Household items and fee rows are excluded. All other items are mapped to a category and optionally overridden by a product-group score.
        Monthly values are weighted by line spend using quantity when available, then normalized to the average of Jul-Sep 2022.
      </div>
    </div>
  </div>

  <script>
    const months = __MONTHS_JSON__;
    const indexValues = __INDEX_VALUES_JSON__;
    const indexTrend = __INDEX_TREND_JSON__;
    const rawScores = __RAW_SCORES_JSON__;
    const healthyShare = __HEALTHY_SHARE_JSON__;
    const spend = __SPEND_JSON__;

    new Chart(document.getElementById('indexChart'), {{
      type: 'line',
      data: {{
        labels: months,
        datasets: [{{
          label: 'Healthiness index',
          data: indexValues,
          borderColor: '#7dd3fc',
          backgroundColor: 'rgba(125, 211, 252, .12)',
          tension: .25,
          borderWidth: 2,
          pointRadius: 2,
        }},
        {{
          label: 'Trendline',
          data: indexTrend,
          borderColor: '#fbbf24',
          borderDash: [6, 6],
          borderWidth: 2,
          pointRadius: 0,
          tension: 0,
          fill: false,
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{ legend: {{ labels: {{ color: '#ecf2ff' }} }} }},
        scales: {{
          x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(148,163,184,.08)' }} }},
          y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(148,163,184,.08)' }} }}
        }}
      }}
    }});

    new Chart(document.getElementById('scoreChart'), {{
      type: 'line',
      data: {{
        labels: months,
        datasets: [
          {{
            label: 'Raw score',
            data: rawScores,
            borderColor: '#fbbf24',
            backgroundColor: 'rgba(251, 191, 36, .12)',
            tension: .25,
            borderWidth: 2,
            pointRadius: 2,
          }},
          {{
            label: 'Healthy share',
            data: healthyShare,
            borderColor: '#34d399',
            backgroundColor: 'rgba(52, 211, 153, .12)',
            tension: .25,
            borderWidth: 2,
            pointRadius: 2,
          }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{ legend: {{ labels: {{ color: '#ecf2ff' }} }} }},
        scales: {{
          x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(148,163,184,.08)' }} }},
          y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(148,163,184,.08)' }} }}
        }}
      }}
    }});
  </script>
</body>
</html>"""
    template = template.replace("{{", "{").replace("}}", "}")
    return (
        template.replace("__CARD_HTML__", card_html)
        .replace("__MONTHS_JSON__", safe_json(data["months"]))
        .replace("__INDEX_VALUES_JSON__", safe_json(data["health_index"]))
        .replace("__INDEX_TREND_JSON__", safe_json(data["health_index_trend"]))
        .replace("__RAW_SCORES_JSON__", safe_json(data["raw_scores"]))
        .replace("__HEALTHY_SHARE_JSON__", safe_json(data["healthy_share"]))
        .replace("__SPEND_JSON__", safe_json(data["considered_spend"]))
        .replace("__CATEGORY_ROWS__", "".join(category_rows))
        .replace("__OVERRIDE_ROWS__", "".join(override_rows))
        .replace("__LATEST_CATEGORY_ROWS__", "".join(latest_category_rows))
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate healthiness HTML")
    parser.add_argument("--output", default=str(OUTPUT_PATH), help="Output HTML file path")
    args = parser.parse_args()

    data = build_data()
    html_output = render_html(data)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_output, encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
