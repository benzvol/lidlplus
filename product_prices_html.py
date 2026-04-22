#!/usr/bin/env python3
"""Generate a product price comparison HTML dashboard."""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "export" / "receipts.db"
TAXONOMY_PATH = (
    BASE_DIR / "export" / "product_taxonomy.json"
)
OUTPUT_PATH = BASE_DIR / "export" / "product_prices.html"

VAT_RATES = {"A": 0.05, "B": 0.18, "C": 0.27}


@dataclass
class SeriesSummary:
    label: str
    first_month: str
    last_month: str
    first_price: float
    last_price: float
    latest_count: int

    @property
    def delta(self) -> float:
        return self.last_price - self.first_price

    @property
    def delta_pct(self) -> float:
        if self.first_price == 0:
            return 0.0
        return (self.last_price / self.first_price - 1) * 100


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


def net_price(value: float, tax_type: str | None) -> float | None:
    vat = VAT_RATES.get(tax_type or "")
    if vat is None:
        return None
    return value / (1 + vat)


def build_series(
    conn: sqlite3.Connection,
    descriptions: list[str],
    *,
    net: bool = False,
) -> list[dict]:
    if not descriptions:
        return []
    placeholders = ",".join("?" for _ in descriptions)
    rows = query_rows(
        conn,
        f"""
        SELECT substr(r.date, 1, 7) AS ym,
               a.unit_price AS unit_price,
               a.tax_type AS tax_type
        FROM articles a
        JOIN receipts r ON r.id = a.receipt_id
        WHERE a.description IN ({placeholders})
          AND a.unit_price IS NOT NULL
        ORDER BY ym
        """,
        tuple(descriptions),
    )
    monthly: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = float(row["unit_price"])
        if net:
            value = net_price(value, row["tax_type"])
            if value is None:
                continue
        monthly[row["ym"]].append(value)
    return [
        {"ym": ym, "avg_price": round(mean(values), 2), "count": len(values)}
        for ym, values in sorted(monthly.items())
    ]


def align_series(months: list[str], series: list[dict]) -> tuple[list[float | None], list[int]]:
    by_month = {row["ym"]: row for row in series}
    values = [by_month.get(ym, {}).get("avg_price") for ym in months]
    counts = [by_month.get(ym, {}).get("count", 0) for ym in months]
    return values, counts


def latest_point(series: list[dict]) -> dict | None:
    for row in reversed(series):
        if row["avg_price"] is not None:
            return row
    return None


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


def build_summary(label: str, series: list[dict]) -> SeriesSummary | None:
    first = next((row for row in series if row["avg_price"] is not None), None)
    last = latest_point(series)
    if not first or not last:
        return None
    return SeriesSummary(
        label=label,
        first_month=first["ym"],
        last_month=last["ym"],
        first_price=float(first["avg_price"]),
        last_price=float(last["avg_price"]),
        latest_count=int(last["count"]),
    )


def group_usage_summary(conn: sqlite3.Connection, descriptions: list[str]) -> dict:
    placeholders = ",".join("?" for _ in descriptions)
    row = query_rows(
        conn,
        f"""
        SELECT COUNT(*) AS occurrences,
               COUNT(DISTINCT a.description) AS raw_items,
               MIN(substr(r.date, 1, 7)) AS first_month,
               MAX(substr(r.date, 1, 7)) AS last_month,
               GROUP_CONCAT(DISTINCT a.tax_type) AS tax_types
        FROM articles a
        JOIN receipts r ON r.id = a.receipt_id
        WHERE a.description IN ({placeholders})
        """,
        tuple(descriptions),
    )[0]
    return {
        "occurrences": int(row["occurrences"]),
        "raw_items": int(row["raw_items"]),
        "first_month": row["first_month"],
        "last_month": row["last_month"],
        "tax_types": row["tax_types"].split(",") if row["tax_types"] else [],
    }


