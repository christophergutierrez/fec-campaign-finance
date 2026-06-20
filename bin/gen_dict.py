#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb>=1.1",
#     "pyyaml>=6.0",
# ]
# ///
"""
Generate DATA_DICTIONARY.md from the LIVE database catalog + curated prose.

Schema facts (tables, columns, types, row counts, parquet column lists) are read
from the database at generation time, so the doc never silently drifts from the
data. Written descriptions and gotchas come from descriptions.yaml. Anything
documented-but-missing (or present-but-undocumented) is flagged in the output so
the gap is visible rather than hidden.

Usage:
    bin/gen_dict.py                    # write DATA_DICTIONARY.md
    bin/gen_dict.py --out OTHER.md
    bin/gen_dict.py --check            # exit 1 if the doc is stale (CI-friendly)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parent.parent  # project root (this script lives in bin/)
PARQUET_DIR = ROOT / "parquet_store"


def live_schema(con) -> dict[str, list[tuple[str, str]]]:
    rows = con.execute("""
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'main'
        ORDER BY table_name, ordinal_position
    """).fetchall()
    out: dict[str, list[tuple[str, str]]] = {}
    for t, c, dt in rows:
        out.setdefault(t, []).append((c, dt))
    return out


def row_count(con, table: str) -> int:
    return con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]


def parquet_info(con, path: Path) -> tuple[int, int]:
    """(column_count, row_count) read from parquet metadata."""
    p = str(path).replace("'", "''")
    ncol = con.execute(
        f"SELECT count(*) FROM (DESCRIBE SELECT * FROM read_parquet('{p}'))"
    ).fetchone()[0]
    nrow = con.execute(f"SELECT count(*) FROM read_parquet('{p}')").fetchone()[0]
    return ncol, nrow


def md_escape(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()


def build(con, desc: dict) -> str:
    schema = live_schema(con)
    db = desc.get("database", {})
    L: list[str] = []
    w = L.append

    w(f"# Data Dictionary — {db.get('name', 'database')}")
    w("")
    w("> **Generated** by `gen_dict.py` from the live DuckDB catalog + "
      "`descriptions.yaml`. Do not hand-edit; edit `descriptions.yaml` and "
      "rerun `bin/gen_dict.py`.")
    w("")
    if db.get("overview"):
        w(db["overview"].strip())
        w("")

    # Summary table
    w("## Tables at a glance")
    w("")
    w("| Table | Rows | Cols | Grain |")
    w("|---|--:|--:|---|")
    tdesc = desc.get("tables", {})
    for t in sorted(schema):
        grain = md_escape(tdesc.get(t, {}).get("grain", ""))
        w(f"| `{t}` | {row_count(con, t):,} | {len(schema[t])} | {grain} |")
    w("")

    # Gotchas
    if desc.get("gotchas"):
        w("## Analytical gotchas")
        w("")
        for g in desc["gotchas"]:
            w(f"### ⚠️ {g['title']}")
            w("")
            w(g["detail"].strip())
            w("")

    # Per-table detail
    w("## Tables")
    w("")
    for t in sorted(schema):
        meta = tdesc.get(t, {})
        documented = meta.get("columns", {})
        w(f"### `{t}`")
        w("")
        if meta.get("description"):
            w(meta["description"].strip())
            w("")
        if meta.get("grain"):
            w(f"*Grain:* {meta['grain']}  ")
        w(f"*Rows:* {row_count(con, t):,}")
        w("")
        w("| Column | Type | Description |")
        w("|---|---|---|")
        for col, dt in schema[t]:
            d = documented.get(col)
            cell = md_escape(d) if d else "_(undocumented)_"
            w(f"| `{col}` | {dt} | {cell} |")
        w("")
        # Drift: documented columns that no longer exist
        live_cols = {c for c, _ in schema[t]}
        stale = [c for c in documented if c not in live_cols]
        if stale:
            w(f"> ⚠️ Described but not in table (stale): {', '.join(stale)}")
            w("")

    # Raw parquet
    pdesc = desc.get("parquet", {})
    pdir = Path(PARQUET_DIR)
    if pdir.is_dir():
        w("## Raw cold storage (`parquet_store/`)")
        w("")
        w("Lossless originals — every FEC column as text. Useful for columns the "
          "curated tables drop (e.g. `MEMO_CD`).")
        w("")
        w("| File | Rows | Cols | Description |")
        w("|---|--:|--:|---|")
        for pq in sorted(pdir.glob("*.parquet")):
            ncol, nrow = parquet_info(con, pq)
            d = md_escape(pdesc.get(pq.name, ""))
            w(f"| `{pq.name}` | {nrow:,} | {ncol} | {d} |")
        w("")

    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate DATA_DICTIONARY.md.")
    ap.add_argument("--db", default=str(ROOT / "fec_campaign_finance.db"))
    ap.add_argument("--descriptions", default=str(ROOT / "model" / "descriptions.yaml"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "DATA_DICTIONARY.md"))
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the on-disk doc differs from freshly generated")
    args = ap.parse_args(argv)

    for f in (args.db, args.descriptions):
        if not Path(f).exists():
            sys.exit(f"Not found: {f}")
    desc = yaml.safe_load(Path(args.descriptions).read_text()) or {}

    con = duckdb.connect(args.db, read_only=True)
    content = build(con, desc)

    out = Path(args.out)
    if args.check:
        current = out.read_text() if out.exists() else ""
        if current != content:
            print(f"{out} is STALE — run bin/gen_dict.py", file=sys.stderr)
            return 1
        print(f"{out} is up to date.")
        return 0

    out.write_text(content)
    nt = len(live_schema(con))
    print(f"Wrote {out} ({nt} tables).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
