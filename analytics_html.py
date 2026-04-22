#!/usr/bin/env python3
"""Generate a first Lidl Plus analytics HTML dashboard."""

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
OUTPUT_PATH = BASE_DIR / "export" / "analytics.html"

BASKET_START_MONTH = "2022-07"
BASKET_WINDOW_MONTHS = 2
BASKET_START_LABEL = "Jul 2022"
BASKET_BASELINE_LABEL = "Jul-Aug 2022"
MIN_BASKET_OCCURRENCES = 20
MIN_BASKET_MONTHS = 8
MAX_BASKET_ITEMS = 9


@dataclass
class BasketItemSnapshot:
    description: str
    baseline_price: float
    latest_price: float
    latest_month: str
    months_covered: int

    @property
    def change(self) -> float:
        return self.latest_price - self.baseline_price

    @property
    def change_pct(self) -> float:
        if self.baseline_price == 0:
            return 0.0
        return (self.latest_price / self.baseline_price - 1) * 100


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


def format_month_window(months: list[str]) -> str:
    return months[0] if len(months) == 1 else f"{months[0]}–{months[-1]}"


def query_rows(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(conn.execute(query, params))


def select_basket_items(conn: sqlite3.Connection) -> list[str]:
    latest_month_row = query_rows(
        conn,
        "SELECT MAX(substr(date, 1, 7)) AS ym FROM receipts WHERE date >= ?",
        (f"{BASKET_START_MONTH}-01",),
    )
    latest_month = latest_month_row[0]["ym"]
    if latest_month is None:
        raise RuntimeError("No receipts found for basket selection")

    months = month_range(BASKET_START_MONTH, latest_month)
    baseline_window = set(months[:BASKET_WINDOW_MONTHS])
    presence_rows = query_rows(
        conn,
        """
        SELECT DISTINCT a.description AS description,
                        substr(r.date, 1, 7) AS ym
        FROM articles a
        JOIN receipts r ON r.id = a.receipt_id
        WHERE r.date >= ?
        """,
        (f"{BASKET_START_MONTH}-01",),
    )
    presence: dict[str, set[str]] = defaultdict(set)
    for row in presence_rows:
        presence[row["description"]].add(row["ym"])

    rows = query_rows(
        conn,
        """
        SELECT a.description AS description,
               COUNT(*) AS occurrences,
               COUNT(DISTINCT substr(r.date, 1, 7)) AS months
        FROM articles a
        JOIN receipts r ON r.id = a.receipt_id
        WHERE r.date >= ?
        GROUP BY a.description
        HAVING COUNT(*) >= ?
           AND COUNT(DISTINCT substr(r.date, 1, 7)) >= ?
        """,
        (
            f"{BASKET_START_MONTH}-01",
            MIN_BASKET_OCCURRENCES,
            MIN_BASKET_MONTHS,
        ),
    )
    ranked = []
    for row in rows:
        description = row["description"]
        if not presence[description].intersection(baseline_window):
            continue
        window_coverage = sum(
            1
            for idx in range(len(months) - 1)
            if months[idx] in presence[description] and months[idx + 1] in presence[description]
        )
        ranked.append((window_coverage, int(row["occurrences"]), int(row["months"]), description))
    ranked.sort(reverse=True)
    basket_items = [description for _, _, _, description in ranked[:MAX_BASKET_ITEMS]]
    if not basket_items:
        raise RuntimeError("No basket items matched the frequency threshold")
    return basket_items


def build_data():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    try:
        basket_items = select_basket_items(conn)
        monthly = query_rows(
            conn,
            """
            SELECT substr(date, 1, 7) AS ym,
                   COUNT(*) AS receipts,
                   ROUND(SUM(total_amount), 2) AS spend,
                   ROUND(AVG(total_amount), 2) AS avg_basket
            FROM receipts
            GROUP BY ym
            ORDER BY ym
            """,
        )
        top_stores = query_rows(
            conn,
            """
            SELECT s.name AS name,
                   s.locality AS locality,
                   COUNT(*) AS receipts,
                   ROUND(SUM(r.total_amount), 2) AS spend,
                   ROUND(AVG(r.total_amount), 2) AS avg_basket
            FROM receipts r
            JOIN stores s ON s.store_id = r.store_id
            GROUP BY s.store_id
            ORDER BY spend DESC, receipts DESC
            LIMIT 10
            """,
        )
        basket_monthly = query_rows(
            conn,
            f"""
            SELECT a.description AS description,
                   substr(r.date, 1, 7) AS ym,
                   ROUND(AVG(a.unit_price), 2) AS avg_price,
                   COUNT(*) AS occurrences
            FROM articles a
            JOIN receipts r ON r.id = a.receipt_id
            WHERE r.date >= '{BASKET_START_MONTH}-01'
              AND a.description IN ({",".join("?" for _ in basket_items)})
            GROUP BY a.description, ym
            ORDER BY ym, a.description
            """,
            tuple(basket_items),
        )
    finally:
        conn.close()

    monthly_rows = [dict(row) for row in monthly]
    top_store_rows = [dict(row) for row in top_stores]

    monthly_item_prices: dict[str, dict[str, float]] = defaultdict(dict)
    item_month_counts: dict[str, set[str]] = defaultdict(set)
    for row in basket_monthly:
        description = row["description"]
        ym = row["ym"]
        monthly_item_prices[ym][description] = float(row["avg_price"])
        item_month_counts[description].add(ym)

    all_months = month_range(BASKET_START_MONTH, monthly_rows[-1]["ym"])
    baseline_window = all_months[:BASKET_WINDOW_MONTHS]
    baseline_by_item: dict[str, float] = {}
    for item in basket_items:
        baseline_prices = [
            monthly_item_prices[month][item]
            for month in baseline_window
            if item in monthly_item_prices.get(month, {})
        ]
        if baseline_prices:
            baseline_by_item[item] = mean(baseline_prices)

    basket_series = []
    latest_item_prices: dict[str, float] = {}
    latest_item_windows: dict[str, str] = {}
    for idx in range(BASKET_WINDOW_MONTHS - 1, len(all_months)):
        window = all_months[idx - BASKET_WINDOW_MONTHS + 1 : idx + 1]
        window_label = format_month_window(window)
        ratios = []
        prices = []
        for item in basket_items:
            item_prices = [
                monthly_item_prices[month][item]
                for month in window
                if item in monthly_item_prices.get(month, {})
            ]
            if not item_prices:
                continue
            price = mean(item_prices)
            latest_item_prices[item] = price
            latest_item_windows[item] = window_label
            baseline = baseline_by_item.get(item)
            if price is None or baseline in (None, 0):
                continue
            ratios.append(price / baseline * 100)
            prices.append(price)
        basket_series.append(
            {
                "ym": window[-1],
                "window": window_label,
                "avg_price": round(mean(prices), 2) if len(prices) >= 4 else None,
                "index": round(mean(ratios), 2) if len(ratios) >= 4 else None,
                "coverage": len(ratios),
            }
        )

    item_snapshots: list[BasketItemSnapshot] = []
    for item in basket_items:
        if item not in baseline_by_item:
            continue
        latest_price = latest_item_prices.get(item)
        latest_month_label = latest_item_windows.get(item)
        if latest_price is None or latest_month_label is None:
            continue
        item_snapshots.append(
            BasketItemSnapshot(
                description=item,
                baseline_price=baseline_by_item[item],
                latest_price=float(latest_price),
                latest_month=latest_month_label,
                months_covered=len(item_month_counts[item]),
            )
        )

    latest_basket = next((row for row in reversed(basket_series) if row["index"] is not None), None)
    latest_spend = monthly_rows[-1]
    total_receipts = sum(int(row["receipts"]) for row in monthly_rows)
    total_spend = sum(float(row["spend"]) for row in monthly_rows)
    avg_basket_all_time = total_spend / total_receipts if total_receipts else 0

    return {
        "monthly": monthly_rows,
        "stores": top_store_rows,
        "basket_series": basket_series,
        "basket_items": [
            {
                "description": snapshot.description,
                "baseline_price": snapshot.baseline_price,
                "latest_price": snapshot.latest_price,
                "latest_month": snapshot.latest_month,
                "months_covered": snapshot.months_covered,
                "change": snapshot.change,
                "change_pct": snapshot.change_pct,
            }
            for snapshot in item_snapshots
        ],
        "basket_latest_index": latest_basket["index"] if latest_basket else None,
        "basket_latest_price": latest_basket["avg_price"] if latest_basket else None,
        "basket_latest_window": basket_series[-1]["window"] if basket_series else None,
        "total_receipts": total_receipts,
        "total_spend": total_spend,
        "avg_basket_all_time": avg_basket_all_time,
        "latest_month": latest_spend["ym"],
        "latest_month_spend": float(latest_spend["spend"]),
        "latest_month_receipts": int(latest_spend["receipts"]),
        "monthly_months": [row["ym"] for row in monthly_rows],
        "monthly_receipts": [int(row["receipts"]) for row in monthly_rows],
        "monthly_spend": [float(row["spend"]) for row in monthly_rows],
        "monthly_avg_basket": [float(row["avg_basket"]) for row in monthly_rows],
        "basket_items_selected": basket_items,
    }


def render_html(data: dict) -> str:
    months = data["monthly_months"]
    basket_months = [row["ym"] for row in data["basket_series"]]
    basket_points = [
        {
            "ym": row["ym"],
            "window": row["window"],
            "index": row["index"],
            "avg_price": row["avg_price"],
            "coverage": row["coverage"],
        }
        for row in data["basket_series"]
    ]
    basket_index_data = [row["index"] for row in basket_points]
    basket_coverage = [row["coverage"] for row in basket_points]
    store_labels = [f"{row['name']} ({row['locality']})" for row in data["stores"]]
    store_spend = [float(row["spend"]) for row in data["stores"]]
    basket_items = data["basket_items"]
    basket_items_selected = data["basket_items_selected"]

    latest_basket_index = data["basket_latest_index"]
    latest_basket_price = data["basket_latest_price"]
    latest_basket_window = data["basket_latest_window"]
    inflation_since_baseline = (
        latest_basket_index - 100 if latest_basket_index is not None else None
    )

    cards = [
        ("Total receipts", fmt_number(data["total_receipts"])),
        ("Total spend", fmt_money(data["total_spend"])),
        ("Average basket", fmt_money(data["avg_basket_all_time"])),
        ("Latest basket index", f"{latest_basket_index:.1f}" if latest_basket_index is not None else "—"),
    ]

    item_rows = []
    for item in basket_items:
        item_rows.append(
            "<tr>"
            f"<td>{html.escape(item['description'])}</td>"
            f"<td>{fmt_money(item['baseline_price'])}</td>"
            f"<td>{fmt_money(item['latest_price'])}</td>"
            f"<td>{fmt_money(item['change'])}</td>"
            f"<td>{fmt_percent(item['change_pct'])}</td>"
            f"<td>{html.escape(item['latest_month'])}</td>"
            f"<td>{fmt_number(item['months_covered'])}</td>"
            "</tr>"
        )

    card_html = "".join(
        f"""
        <div class="card">
          <div class="label">{html.escape(label)}</div>
          <div class="value">{html.escape(value)}</div>
        </div>
        """
        for label, value in cards
    )

    template = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lidl Plus Analytics</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {{
      --bg: #0b1020;
      --panel: #131a2e;
      --panel-soft: #18213a;
      --text: #ecf2ff;
      --muted: #94a3b8;
      --accent: #7dd3fc;
      --accent-2: #fbbf24;
      --border: rgba(148,163,184,.18);
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
    .chart-box.sm {{ height: 300px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 14px;
      font-size: 14px;
    }}
    th, td {{
      padding: 10px 8px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      white-space: nowrap;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    .section {{ margin-top: 18px; }}
    .two-col {{ display: grid; grid-template-columns: 1.3fr .7fr; gap: 16px; }}
    .note {{ color: var(--muted); font-size: 13px; line-height: 1.5; }}
    @media (max-width: 1100px) {{
      .cards, .grid, .two-col {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Lidl Plus Analytics</h1>
    <div class="sub">Aug 2021 – Apr 2026 · basket inflation focused on the weekly-shopping era from Jul 2022</div>
    <div class="cards">__CARD_HTML__</div>

    <div class="grid">
      <div class="panel">
        <h2>Staple basket inflation</h2>
        <div class="chart-box"><canvas id="basketChart"></canvas></div>
        <p>Basket index uses automatically selected recurring items, normalized to 100 on the Jul-Aug 2022 2-month baseline.</p>
      </div>
      <div class="panel">
        <h2>Method</h2>
        <div class="note">
          The basket uses the most frequent items from the weekly-shopping era that began after you moved out in __BASKET_START_LABEL__.
          It currently requires at least 20 purchases and 8 distinct months of coverage since __BASKET_START_LABEL__.
          Each point is a 2-month rolling average ending in that month, and the basket line is shown only when at least four selected staples are available.
        </div>
        <div class="section">
          <div class="label">Selected basket items</div>
          <div class="note">__BASKET_ITEMS_SELECTED__</div>
        </div>
        <div class="section">
          <div class="label">Latest 2-mo basket price</div>
            <div class="value">__LATEST_BASKET_PRICE__</div>
        </div>
        <div class="section">
          <div class="label">Inflation since baseline</div>
            <div class="value">__INFLATION_SINCE_BASELINE__</div>
        </div>
        <div class="section">
          <div class="label">Latest 2-mo window</div>
            <div class="value">__LATEST_MONTH__</div>
        </div>
      </div>
    </div>

    <div class="grid section">
      <div class="panel">
        <h2>Monthly spend and receipt count</h2>
        <div class="chart-box"><canvas id="spendChart"></canvas></div>
      </div>
      <div class="panel">
        <h2>Top stores by spend</h2>
        <div class="chart-box sm"><canvas id="storeChart"></canvas></div>
      </div>
    </div>

    <div class="panel section">
      <h2>Basket item price change</h2>
      <table>
        <thead>
          <tr>
            <th>Item</th>
            <th>__BASKET_BASELINE_LABEL__ avg</th>
            <th>Latest 2-mo avg</th>
            <th>Delta</th>
            <th>Delta %</th>
            <th>Latest window</th>
            <th>Months covered</th>
          </tr>
        </thead>
        <tbody>
          __ITEM_ROWS__
        </tbody>
      </table>
    </div>
  </div>

  <script>
    const basketMonths = __BASKET_MONTHS_JSON__;
    const basketPoints = __BASKET_POINTS_JSON__;
    const monthlyMonths = __MONTHLY_MONTHS_JSON__;
    const monthlySpend = __MONTHLY_SPEND_JSON__;
    const monthlyReceipts = __MONTHLY_RECEIPTS_JSON__;
    const storeLabels = __STORE_LABELS_JSON__;
    const storeSpend = __STORE_SPEND_JSON__;
    const basketIndexData = __BASKET_INDEX_JSON__;
    const basketCoverage = __BASKET_COVERAGE_JSON__;
    const basketItemsSelected = __BASKET_ITEMS_SELECTED_JSON__;

    new Chart(document.getElementById('basketChart'), {{
      type: 'line',
      data: {{
        labels: basketMonths,
        datasets: [
          {{
            label: 'Basket index',
            data: basketIndexData,
            borderColor: '#7dd3fc',
            backgroundColor: 'rgba(125, 211, 252, .12)',
            tension: .25,
            borderWidth: 2,
            pointRadius: 3,
          }},
          {{
            label: 'Coverage',
            data: basketCoverage,
            type: 'bar',
            yAxisID: 'y1',
            backgroundColor: 'rgba(251, 191, 36, .25)',
            borderColor: '#fbbf24',
            borderWidth: 1,
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
                if (ctx.dataset.label === 'Basket index') {
                  const p = basketPoints[ctx.dataIndex];
                  return 'Window: ' + p.window + '\\nCoverage: ' + p.coverage + '/' + basketItemsSelected.length;
                }
                return '';
              }
            }}
          }}
        }},
        scales: {{
          x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(148,163,184,.08)' }} }},
          y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(148,163,184,.08)' }} }},
          y1: {{
            position: 'right',
            ticks: {{ color: '#94a3b8' }},
            grid: {{ drawOnChartArea: false }},
            suggestedMax: 8
          }}
        }}
      }}
    }});

    new Chart(document.getElementById('spendChart'), {{
      type: 'line',
      data: {{
        labels: monthlyMonths,
        datasets: [
          {{
            label: 'Spend',
            data: monthlySpend,
            borderColor: '#7dd3fc',
            backgroundColor: 'rgba(125, 211, 252, .12)',
            tension: .25,
            yAxisID: 'y',
          }},
          {{
            label: 'Receipts',
            data: monthlyReceipts,
            borderColor: '#fbbf24',
            backgroundColor: 'rgba(251, 191, 36, .15)',
            tension: .25,
            yAxisID: 'y1',
          }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{ legend: {{ labels: {{ color: '#ecf2ff' }} }} }},
        scales: {{
          x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(148,163,184,.08)' }} }},
          y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(148,163,184,.08)' }} }},
          y1: {{
            position: 'right',
            ticks: {{ color: '#94a3b8' }},
            grid: {{ drawOnChartArea: false }}
          }}
        }}
      }}
    }});

    new Chart(document.getElementById('storeChart'), {{
      type: 'bar',
      data: {{
        labels: storeLabels,
        datasets: [{{
          label: 'Spend',
          data: storeSpend,
          backgroundColor: 'rgba(125, 211, 252, .65)',
          borderColor: '#7dd3fc',
          borderWidth: 1
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: 'y',
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(148,163,184,.08)' }} }},
          y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ display: false }} }}
        }}
      }}
    }});
  </script>
</body>
</html>"""
    template = template.replace("{{", "{").replace("}}", "}")
    return (
        template.replace("__CARD_HTML__", card_html)
        .replace("__LATEST_BASKET_PRICE__", html.escape(fmt_money(latest_basket_price)))
        .replace("__INFLATION_SINCE_BASELINE__", html.escape(fmt_percent(inflation_since_baseline)))
        .replace("__ITEM_ROWS__", "".join(item_rows))
        .replace("__BASKET_MONTHS_JSON__", safe_json(basket_months))
        .replace("__BASKET_POINTS_JSON__", safe_json(basket_points))
        .replace("__MONTHLY_MONTHS_JSON__", safe_json(months))
        .replace("__MONTHLY_SPEND_JSON__", safe_json(data["monthly_spend"]))
        .replace("__MONTHLY_RECEIPTS_JSON__", safe_json(data["monthly_receipts"]))
        .replace("__STORE_LABELS_JSON__", safe_json(store_labels))
        .replace("__STORE_SPEND_JSON__", safe_json(store_spend))
        .replace("__BASKET_INDEX_JSON__", safe_json(basket_index_data))
        .replace("__BASKET_COVERAGE_JSON__", safe_json(basket_coverage))
        .replace("__BASKET_ITEMS_SELECTED_JSON__", safe_json(basket_items_selected))
        .replace("__BASKET_ITEMS_SELECTED__", html.escape(", ".join(basket_items_selected)))
        .replace("__BASKET_BASELINE_LABEL__", html.escape(BASKET_BASELINE_LABEL))
        .replace("__BASKET_START_LABEL__", html.escape(BASKET_START_LABEL))
        .replace("__LATEST_MONTH__", html.escape(latest_basket_window or data["latest_month"]))
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Lidl Plus analytics HTML")
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
