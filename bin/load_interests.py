#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb>=1.1",
# ]
# ///
"""
Load the curated committee->interest dictionary into dim_group_mappings.

`committee_interests.csv` is the version-controlled source of truth (hand-curated,
NOT keyword-matched). This loader is the ONLY sanctioned writer of the otherwise
empty dim_group_mappings table; it touches no other table. It is idempotent:
each run fully replaces dim_group_mappings from the CSV.

Usage:
    ./load_interests.py                          # load committee_interests.csv
    ./load_interests.py --csv other.csv
    ./load_interests.py --dry-run                # validate only, write nothing

Validation: every cmte_id is checked against dim_committees. Unknown ids are
reported and skipped (they would silently match nothing in the fact tables).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent  # project root (this script lives in bin/)


def read_dictionary(path: Path) -> list[tuple[str, str, str]]:
    """Parse the curated CSV, skipping '#' comment and blank lines."""
    rows: list[tuple[str, str, str]] = []
    with path.open(newline="") as fh:
        lines = [ln for ln in fh
                 if ln.strip() and not ln.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    required = {"cmte_id", "category", "notes"}
    if not required.issubset({c.strip() for c in (reader.fieldnames or [])}):
        sys.exit(f"CSV must have columns {sorted(required)}; "
                 f"got {reader.fieldnames}")
    for r in reader:
        cid = (r["cmte_id"] or "").strip()
        cat = (r["category"] or "").strip()
        notes = (r.get("notes") or "").strip()
        if cid and cat:
            rows.append((cid, cat, notes))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Load committee interest dictionary into dim_group_mappings.")
    ap.add_argument("--csv", default=str(ROOT / "model" / "committee_interests.csv"))
    ap.add_argument("--db", default=str(ROOT / "fec_campaign_finance.db"))
    ap.add_argument("--dry-run", action="store_true",
                    help="validate and report, but do not write")
    args = ap.parse_args(argv)

    csv_path, db_path = Path(args.csv), Path(args.db)
    if not csv_path.exists():
        sys.exit(f"Dictionary not found: {csv_path}")
    if not db_path.exists():
        sys.exit(f"DB not found: {db_path}")

    entries = read_dictionary(csv_path)
    if not entries:
        sys.exit("No entries parsed from CSV.")

    # Duplicate cmte_id check (a committee in two categories is almost always a bug).
    seen: dict[str, str] = {}
    dupes = []
    for cid, cat, _ in entries:
        if cid in seen and seen[cid] != cat:
            dupes.append((cid, seen[cid], cat))
        seen[cid] = cat
    for cid, a, b in dupes:
        print(f"  ! duplicate cmte_id {cid} in both '{a}' and '{b}'")

    con = duckdb.connect(str(db_path), read_only=args.dry_run)

    # Validate ids against the committee master.
    known = {r[0] for r in con.execute("SELECT CMTE_ID FROM dim_committees").fetchall()}
    valid = [(c, cat, n) for (c, cat, n) in entries if c in known]
    unknown = [c for (c, _, _) in entries if c not in known]

    by_cat: dict[str, int] = {}
    for _, cat, _ in valid:
        by_cat[cat] = by_cat.get(cat, 0) + 1

    print(f"Parsed {len(entries)} entries from {csv_path.name}: "
          f"{len(valid)} valid, {len(unknown)} unknown committee id(s).")
    for cat in sorted(by_cat):
        print(f"  {by_cat[cat]:>3}  {cat}")
    if unknown:
        print("  unknown (skipped): " + ", ".join(unknown))

    if args.dry_run:
        print("\n[dry-run] nothing written.")
        return 0

    # Replace dim_group_mappings only. No other table is touched.
    con.execute("BEGIN")
    con.execute("DELETE FROM dim_group_mappings")
    con.executemany(
        "INSERT INTO dim_group_mappings (CMTE_ID, custom_category, notes) "
        "VALUES (?, ?, ?)", valid)
    con.execute("COMMIT")
    n = con.execute("SELECT count(*) FROM dim_group_mappings").fetchone()[0]
    print(f"\nLoaded {n} rows into dim_group_mappings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
