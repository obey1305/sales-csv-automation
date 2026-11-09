"""Consolidate single-order INR sales exports without guessing missing values."""
import argparse
from collections import defaultdict
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape
import json
from pathlib import Path
import re

FIELDS = ["order_id", "order_date", "product", "quantity", "unit_price", "currency", "channel"]
ALIASES = {"date": "order_date", "qty": "quantity", "price": "unit_price", "sales_channel": "channel"}
ORIGIN = ["source_file", "source_row"]
CLEAN_FIELDS = FIELDS + ["sales_inr"] + ORIGIN
MONEY = re.compile(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{1,2})?\Z")

def read_inputs(directory):
    """Read and validate all file structures before writing any output."""
    files = sorted(p for p in directory.glob("*.csv") if p.is_file())
    if not files:
        raise ValueError("No .csv input files found")
    rows = []
    for path in files:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f, strict=True)
            header = next(reader, [])
            mapped = []
            for label in header:
                key = re.sub(r"[\s-]+", "_", label.strip().lower())
                mapped.append(ALIASES.get(key, key))
            if len(set(mapped)) != len(mapped) or set(mapped) != set(FIELDS):
                raise ValueError(f"{path.name}: expected exactly these columns (or documented aliases): {', '.join(FIELDS)}")
            for line, values in enumerate(reader, 2):
                if len(values) != len(mapped):
                    raise ValueError(f"{path.name}, record {line}: wrong number of fields")
                rows.append(dict(zip(mapped, values), source_file=path.name, source_row=line))
    if not rows:
        raise ValueError("Input files contain no data rows")
    return files, rows

def normalize(raw):
    row = {key: raw[key].strip() for key in FIELDS}
    row.update({key: raw[key] for key in ORIGIN})
    issues = []
    for key in ["order_id", "product", "channel"]:
        if not row[key]:
            issues.append(f"missing {key}")
    row["channel"] = row["channel"].lower()
    row["currency"] = row["currency"].upper()
    if row["currency"] != "INR":
        issues.append("currency must be INR; conversion is not performed")
    for fmt in ["%Y-%m-%d", "%d/%m/%Y"]:
        try:
            row["order_date"] = datetime.strptime(row["order_date"], fmt).date().isoformat()
            break
        except ValueError:
            continue
    else:
        issues.append("invalid date; expected YYYY-MM-DD or DD/MM/YYYY")
    if not re.fullmatch(r"[0-9]{1,6}", row["quantity"]) or not 0 < int(row["quantity"] or 0) <= 100000:
        issues.append("quantity must be a whole number from 1 to 100000")
    else:
        row["quantity"] = str(int(row["quantity"]))
    price = row["unit_price"]
    if price.startswith("₹"):
        price = price[1:].strip()
    if not MONEY.fullmatch(price) or len(price) > 20:
        issues.append("invalid INR price; use a nonnegative amount with at most two decimals")
    else:
        try:
            amount = Decimal(price.replace(",", ""))
            if amount > Decimal("1000000000"):
                raise InvalidOperation
            row["unit_price"] = f"{amount:.2f}"
        except InvalidOperation:
            issues.append("price exceeds supported limit of INR 1000000000")
    if not issues:
        row["sales_inr"] = f"{Decimal(row['quantity']) * Decimal(row['unit_price']):.2f}"
    return row, issues

def prepare(raw_rows):
    groups = defaultdict(list)
    flagged = []
    # Include invalid variants when detecting ID conflicts: a valid row must not
    # silently win over an invalid row carrying the same order ID.
    for raw in raw_rows:
        row, issues = normalize(raw)
        if row["order_id"]:
            groups[row["order_id"]].append((row, issues, raw))
        else:
            flagged.append(dict(raw, issues="; ".join(issues)))
    cleaned, duplicates = [], []
    for _, variants in sorted(groups.items()):
        signatures = {tuple(row[k] for k in FIELDS) for row, _, _ in variants}
        conflict = len(signatures) > 1
        for index, (row, issues, raw) in enumerate(variants):
            if conflict:
                issues = issues + ["conflicting records for order_id; review every variant"]
            if issues:
                flagged.append(dict(raw, issues="; ".join(issues)))
            elif index == 0:
                cleaned.append(row)
            else:
                duplicates.append(dict(row, issues="duplicate normalized order; kept first source"))
    flagged.sort(key=lambda r: (r["source_file"], r["source_row"]))
    return cleaned, duplicates, flagged

