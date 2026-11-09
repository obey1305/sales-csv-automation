"""Integration tests: real CLI, temporary files, hand-calculated outcomes."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "sales_cleanup.py"
HEADERS = ["order_id", "order_date", "product", "quantity", "unit_price", "currency", "channel"]
BASE = ["A001", "2026-10-01", "Mug", "2", "100.25", "INR", "Website"]

class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.out = self.root / "out"

    def write(self, name, rows, headers=HEADERS):
        with (self.raw / name).open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)

    def run_cli(self, success=True, output=None):
        p = subprocess.run([sys.executable, str(SCRIPT), "--input", str(self.raw),
                            "--output", str(output or self.out)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0 if success else 2, p.stdout + p.stderr)
        return p

    def rows(self, filename):
        with (self.out / filename).open(newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def summary(self):
        return json.loads((self.out / "summary.json").read_text())

    def test_normalizes_and_deduplicates_without_inflating_sales(self):
        self.write("a.csv", [BASE])
        self.write("b.csv", [[" A001 ", "01/10/2026", " Mug ", "2", "₹100.25", "inr", "website"],
                              ["A002", "2026-10-02", "Bag", "1", "1,299.00", "INR", "Marketplace"]],
                   ["Order ID", "Date", "Product", "Qty", "Price", "Currency", "Sales Channel"])
        self.run_cli()
        s = self.summary()
        self.assertEqual((s["input_rows"], s["clean_rows"], s["duplicate_rows"], s["flagged_rows"]), (3, 2, 1, 0))
        self.assertEqual(s["total_sales_inr"], "1499.50")
        self.assertEqual(self.rows("cleaned_sales.csv")[0]["order_date"], "2026-10-01")
        self.assertEqual(self.rows("duplicates.csv")[0]["source_file"], "b.csv")

    def test_invalid_values_are_flagged_and_never_enter_sales(self):
        bad = []
        for col, val in [(0, ""), (1, "31/02/2026"), (3, ""), (3, "1.5"), (4, "NaN"),
                         (4, "1,2,3"), (4, "-1"), (5, "USD"), (4, "1.001")]:
            row = BASE.copy()
            row[0] = f"BAD{len(bad):03}"
            row[col] = val
            bad.append(row)
        self.write("bad.csv", bad + [BASE])
        self.run_cli()
        self.assertEqual(self.summary()["flagged_rows"], 9)
        self.assertEqual(self.summary()["total_sales_inr"], "200.50")
        self.assertTrue(all(r["issues"] for r in self.rows("flagged_rows.csv")))

    def test_conflicting_order_ids_quarantine_every_variant(self):
        second = BASE.copy()
        second[4] = "250.00"
        self.write("a.csv", [BASE, second, BASE])
        self.run_cli()
        self.assertEqual(self.summary()["clean_rows"], 0)
        self.assertEqual(self.summary()["flagged_rows"], 3)
        self.assertEqual(self.summary()["total_sales_inr"], "0.00")

    def test_schema_errors_fail_before_writing_results(self):
        self.write("broken.csv", [["A001"]], ["order_id"])
        self.run_cli(False)
        self.assertFalse(self.out.exists())

    def test_empty_input_fails_and_source_directory_is_protected(self):
        self.run_cli(False)
        self.write("a.csv", [BASE])
        original = (self.raw / "a.csv").read_bytes()
        self.run_cli(False, self.raw)
        self.assertEqual((self.raw / "a.csv").read_bytes(), original)

    def test_reruns_are_deterministic_and_preserve_raw_data(self):
        self.write("a.csv", [BASE])
        before = (self.raw / "a.csv").read_bytes()
        self.run_cli()
        outputs = {p.name: p.read_bytes() for p in self.out.iterdir()}
        self.run_cli()
        self.assertEqual(outputs, {p.name: p.read_bytes() for p in self.out.iterdir()})
        self.assertEqual(before, (self.raw / "a.csv").read_bytes())

    def test_html_escapes_source_text_and_csv_prevents_formula_execution(self):
        row = BASE.copy()
        row[2] = '<script>alert("x")</script>'
        other = BASE.copy()
        other[0] = "A002"
        other[2] = "=1+1"
        self.write("a.csv", [row, other])
        self.run_cli()
        report = (self.out / "report.html").read_text()
        self.assertNotIn('<script>alert("x")</script>', report)
        self.assertEqual(self.rows("cleaned_sales.csv")[1]["product"], "'=1+1")

    def test_malformed_row_is_rejected_instead_of_silently_truncated(self):
        self.write("a.csv", [BASE + ["unexpected"]])
        self.run_cli(False)
        self.assertFalse(self.out.exists())

    def test_csv_escaping_does_not_merge_distinct_order_ids(self):
        a, b = BASE.copy(), BASE.copy()
        a[0], b[0] = "=1", "'=1"
        self.write("a.csv", [a, b])
        self.run_cli()
        ids = [row["order_id"] for row in self.rows("cleaned_sales.csv")]
        self.assertEqual(ids, ["''=1", "'=1"])

    def test_html_channel_breakdown_includes_more_than_eight_channels(self):
        records = []
        for i in range(10):
            row = BASE.copy()
            row[0], row[6] = f"A{i:03}", f"channel-{i:02}"
            records.append(row)
        self.write("a.csv", records)
        self.run_cli()
        self.assertIn("channel-09", (self.out / "report.html").read_text())

if __name__ == "__main__":
    unittest.main()
