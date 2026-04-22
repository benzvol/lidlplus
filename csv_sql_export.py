import csv
import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
EXPORT_DIR = BASE_DIR / "export"
EXPORT_DIR.mkdir(exist_ok=True)
TAXONOMY_PATH = EXPORT_DIR / "product_taxonomy.json"


def normalize_description(description: str) -> str:
    if description == "BANÁN":
        return "BANÁN KG"
    return description


def load_taxonomy() -> dict:
    if not TAXONOMY_PATH.exists():
        raise FileNotFoundError(f"Taxonomy JSON not found: {TAXONOMY_PATH}")
    with open(TAXONOMY_PATH, "r", encoding="utf-8") as file:
        return json.load(file)

# Load JSON data
with open(BASE_DIR / "tickets.json", "r", encoding="utf-8") as file:
    receipts = json.load(file)

# === 1. Write Receipts CSV ===
with open(EXPORT_DIR / "receipts.csv", "w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)
    writer.writerow(["id", "date", "total_amount", "store_id"])
    
    for receipt in receipts:
        writer.writerow([
            receipt["id"],
            receipt["date"],
            receipt["totalAmount"],
            receipt["store"]["id"]
        ])

# === 2. Write Stores CSV ===
stores_seen = set()
with open(EXPORT_DIR / "stores.csv", "w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)
    writer.writerow(["store_id", "name", "address", "postal_code", "locality"])
    
    for receipt in receipts:
        store = receipt["store"]
        if store["id"] not in stores_seen:
            writer.writerow([
                store["id"],
                store["name"],
                store["address"],
                store["postalCode"],
                store["locality"]
            ])
            stores_seen.add(store["id"])

# === 3. Write Articles CSV ===
with open(EXPORT_DIR / "articles.csv", "w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)
    writer.writerow(["receipt_id", "article_id", "description", "unit_price", "quantity", "tax_type"])
    
    for receipt in receipts:
        for article in receipt["articles"]:
            description = normalize_description(article["description"])
            writer.writerow([
                receipt["id"],  # Foreign key reference to receipt
                article["id"],
                description,
                article["unit_price"],
                article["quantity"],
                article["tax_type"]
            ])

# === 4. Store Data in SQLite ===
db_path = EXPORT_DIR / "receipts.db"
if db_path.exists():
    db_path.unlink()
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
taxonomy = load_taxonomy()

# Create Stores Table
cursor.execute("""
CREATE TABLE IF NOT EXISTS stores (
    store_id TEXT PRIMARY KEY,
    name TEXT,
    address TEXT,
    postal_code TEXT,
    locality TEXT
)
""")

# Create Receipts Table
cursor.execute("""
CREATE TABLE IF NOT EXISTS receipts (
    id TEXT PRIMARY KEY,
    date TEXT,
    total_amount REAL,
    store_id TEXT,
    FOREIGN KEY (store_id) REFERENCES stores(store_id)
)
""")

# Create Articles Table
cursor.execute("""
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id TEXT,
    article_id TEXT,
    description TEXT,
    unit_price REAL,
    quantity REAL,
    tax_type TEXT,
    FOREIGN KEY (receipt_id) REFERENCES receipts(id)
)
""")

# Create Taxonomy Tables
cursor.executescript("""
CREATE TABLE IF NOT EXISTS taxonomy_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS taxonomy_product_groups (
    product_type TEXT PRIMARY KEY,
    category TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS taxonomy_group_members (
    product_type TEXT NOT NULL,
    description TEXT NOT NULL,
    PRIMARY KEY (product_type, description)
);

CREATE TABLE IF NOT EXISTS taxonomy_items (
    description TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    product_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS taxonomy_vat_rates (
    tax_type TEXT PRIMARY KEY,
    vat_rate REAL NOT NULL
);
""")

# Insert Stores into SQLite
for receipt in receipts:
    store = receipt["store"]
    cursor.execute("""
    INSERT OR IGNORE INTO stores (store_id, name, address, postal_code, locality)
    VALUES (?, ?, ?, ?, ?)
    """, (
        store["id"],
        store["name"],
        store["address"],
        store["postalCode"],
        store["locality"]
    ))

# Insert Receipts into SQLite
for receipt in receipts:
    cursor.execute("""
    INSERT INTO receipts (id, date, total_amount, store_id)
    VALUES (?, ?, ?, ?)
    """, (
        receipt["id"],
        receipt["date"],
        receipt["totalAmount"],
        receipt["store"]["id"]
    ))

# Insert Articles into SQLite
for receipt in receipts:
    for article in receipt["articles"]:
        description = normalize_description(article["description"])
        cursor.execute("""
        INSERT INTO articles (receipt_id, article_id, description, unit_price, quantity, tax_type)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (
            receipt["id"],
            article["id"],
            description,
            article["unit_price"],
            article["quantity"],
            article["tax_type"]
        ))

# Insert Taxonomy into SQLite
cursor.executemany(
    "INSERT OR REPLACE INTO taxonomy_meta (key, value) VALUES (?, ?)",
    [
        ("source", taxonomy.get("source", "unknown")),
        ("generated_at", taxonomy.get("generated_at", "")),
        ("batch_size", str(taxonomy.get("batch_size", ""))),
    ],
)

cursor.executemany(
    "INSERT OR REPLACE INTO taxonomy_vat_rates (tax_type, vat_rate) VALUES (?, ?)",
    [
        (tax_type, float(rate))
        for tax_type, rate in taxonomy.get("vat_rates", {}).items()
    ],
)

item_lookup = {item["description"]: item for item in taxonomy.get("items", [])}
cursor.executemany(
    """
    INSERT OR REPLACE INTO taxonomy_items (
        description, category, product_type
    ) VALUES (?, ?, ?)
    """,
    [
        (
            item["description"],
            item["category"],
            item["product_type"],
        )
        for item in taxonomy.get("items", [])
    ],
)

group_rows = []
group_member_rows = []
for product_type, descriptions in taxonomy.get("product_groups", {}).items():
    if not descriptions:
        continue
    first_item = item_lookup.get(descriptions[0])
    category = first_item["category"] if first_item else "unknown"
    group_rows.append((product_type, category))
    group_member_rows.extend((product_type, description) for description in descriptions)

cursor.executemany(
    "INSERT OR REPLACE INTO taxonomy_product_groups (product_type, category) VALUES (?, ?)",
    group_rows,
)
cursor.executemany(
    "INSERT OR REPLACE INTO taxonomy_group_members (product_type, description) VALUES (?, ?)",
    group_member_rows,
)

# Commit & Close DB Connection
conn.commit()
conn.close()

print("CSV files and SQLite database created successfully!")