def safe_cell(value):
    """Keep formula-like text inert when CSVs are opened in spreadsheet apps."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "'")):
        return "'" + value
    return value

def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: safe_cell(v) for k, v in row.items() if k in fields})

def table(rows, fields, limit=8):
    head = "".join(f"<th>{escape(k.replace('_', ' ').title())}</th>" for k in fields)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(r.get(k, '')))}</td>" for k in fields) + "</tr>" for r in rows[:limit])
    if not body:
        body = f'<tr><td colspan="{len(fields)}">No records</td></tr>'
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"

def write_report(path, summary, raw, clean, flagged, channels):
    cards = [("Input records", summary["input_rows"]), ("Clean orders", summary["clean_rows"]),
             ("Duplicates removed", summary["duplicate_rows"]), ("Flagged for review", summary["flagged_rows"])]
    card_html = "".join(f"<div class='card'><span>{name}</span><strong>{value}</strong></div>" for name, value in cards)
    price = f"{Decimal(summary['total_sales_inr']):,.2f}"
    # Source-ordered preview intentionally includes an early duplicate and invalid
    # record in the synthetic demo. All HTML values are escaped.
    document = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Sales CSV cleanup | Before &amp; after</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#19283b;font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}.page{max-width:1320px;margin:auto;padding:40px 44px}header{display:flex;justify-content:space-between;align-items:start;border-bottom:1px solid #d5dde7;padding-bottom:26px}.eyebrow{font-size:12px;letter-spacing:2px;font-weight:700;color:#306d75}h1{font-size:36px;line-height:1.15;margin:10px 0}p{margin:8px 0;color:#526175}.badge{background:#e1ecf0;color:#285861;padding:8px 14px;border-radius:20px;font-size:12px;font-weight:700}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:26px 0}.card{padding:18px 22px;background:white;border:1px solid #dce3eb;border-radius:12px}.card span{display:block;color:#526175;font-size:13px}.card strong{display:block;font-size:34px;line-height:1.4}section{background:white;border:1px solid #dce3eb;border-radius:12px;margin:20px 0;padding:24px}h2{font-size:22px;margin:0 0 6px}h3{font-size:16px;margin:20px 0 8px}.section-label{font-size:11px;font-weight:750;letter-spacing:1.6px;color:#306d75}.table-wrap{overflow-x:auto;margin-top:18px}table{border-collapse:collapse;width:100%;font-size:12px;text-align:left}th{background:#eaf0f5;color:#34465d;font-size:11px;letter-spacing:.2px;padding:11px 10px;white-space:nowrap}td{padding:11px 10px;border-bottom:1px solid #e8edf3;max-width:360px;overflow-wrap:anywhere}tbody tr:nth-child(even){background:#fafbfd}.note{padding:13px 16px;background:#fff6e4;border-left:3px solid #b77b17;font-size:13px;margin-top:18px}.money{font-size:34px;font-weight:750;margin:12px 0;color:#215d60}.flow{font-size:13px;color:#526175;margin-top:16px}.footer{font-size:12px;padding:8px 0;color:#66758a}.two{display:grid;grid-template-columns:1fr 1fr;gap:28px}@media(max-width:800px){.page{padding:20px}.cards{grid-template-columns:1fr 1fr}.two{grid-template-columns:1fr}h1{font-size:28px}}
</style><div class="page"><header><div><div class="eyebrow">PYTHON DATA AUTOMATION / PORTFOLIO DEMO</div><h1>From scattered CSVs to a clear sales report</h1><p>Repeatable cleanup for small online retailers.</p></div><span class="badge">SYNTHETIC DATA</span></header>"""
    document += f"<div class='cards'>{card_html}</div>"
    document += "<section id='before'><div class='section-label'>01 / BEFORE</div><h2>Messy exports, repeated manual work</h2><p>Source preview: mixed dates and prices, whitespace, duplicate orders and missing values.</p>"
    document += table(raw, FIELDS + ["source_file"], 8)
    document += "<div class='note'>Input date contract: YYYY-MM-DD or DD/MM/YYYY. All prices are INR. One row represents one order.</div></section>"
    document += "<section id='after'><div class='section-label'>02 / AFTER</div><h2>One clean, traceable dataset</h2><p>Standardized values, duplicate protection and a source reference for every order.</p>"
    document += table(clean, ["order_id", "order_date", "product", "quantity", "unit_price", "sales_inr", "source_file"], 6)
    document += f"<div class='two'><div><h3>Sales from clean orders only</h3><div class='money'>INR {price}</div><p>Quantity × unit price. Excludes flagged and duplicate rows.</p></div><div>{table(channels, ['channel', 'orders', 'sales_inr'], len(channels))}</div></div></section>"
    document += "<section id='review'><div class='section-label'>03 / HUMAN REVIEW</div><h2>Uncertain data stays visible</h2><p>These records are excluded from sales totals. Resolve against source records and rerun.</p>"
    document += table(flagged, ["order_id", "order_date", "quantity", "unit_price", "issues", "source_file"], 8)
    document += f"<div class='flow'>Reconciliation: {summary['input_rows']} input = {summary['clean_rows']} clean + {summary['duplicate_rows']} duplicate + {summary['flagged_rows']} flagged records.</div></section>"
    document += "<div class='footer'>Generated by sales_cleanup.py · Fictional demonstration, not client results · No measured time-saving or revenue claims</div></div></html>"
    path.write_text(document, encoding="utf-8")