def build_data() -> dict:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    taxonomy = load_taxonomy()
    groups = taxonomy["product_groups"]
    category_counts = taxonomy["category_counts"]
    categories = taxonomy["categories"]
    group_order = ["banana_kg", "cow_milk", "oat_milk"]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        banana_series = build_series(conn, groups["banana_kg"], net=False)
        cow_series = build_series(conn, groups["cow_milk"], net=True)
        oat_series = build_series(conn, groups["oat_milk"], net=True)
        group_summaries = {
            key: group_usage_summary(conn, groups[key]) for key in group_order
        }
    finally:
        conn.close()

    banana_summary = build_summary("banana", banana_series)
    cow_summary = build_summary("cow milk", cow_series)
    oat_summary = build_summary("oat milk", oat_series)

    if not banana_summary or not cow_summary or not oat_summary:
        raise RuntimeError("Missing price history for one of the requested groups")

    banana_months = [row["ym"] for row in banana_series]
    milk_months = month_range(
        min(cow_series[0]["ym"], oat_series[0]["ym"]),
        max(cow_series[-1]["ym"], oat_series[-1]["ym"]),
    )

    banana_values, banana_counts = align_series(banana_months, banana_series)
    banana_trend = linear_trend(banana_values)
    cow_values, cow_counts = align_series(milk_months, cow_series)
    oat_values, oat_counts = align_series(milk_months, oat_series)

    latest_gap = oat_summary.last_price - cow_summary.last_price
    latest_gap_pct = (latest_gap / cow_summary.last_price * 100) if cow_summary.last_price else 0

    selected_groups = []
    for key in group_order:
        selected_groups.append(
            {
                "key": key,
                "category": next(
                    item["category"] for item in taxonomy["items"] if item["product_type"] == key
                ),
                "items": groups[key],
                "summary": group_summaries[key],
                "tax_types": group_summaries[key]["tax_types"],
                "vat_note": "A=5%" if key == "cow_milk" else ("C=27%" if key == "oat_milk" else ""),
            }
        )

    return {
        "banana_months": banana_months,
        "banana_values": banana_values,
        "banana_trend": banana_trend,
        "banana_counts": banana_counts,
        "cow_values": cow_values,
        "cow_counts": cow_counts,
        "oat_values": oat_values,
        "oat_counts": oat_counts,
        "milk_months": milk_months,
        "banana_summary": banana_summary,
        "cow_summary": cow_summary,
        "oat_summary": oat_summary,
        "latest_gap": latest_gap,
        "latest_gap_pct": latest_gap_pct,
        "selected_groups": selected_groups,
        "category_counts": category_counts,
        "categories": categories,
    }


