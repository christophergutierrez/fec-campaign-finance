# CLAUDE.md — FEC Campaign Finance DB

Local **FEC campaign-finance database** (DuckDB, 2026 cycle). The owner asks
questions in **natural language** and expects you to **answer by querying the data
yourself** — run the queries, interpret results, state the answer. Show SQL only
when it aids understanding.

Run commands and queries from the **project root** (paths below are relative to it).
The layout: `bin/` scripts · `model/` semantic layer + curated inputs · `docs/`
documentation · `data/` + `parquet_store/` + `fec_campaign_finance.db` data artifacts.

## How to query

```bash
duckdb -readonly fec_campaign_finance.db -box "SELECT count(*) FROM fact_contributions;"
```

Formats: `-box` (default for analysis), `-csv`, `-json`, `-markdown`, `-line`.
Lossless originals (every raw FEC column as text) are in `parquet_store/raw_*.parquet`,
queryable via `read_parquet(...)`.

**The DB is read-only.** Data comes only from `bin/ingest.py`; the sole exception is
`dim_group_mappings`, written by `bin/load_interests.py`. Never mutate by hand.

## Schema, columns, codes, and gotchas → `docs/DATA_DICTIONARY.md`

That file is generated from the live DB (`bin/gen_dict.py`). **Read it before
aggregating** — it documents every table/column, the FEC code meanings, and the
analytical traps. Use `bin/gen_dict.py --check` to verify it is current.

For **how metrics are calculated** (raised, total backing, out-of-state %, interest
blocs, etc.), use the executable SQL layer in `sql/views/` and `sql/queries/`.
`docs/CALCULATIONS.md` explains those definitions in prose, and `model/fec.malloy`
is a semantic consumer of the same concepts.

Two footguns worth stating up front (the rest are in the dictionary):
- The **two contribution fact tables use different join keys** — `fact_contributions`
  is committee→candidate; `fact_individual_contributions` is individual→committee and
  joins on **`TARGET_CMTE_ID`**, not `CMTE_ID`.
- **`fact_contributions` also contains independent-expenditure rows** (`24E`/`24A`)
  that overlap `fact_independent_expenditures` — filter to true direct money and don't
  double-count. (Details + correct filters in the dictionary.)

## Tooling (run from project root; scripts live in `bin/`)

- `bin/ingest.py` — refresh/load data (incremental; `--cycle`, `--datasets`, `--force`). See `README.md`.
- `bin/load_interests.py` — load `model/committee_interests.csv` → `dim_group_mappings`.
- `bin/gen_dict.py` — regenerate `docs/DATA_DICTIONARY.md` (run after any schema change; `--check` for CI).
- `bin/influence.py` — per-candidate funding-influence profile, or `--rank` a cohort.