def run(input_dir, output_dir):
    input_dir, output_dir = input_dir.resolve(), output_dir.resolve()
    if output_dir == input_dir or input_dir in output_dir.parents or output_dir in input_dir.parents:
        raise ValueError("Input and output must be separate, non-nested directories")
    files, raw = read_inputs(input_dir)
    clean, duplicates, flagged = prepare(raw)
    total = sum((Decimal(r["sales_inr"]) for r in clean), Decimal(0))
    channels = []
    for channel in sorted({r["channel"] for r in clean}):
        orders = [r for r in clean if r["channel"] == channel]
        amount = sum((Decimal(r["sales_inr"]) for r in orders), Decimal(0))
        channels.append(dict(channel=channel, orders=len(orders), sales_inr=f"{amount:.2f}"))
    summary = dict(input_files=len(files), input_rows=len(raw), clean_rows=len(clean),
                   duplicate_rows=len(duplicates), flagged_rows=len(flagged), total_sales_inr=f"{total:.2f}",
                   currency="INR", grain="one row per order", sales_definition="quantity * unit_price; excludes tax, shipping, discounts and refunds")
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "cleaned_sales.csv", CLEAN_FIELDS, clean)
    write_csv(output_dir / "duplicates.csv", CLEAN_FIELDS + ["issues"], duplicates)
    write_csv(output_dir / "flagged_rows.csv", FIELDS + ORIGIN + ["issues"], flagged)
    write_csv(output_dir / "sales_by_channel.csv", ["channel", "orders", "sales_inr"], channels)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    write_report(output_dir / "report.html", summary, raw, clean, flagged, channels)
    return summary

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/raw"))
    parser.add_argument("--output", type=Path, default=Path("results"))
    args = parser.parse_args()
    try:
        summary = run(args.input, args.output)
    except (ValueError, OSError, csv.Error, UnicodeError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