def render_html(data: dict) -> str:
    banana_summary = data["banana_summary"]
    cow_summary = data["cow_summary"]
    oat_summary = data["oat_summary"]

    cards = [
        ("Banana latest", f"{fmt_money(banana_summary.last_price)} / kg"),
        ("Banana change", f"{fmt_money(banana_summary.delta)} ({fmt_percent(banana_summary.delta_pct)})"),
        ("Cow milk latest net", f"{fmt_money(cow_summary.last_price)} / l"),
        ("Oat milk latest net", f"{fmt_money(oat_summary.last_price)} / l"),
        ("Oat premium", f"{fmt_money(data['latest_gap'])} ({fmt_percent(data['latest_gap_pct'])})"),
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

    group_rows = []
    for group in data["selected_groups"]:
        group_rows.append(
            "<tr>"
            f"<td>{html.escape(group['key'])}</td>"
            f"<td>{html.escape(group['category'])}</td>"
            f"<td>{html.escape(', '.join(group['items']))}</td>"
            f"<td>{fmt_number(group['summary']['occurrences'])} · {html.escape(group['summary']['first_month'])}–{html.escape(group['summary']['last_month'])}</td>"
            f"<td>{html.escape(', '.join(group['tax_types']) or '—')}</td>"
            f"<td>{html.escape(group['vat_note'] or '—')}</td>"
            "</tr>"
        )

    category_rows = [
        f"<tr><td>{html.escape(category)}</td><td>{fmt_number(count)}</td></tr>"
        for category, count in sorted(data["category_counts"].items(), key=lambda x: (-x[1], x[0]))
    ]

    template = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lidl Plus Product Prices</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {{
      --bg: #0b1020;
      --panel: #131a2e;
      --border: rgba(148,163,184,.18);
      --text: #ecf2ff;
      --muted: #94a3b8;
      --accent: #7dd3fc;
      --accent-2: #fbbf24;
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
    .cards {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 14px; margin-bottom: 18px; }}
    .card, .panel {{
      background: rgba(19,26,46,.92);
      border: 1px solid var(--border);
      border-radius: 18px;
      box-shadow: 0 12px 28px rgba(0,0,0,.18);
    }}
    .card {{ padding: 18px; }}
    .label {{ color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: .08em; }}
    .value {{ font-size: 27px; font-weight: 700; margin-top: 8px; }}
    .grid {{ display: grid; grid-template-columns: 2fr 2fr; gap: 16px; margin-top: 16px; }}
    .panel {{ padding: 18px; }}
    .panel h2 {{ margin: 0 0 10px; font-size: 18px; }}
    .panel p {{ margin: 8px 0 0; color: var(--muted); }}
    .chart-box {{ height: 370px; }}
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
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    .note {{ color: var(--muted); font-size: 13px; line-height: 1.5; }}
    .small {{ font-size: 12px; color: var(--muted); }}
    @media (max-width: 1200px) {{
      .cards, .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Lidl Plus Product Prices</h1>
    <div class="sub">Banana kg price and cow-vs-oat milk comparison · Aug 2021 – Apr 2026</div>
    <div class="cards">__CARD_HTML__</div>

    <div class="grid">
      <div class="panel">
        <h2>Banana price per kg</h2>
        <div class="chart-box"><canvas id="bananaChart"></canvas></div>
        <p>Gross unit price across the banana_kg group.</p>
      </div>
      <div class="panel">
        <h2>Cow milk vs oat milk</h2>
        <div class="chart-box"><canvas id="milkChart"></canvas></div>
        <p>Net unit price after VAT adjustment: cow milk uses 5% VAT (A), oat milk 27% VAT (C).</p>
      </div>
    </div>

    <div class="grid section">
      <div class="panel">
        <h2>Selected product groups</h2>
        <table>
          <thead>
            <tr>
              <th>Group</th>
              <th>Category</th>
              <th>Raw articles</th>
              <th>Usage</th>
              <th>VAT types</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            __GROUP_ROWS__
          </tbody>
        </table>
      </div>
      <div class="panel">
        <h2>Category overview</h2>
        <table>
          <thead>
            <tr><th>Category</th><th>Items</th></tr>
          </thead>
          <tbody>
            __CATEGORY_ROWS__
          </tbody>
        </table>
      </div>
    </div>

    <div class="panel section">
      <h2>Method</h2>
      <div class="note">
        Banana uses the grouped kg variants only. Cow milk and oat milk use VAT-aware net prices so the comparison is meaningful across different VAT rates.
        `E` tax items are excluded because they are deposit-return fees, not products.
      </div>
    </div>
  </div>

  <script>
    const bananaMonths = __BANANA_MONTHS_JSON__;
    const bananaValues = __BANANA_VALUES_JSON__;
    const bananaTrend = __BANANA_TREND_JSON__;
    const bananaCounts = __BANANA_COUNTS_JSON__;
    const milkMonths = __MILK_MONTHS_JSON__;
    const cowValues = __COW_VALUES_JSON__;
    const cowCounts = __COW_COUNTS_JSON__;
    const oatValues = __OAT_VALUES_JSON__;
    const oatCounts = __OAT_COUNTS_JSON__;

    new Chart(document.getElementById('bananaChart'), {{
      type: 'line',
      data: {{
        labels: bananaMonths,
        datasets: [{{
          label: 'Banana gross Ft/kg',
          data: bananaValues,
          borderColor: '#7dd3fc',
          backgroundColor: 'rgba(125, 211, 252, .12)',
          tension: .25,
          borderWidth: 2,
          pointRadius: 2,
        }},
        {{
          label: 'Trendline',
          data: bananaTrend,
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
        plugins: {{
          legend: {{ labels: {{ color: '#ecf2ff' }} }},
          tooltip: {{
            callbacks: {{
              afterLabel: (ctx) => 'Entries: ' + bananaCounts[ctx.dataIndex]
            }}
          }}
        }},
        scales: {{
          x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(148,163,184,.08)' }} }},
          y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(148,163,184,.08)' }} }}
        }}
      }}
    }});

    new Chart(document.getElementById('milkChart'), {{
      type: 'line',
      data: {{
        labels: milkMonths,
        datasets: [
          {{
            label: 'Cow milk net Ft/l',
            data: cowValues,
            borderColor: '#fbbf24',
            backgroundColor: 'rgba(251, 191, 36, .12)',
            tension: .25,
            borderWidth: 2,
            pointRadius: 2,
          }},
          {{
            label: 'Oat milk net Ft/l',
            data: oatValues,
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
        plugins: {{
          legend: {{ labels: {{ color: '#ecf2ff' }} }},
          tooltip: {{
            callbacks: {{
              afterLabel: (ctx) => {
                if (ctx.dataset.label === 'Cow milk net Ft/l') return 'Entries: ' + cowCounts[ctx.dataIndex];
                if (ctx.dataset.label === 'Oat milk net Ft/l') return 'Entries: ' + oatCounts[ctx.dataIndex];
                return '';
              }
            }}
          }}
        }},
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
        .replace("__GROUP_ROWS__", "".join(group_rows))
        .replace("__CATEGORY_ROWS__", "".join(category_rows))
        .replace("__BANANA_MONTHS_JSON__", safe_json(data["banana_months"]))
        .replace("__BANANA_VALUES_JSON__", safe_json(data["banana_values"]))
        .replace("__BANANA_TREND_JSON__", safe_json(data["banana_trend"]))
        .replace("__BANANA_COUNTS_JSON__", safe_json(data["banana_counts"]))
        .replace("__MILK_MONTHS_JSON__", safe_json(data["milk_months"]))
        .replace("__COW_VALUES_JSON__", safe_json(data["cow_values"]))
        .replace("__COW_COUNTS_JSON__", safe_json(data["cow_counts"]))
        .replace("__OAT_VALUES_JSON__", safe_json(data["oat_values"]))
        .replace("__OAT_COUNTS_JSON__", safe_json(data["oat_counts"]))
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate product price HTML")
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
