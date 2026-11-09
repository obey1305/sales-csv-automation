"""Recreate five fictional CSV exports. No real customer or business data."""
import csv
from datetime import date, timedelta
from pathlib import Path

from sales_cleanup import FIELDS

def main():
    directory = Path(__file__).resolve().parent / "data" / "raw"
    directory.mkdir(parents=True, exist_ok=True)
    products = [("Ceramic Mug", "249.00"), ("Canvas Tote", "499.50"),
                ("Desk Mat", "1299.00"), ("Notebook", "179.00"), ("Water Bottle", "699.00")]
    records = []
    for i in range(50):
        product, price = products[i % 5]
        records.append([f"ORD-{1001+i}", (date(2026, 10, 1) + timedelta(days=i % 28)).isoformat(),
                        product, str(i % 3 + 1), price, "INR", "Website" if i % 2 == 0 else "Marketplace"])
    headers = [FIELDS, ["Order ID", "Date", "Product", "Qty", "Price", "Currency", "Sales Channel"]]
    names = ["week_01.csv", "week_02.csv", "week_03.csv", "week_04.csv", "month_end.csv"]
    groups = [[r.copy() for r in records[i*10:(i+1)*10]] for i in range(5)]
    for target, source in [(0, 1), (1, 4), (2, 12), (3, 22), (4, 32), (4, 41)]:
        groups[target].insert(2, records[source].copy())
    # Four independent unresolved orders: missing ID, impossible date, missing
    # quantity and a nonnumeric price. None can be safely repaired by guessing.
    bad = [records[0].copy() for _ in range(4)]
    for index, row in enumerate(bad):
        row[0] = f"ORD-ERR-{index+1}"
    bad[0][0] = ""
    bad[1][1] = "31/02/2026"
    bad[2][3] = ""
    bad[3][4] = "unknown"
    for index, row in enumerate(bad):
        groups[index].insert(4, row)
    for index, (name, rows) in enumerate(zip(names, groups)):
        with (directory / name).open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers[index % 2])
            for number, row in enumerate(rows):
                if number % 2 == 0:
                    row[2] = " " + row[2] + "  "
                    row[6] = row[6].upper()
                if number % 3 == 0 and row[1] != "31/02/2026":
                    row[1] = date.fromisoformat(row[1]).strftime("%d/%m/%Y")
                if number % 2 == 1 and row[4] != "unknown":
                    row[4] = "₹" + format(float(row[4]), ",.2f")
                writer.writerow(row)
    print("Created 5 synthetic CSVs: 50 valid orders, 6 duplicate rows, 4 invalid rows.")

if __name__ == "__main__":
    main()
