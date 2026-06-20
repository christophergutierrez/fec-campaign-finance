# Congress Campaign Finance Database — Phase 1

Local relational store of **FEC bulk data** for U.S. House & Senate campaign
finance, built on **DuckDB + Parquet**. Phase 1 covers ingestion, a star-schema
layout, and a reproducible/idempotent update pipeline.

## Project layout

Run all commands from the project root.

```
bin/      executable tools (uv self-contained scripts)
            ingest.py         build/refresh the DB from FEC bulk data
            load_interests.py load the committee→interest dictionary
            gen_dict.py       regenerate docs/DATA_DICTIONARY.md from the live DB
            influence.py      funding-influence profile / cohort ranking
                              (--export-infographic DIR writes chart-ready angle JSON)
sql/      executable semantic layer (canonical temp views + report queries)
model/    semantic layer + curated inputs
            fec.malloy             semantic model (joins + measures)
            descriptions.yaml      curated prose for the data dictionary
            committee_interests.csv  curated committee→interest tags
docs/     DATA_DICTIONARY.md (generated) · CALCULATIONS.md · TEST_PLAN.md
data/     download cache + ingest intermediates (raw/, staging/, .manifest.json)
parquet_store/            lossless raw cold storage (one raw_<ds>.parquet per dataset)
fec_campaign_finance.db   curated DuckDB star schema
```

`data/`, `parquet_store/`, and `*.db` are git-ignored — all re-creatable via
`bin/ingest.py`. See `docs/DATA_DICTIONARY.md` for the schema and `docs/CALCULATIONS.md`
for how metrics are defined.

The generated data includes public FEC donor names, ZIPs, employers, and
occupations. Treat DB/parquet/raw artifacts as local analysis data, not source
files to redistribute accidentally.

## Quick start

```bash
bin/ingest.py                       # full pipeline, cycle 2026, all datasets
bin/ingest.py --cycle 2024          # requires a fresh generated store
bin/ingest.py --datasets cn cm ccl  # small reference files only
bin/ingest.py --raw-only            # land parquet, skip curated tables
bin/ingest.py --force               # re-download cached zips
```

`bin/ingest.py` is a [uv](https://docs.astral.sh/uv/) self-contained script — its
dependencies (`duckdb`, `requests`) are declared inline and resolved on first run.
No virtualenv to manage.

In locked-down/offline environments, create the environment ahead of time or set a
writable cache path such as `UV_CACHE_DIR=/tmp/uv-cache`. The project also has a
`pyproject.toml` with the same dependency bounds for repeatable setup.

## Infographic export

`bin/influence.py` can emit per-candidate, chart-ready JSON — one file per funding
"angle" (donor-size, geography, money composition, PAC roster, and outside-spending
signals), each a self-contained brief for one static chart:

```bash
uv run bin/influence.py S6MI00426 --export-infographic infographics/stevens
```

Output lands in the given directory as `NN-<angle-id>.json`. `infographics/` is
git-ignored — the files are regenerable output, not source. Which angles appear
differs per candidate: structural angles always emit, while outside-money/interest
signals appear only when they clear a materiality floor. See
`docs/INFOGRAPHIC_EXPORT.md` for the angle catalog, inclusion rules, and JSON schema.

## Data flow

```
FEC server  ──download──▶  data/raw/*.zip
                          │ unzip
                          ▼
                  data/staging/*.txt   (pipe-delimited, no header, latin-1)
                          │ DuckDB read_csv (all VARCHAR, lossless)
                          ▼
              parquet_store/raw_<ds>.parquet   ← cold storage
                          │ typed SELECT
                          ▼
              fec_campaign_finance.db          ← curated star schema
```

Raw Parquet is the lossless cold layer; the DuckDB tables are the typed,
query-ready layer.

## Incremental updates

FEC bulk files are **full snapshots** (regenerated ~weekly), not row-level delta
feeds — true per-row incrementality only exists at the individual-filing API, not
the bulk endpoint. So "incremental" here is two cheap, correct optimizations:

1. **Conditional download.** Each zip's `ETag`/`Last-Modified` is cached in
   `data/.manifest.json`. Re-runs send `If-None-Match`/`If-Modified-Since`; an
   unchanged snapshot returns **HTTP 304** and is skipped without downloading —
   the big win for the multi-GB `indiv` file.
2. **Rebuild only what changed.** Only datasets that actually changed are
   re-landed to Parquet and rebuilt (`CREATE OR REPLACE`). Replace-on-change (vs.
   row append) is deliberate: the snapshot is current truth, so superseded/amended
   records can't linger.

This makes the script **idempotent and cron-safe** — run it daily and it does
near-zero work until FEC posts new data. Use `--force` to bypass validators and
re-pull everything.

```cron
# refresh every day at 06:30
30 6 * * * cd ~/git_home/fec-campaign-finance && bin/ingest.py >> ingest.log 2>&1
```

## Datasets ingested

| id      | FEC file     | grain                                   |
|---------|--------------|-----------------------------------------|
| `cn`    | `cn.txt`     | candidate master                        |
| `cm`    | `cm.txt`     | committee master                        |
| `ccl`   | `ccl.txt`    | candidate ↔ committee linkage           |
| `pas2`  | `itpas2.txt` | committee → candidate contributions     |
| `indiv` | `itcont.txt` | individual → committee contributions    |
| `ie`    | `independent_expenditure_<cycle>.csv` | independent expenditures |

> `indiv` is large (multi-GB uncompressed). Omit it with `--datasets cn cm ccl pas2`
> while iterating.

## Schema (star)

```
   dim_committees                 dim_candidates
        │                                │
        │ SOURCE_CMTE_ID   TARGET_CAND_ID │
        └──────────────┬─────────────────┘
                       ▼
              fact_contributions         ← committee→candidate edge list (pas2)
                       ▲
                       │ (future N:1)
              dim_group_mappings         ← committee→interest tags (load_interests.py)

  bridge_candidate_committee            ← cand↔cmte linkage (ccl)
  fact_individual_contributions         ← individual→committee edges (indiv)
```

- **`fact_contributions`** is a directed edge list (`SOURCE_CMTE_ID → TARGET_CAND_ID`,
  `AMOUNT`, `TRANSACTION_DT`, `IMAGE_NUM`) — drops straight into NetworkX/igraph/D3.
- **`dim_group_mappings`** maps `CMTE_ID` → interest category (e.g. `Israel-aligned`,
  `Crypto/digital assets`); curated in `model/committee_interests.csv`, loaded by
  `bin/load_interests.py`. (A third fact table, `fact_independent_expenditures`, was
  added after Phase 1 — see `docs/DATA_DICTIONARY.md`.)

## Column mapping is authoritative

Column orders come verbatim from the official FEC header files
(`bulk-downloads/data_dictionaries/<ds>_header_file.csv`), pinned in
`COLUMNS` in `bin/ingest.py`. Note `itpas2` and `itcont` have **different** layouts
(`itcont` has no `CAND_ID`; `SUB_ID` shifts to the last column) — handled per-file.

## Notes / Phase-2 hooks

- FEC bulk files are full snapshots replaced periodically; "incremental" here
  means cached-download reuse + idempotent rebuilds, not row-level diffing.
- Dates parsed with `TRY_STRPTIME(..., '%m%d%Y')`; blanks → NULL rather than error.
- Amounts cast to `DECIMAL(14,2)` via `TRY_CAST`.
