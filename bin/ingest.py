#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb>=1.1",
#     "requests>=2.31",
# ]
# ///
"""
FEC Campaign Finance Database — Phase 1 ingestion pipeline.

Downloads FEC bulk data for a given election cycle, lands the raw pipe-delimited
files into Parquet "cold storage" with full fidelity, then builds a curated star
schema (dim / bridge / fact tables) in a DuckDB database.

Column layouts come straight from the official FEC header files
(https://www.fec.gov/files/bulk-downloads/data_dictionaries/) — see COLUMNS below.
These are the source of truth; do NOT rely on positional guesses.

Incremental: FEC bulk files are full snapshots (no row-level deltas), so re-runs
use HTTP conditional GET (ETag / Last-Modified, cached in data/.manifest.json).
Unchanged snapshots return 304 and are skipped entirely; only changed datasets
are re-landed and their tables rebuilt. Safe to run on a cron.

Usage:
    ./ingest.py                       # full pipeline, cycle 2026, all datasets
    ./ingest.py --cycle 2024
    ./ingest.py --datasets cn cm ccl  # small files only (skip the big itemized ones)
    ./ingest.py --force               # ignore validators; re-download everything
    ./ingest.py --raw-only            # land parquet, skip building curated tables
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import duckdb
import requests

HERE = Path(__file__).resolve().parent.parent  # project root (this script lives in bin/)
RAW_DIR = HERE / "data" / "raw"          # downloaded .zip files
STAGING_DIR = HERE / "data" / "staging"  # extracted .txt files
PARQUET_DIR = HERE / "parquet_store"     # cold storage, one raw_<name>.parquet per dataset
DB_PATH = HERE / "fec_campaign_finance.db"
MANIFEST_PATH = HERE / "data" / ".manifest.json"  # per-zip ETag/Last-Modified cache

BASE_URL = "https://www.fec.gov/files/bulk-downloads"

# Official FEC column orders. Keyed by dataset id.
#   zip:  filename template on the FEC server ({yr} = 2-digit cycle year)
#   txt:  name of the file inside the zip
#   cols: full column list, in order, from the *_header_file.csv
COLUMNS: dict[str, dict] = {
    "cn": {
        "zip": "cn{yr}.zip",
        "txt": "cn.txt",
        "cols": [
            "CAND_ID", "CAND_NAME", "CAND_PTY_AFFILIATION", "CAND_ELECTION_YR",
            "CAND_OFFICE_ST", "CAND_OFFICE", "CAND_OFFICE_DISTRICT", "CAND_ICI",
            "CAND_STATUS", "CAND_PCC", "CAND_ST1", "CAND_ST2", "CAND_CITY",
            "CAND_ST", "CAND_ZIP",
        ],
    },
    "cm": {
        "zip": "cm{yr}.zip",
        "txt": "cm.txt",
        "cols": [
            "CMTE_ID", "CMTE_NM", "TRES_NM", "CMTE_ST1", "CMTE_ST2", "CMTE_CITY",
            "CMTE_ST", "CMTE_ZIP", "CMTE_DSGN", "CMTE_TP", "CMTE_PTY_AFFILIATION",
            "CMTE_FILING_FREQ", "ORG_TP", "CONNECTED_ORG_NM", "CAND_ID",
        ],
    },
    "ccl": {
        "zip": "ccl{yr}.zip",
        "txt": "ccl.txt",
        "cols": [
            "CAND_ID", "CAND_ELECTION_YR", "FEC_ELECTION_YR", "CMTE_ID",
            "CMTE_TP", "CMTE_DSGN", "LINKAGE_ID",
        ],
    },
    # PAC / committee -> candidate contributions (the directed-edge fact table).
    "pas2": {
        "zip": "pas2{yr}.zip",
        "txt": "itpas2.txt",
        "cols": [
            "CMTE_ID", "AMNDT_IND", "RPT_TP", "TRANSACTION_PGI", "IMAGE_NUM",
            "TRANSACTION_TP", "ENTITY_TP", "NAME", "CITY", "STATE", "ZIP_CODE",
            "EMPLOYER", "OCCUPATION", "TRANSACTION_DT", "TRANSACTION_AMT",
            "OTHER_ID", "CAND_ID", "TRAN_ID", "FILE_NUM", "MEMO_CD",
            "MEMO_TEXT", "SUB_ID",
        ],
    },
    # Individual -> committee itemized contributions. NOTE: different layout from
    # pas2 — no CAND_ID, and SUB_ID is the last (21st) column.
    "indiv": {
        "zip": "indiv{yr}.zip",
        "txt": "itcont.txt",
        "cols": [
            "CMTE_ID", "AMNDT_IND", "RPT_TP", "TRANSACTION_PGI", "IMAGE_NUM",
            "TRANSACTION_TP", "ENTITY_TP", "NAME", "CITY", "STATE", "ZIP_CODE",
            "EMPLOYER", "OCCUPATION", "TRANSACTION_DT", "TRANSACTION_AMT",
            "OTHER_ID", "TRAN_ID", "FILE_NUM", "MEMO_CD", "MEMO_TEXT", "SUB_ID",
        ],
    },
    # Independent expenditures: committee (super PAC) spending FOR/AGAINST a
    # candidate, with no money touching the candidate's committee — so it is
    # absent from both pas2 and indiv. UNLIKE the others this is a real CSV:
    # comma-delimited, double-quoted, WITH a header row, and served as a bare
    # .csv (not a .zip). Dates are DD-MON-YY, not MMDDYYYY. Hence csv=True and a
    # branch in download/extract/land_parquet. Column order is taken verbatim
    # from the file's own header; we keep all 23 columns losslessly.
    "ie": {
        "csv": True,
        "file": "independent_expenditure_{cycle}.csv",
        "cols": [
            "cand_id", "cand_name", "spe_id", "spe_nam", "ele_type",
            "can_office_state", "can_office_dis", "can_office", "cand_pty_aff",
            "exp_amo", "exp_date", "agg_amo", "sup_opp", "pur", "pay",
            "file_num", "amndt_ind", "tran_id", "image_num", "receipt_dat",
            "fec_election_yr", "prev_file_num", "dissem_dt",
        ],
    },
}

ALL_DATASETS = list(COLUMNS.keys())


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {}


def save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_cached_file(path: Path, entry: dict) -> None:
    if not path.exists() or not entry:
        return
    expected_size = entry.get("content_length")
    if expected_size is not None and path.stat().st_size != expected_size:
        raise RuntimeError(
            f"cached file size mismatch for {path.name}: "
            f"{path.stat().st_size} != {expected_size}"
        )
    expected_hash = entry.get("sha256")
    if expected_hash and sha256_file(path) != expected_hash:
        raise RuntimeError(f"cached file hash mismatch for {path.name}")


def download(dataset: str, cycle: str, force: bool, manifest: dict) -> tuple[Path, bool]:
    """Conditionally fetch the dataset file into data/raw/.

    Sends If-None-Match / If-Modified-Since from the manifest so the server can
    answer 304 when the snapshot is unchanged — that's the incremental win, since
    FEC bulk files are full snapshots regenerated periodically (no row deltas).

    Most datasets ship as zips; the IE dataset is a bare .csv (csv=True), so the
    filename/URL are built differently but the conditional-GET logic is shared.

    Returns (local_path, changed). changed=False means skip reprocessing.
    """
    spec = COLUMNS[dataset]
    if spec.get("csv"):
        zip_name = spec["file"].format(cycle=cycle)
    else:
        yr = cycle[2:]
        zip_name = spec["zip"].format(yr=yr)
    url = f"{BASE_URL}/{cycle}/{zip_name}"
    dest = RAW_DIR / zip_name
    entry = manifest.get(zip_name, {})
    validate_cached_file(dest, entry)

    headers: dict[str, str] = {}
    # Only attempt a conditional request if we still hold the matching local zip.
    if dest.exists() and not force:
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]

    with requests.get(url, stream=True, timeout=120, headers=headers) as r:
        if r.status_code == 304:
            print(f"  [304]   {zip_name} unchanged ({dest.stat().st_size:,} bytes)")
            return dest, False
        r.raise_for_status()
        tmp = dest.with_suffix(".zip.part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):  # 1 MiB chunks
                f.write(chunk)
        tmp.replace(dest)  # atomic: a partial download never looks complete
        # Record validators from the final (post-redirect) response.
        manifest[zip_name] = {
            "etag": r.headers.get("ETag"),
            "last_modified": r.headers.get("Last-Modified"),
            "content_length": dest.stat().st_size,
            "sha256": sha256_file(dest),
        }
    print(f"  [get]   {zip_name} updated ({dest.stat().st_size:,} bytes)")
    return dest, True


def extract(zip_path: Path, dataset: str) -> Path:
    """Extract the dataset's .txt from its zip into data/staging/.

    CSV datasets (csv=True) aren't zipped — the downloaded file is the data file
    itself, so it's returned directly with nothing to unpack."""
    if COLUMNS[dataset].get("csv"):
        return zip_path
    txt_name = COLUMNS[dataset]["txt"]
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if txt_name not in names:
            raise FileNotFoundError(
                f"{txt_name!r} not in {zip_path.name}; archive holds {names}"
            )
        zf.extract(txt_name, STAGING_DIR)
    return STAGING_DIR / txt_name


def land_parquet(con: duckdb.DuckDBPyConnection, dataset: str, txt_path: Path) -> Path:
    """Read the raw FEC file (all columns as VARCHAR for full fidelity) and write
    it to parquet_store/raw_<dataset>.parquet."""
    spec = COLUMNS[dataset]
    cols = spec["cols"]
    out = PARQUET_DIR / f"raw_{dataset}.parquet"
    # Inline names/paths: DuckDB's COPY ... TO target and the names= list don't
    # bind cleanly as positional params. These are our own constants, not user
    # input. names_sql is a SQL array literal of column identifiers.
    names_sql = "[" + ", ".join(f"'{c}'" for c in cols) + "]"
    src = str(txt_path).replace("'", "''")
    dst = str(out).replace("'", "''")
    if spec.get("csv"):
        # IE dataset: a real RFC-style CSV — comma-delimited, double-quoted,
        # with a header row. skip=1 drops that header so our pinned `names`
        # (not the file's, in case FEC reorders/renames) define the schema.
        read = (
            f"read_csv('{src}', delim = ',', header = false, skip = 1, "
            f"quote = '\"', escape = '\"', encoding = 'utf-8', "
            f"all_varchar = true, names = {names_sql})"
        )
    else:
        # FEC bulk files: '|' delimited, no header, no quoting (employer/
        # occupation fields contain stray double-quotes), latin-1 encoded.
        # all_varchar keeps cold storage lossless; typing is in the curated layer.
        read = (
            f"read_csv('{src}', delim = '|', header = false, quote = '', "
            f"escape = '', encoding = 'latin-1', all_varchar = true, "
            f"names = {names_sql})"
        )
    con.execute(
        f"""
        COPY (
            SELECT * FROM {read}
        ) TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    n = con.execute(f"SELECT count(*) FROM read_parquet('{dst}')").fetchone()[0]
    print(f"  [parquet] raw_{dataset}.parquet — {n:,} rows")
    return out


TABLES_BY_DATASET = {
    "cn": "dim_candidates",
    "cm": "dim_committees",
    "ccl": "bridge_candidate_committee",
    "pas2": "fact_contributions",
    "indiv": "fact_individual_contributions",
    "ie": "fact_independent_expenditures",
}


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='main' AND table_name = ?",
        [table],
    ).fetchone()[0] > 0


def datasets_needing_tables(con: duckdb.DuckDBPyConnection, datasets: list[str]) -> set[str]:
    needed = set()
    for ds in datasets:
        table = TABLES_BY_DATASET[ds]
        if (PARQUET_DIR / f"raw_{ds}.parquet").exists() and not table_exists(con, table):
            needed.add(ds)
    return needed


def build_curated(con: duckdb.DuckDBPyConnection, rebuild: set[str]) -> None:
    """(Re)build the star schema for datasets that changed this run.

    Changed datasets and missing target tables are rebuilt. Unchanged existing
    tables persist untouched, which keeps repeated runs cheap."""

    def have(ds: str) -> bool:
        return ds in rebuild and (PARQUET_DIR / f"raw_{ds}.parquet").exists()

    p = lambda ds: f"read_parquet('{PARQUET_DIR / f'raw_{ds}.parquet'}')"  # noqa: E731

    if have("cn"):
        con.execute(f"""
            CREATE OR REPLACE TABLE dim_candidates AS
            SELECT
                CAND_ID,
                CAND_NAME,
                CAND_PTY_AFFILIATION,
                TRY_CAST(CAND_ELECTION_YR AS INTEGER) AS CAND_ELECTION_YR,
                CAND_OFFICE_ST,
                CAND_OFFICE,                       -- 'H' (House) / 'S' (Senate) / 'P'
                CAND_OFFICE_DISTRICT,
                CAND_ICI,                          -- Incumbent/Challenger/Open
                CAND_STATUS,
                CAND_PCC                           -- principal campaign committee
            FROM {p('cn')}
        """)
        print("  [table] dim_candidates")

    if have("cm"):
        con.execute(f"""
            CREATE OR REPLACE TABLE dim_committees AS
            SELECT
                CMTE_ID, CMTE_NM, CMTE_TP, CMTE_DSGN, CMTE_PTY_AFFILIATION,
                ORG_TP, CONNECTED_ORG_NM, CAND_ID AS AFFILIATED_CAND_ID
            FROM {p('cm')}
        """)
        print("  [table] dim_committees")

    if have("ccl"):
        con.execute(f"""
            CREATE OR REPLACE TABLE bridge_candidate_committee AS
            SELECT
                CAND_ID,
                CMTE_ID,
                TRY_CAST(FEC_ELECTION_YR AS INTEGER) AS FEC_ELECTION_YR,
                CMTE_TP, CMTE_DSGN, LINKAGE_ID
            FROM {p('ccl')}
        """)
        print("  [table] bridge_candidate_committee")

    if have("pas2"):
        # The directed money-flow edge list: committee (source) -> candidate (target).
        con.execute(f"""
            CREATE OR REPLACE TABLE fact_contributions AS
            SELECT
                TRY_CAST(SUB_ID AS BIGINT)              AS SUB_ID,        -- PK
                CMTE_ID                                 AS SOURCE_CMTE_ID,
                CAND_ID                                 AS TARGET_CAND_ID,
                TRY_CAST(TRANSACTION_AMT AS DECIMAL(14,2)) AS AMOUNT,
                TRY_STRPTIME(TRANSACTION_DT, '%m%d%Y')::DATE AS TRANSACTION_DT,
                TRANSACTION_TP, ENTITY_TP, OTHER_ID,
                IMAGE_NUM,                              -- back-reference to scanned filing
                FILE_NUM, TRAN_ID
            FROM {p('pas2')}
        """)
        print("  [table] fact_contributions (PAC -> candidate edges)")

    if have("indiv"):
        # Individual -> committee itemizations. Separate fact table because the
        # edge endpoints differ (donor name + committee, no candidate).
        con.execute(f"""
            CREATE OR REPLACE TABLE fact_individual_contributions AS
            SELECT
                TRY_CAST(SUB_ID AS BIGINT)              AS SUB_ID,        -- PK
                CMTE_ID                                 AS TARGET_CMTE_ID,
                NAME                                    AS CONTRIBUTOR_NAME,
                CITY, STATE, ZIP_CODE, EMPLOYER, OCCUPATION,
                TRY_CAST(TRANSACTION_AMT AS DECIMAL(14,2)) AS AMOUNT,
                TRY_STRPTIME(TRANSACTION_DT, '%m%d%Y')::DATE AS TRANSACTION_DT,
                TRANSACTION_TP, ENTITY_TP, OTHER_ID, IMAGE_NUM, FILE_NUM,
                TRAN_ID, MEMO_CD
            FROM {p('indiv')}
        """)
        print("  [table] fact_individual_contributions (individual -> committee edges)")

    if have("ie"):
        # Independent expenditures: a spender committee (super PAC) -> candidate,
        # tagged support ('S') or oppose ('O'). This money never enters the
        # candidate's committee, so it's invisible in the other two fact tables.
        # The IE file has no SUB_ID; file_num||'-'||tran_id is unique per row
        # (verified) and serves as the synthetic PK, mirroring the SUB_ID role.
        # Dates are DD-MON-YY here (vs MMDDYYYY elsewhere) -> '%d-%b-%y'.
        con.execute(f"""
            CREATE OR REPLACE TABLE fact_independent_expenditures AS
            SELECT
                file_num || '-' || tran_id              AS IE_ID,          -- PK
                spe_id                                  AS SPENDER_CMTE_ID,
                cand_id                                 AS TARGET_CAND_ID,
                upper(sup_opp)                          AS SUPPORT_OPPOSE,  -- 'S'/'O'
                TRY_CAST(exp_amo AS DECIMAL(14,2))      AS AMOUNT,
                TRY_STRPTIME(exp_date, '%d-%b-%y')::DATE AS EXPENDITURE_DT,
                TRY_CAST(agg_amo AS DECIMAL(14,2))      AS AGGREGATE_AMOUNT,
                pur                                     AS PURPOSE,
                pay                                     AS PAYEE,
                spe_nam                                 AS SPENDER_NAME,
                cand_name                               AS TARGET_CAND_NAME,
                ele_type                                AS ELECTION_TYPE,
                TRY_STRPTIME(dissem_dt, '%d-%b-%y')::DATE AS DISSEMINATION_DT,
                image_num                               AS IMAGE_NUM,
                file_num                                AS FILE_NUM,
                tran_id                                 AS TRAN_ID,
                amndt_ind                               AS AMNDT_IND,
                prev_file_num                           AS PREV_FILE_NUM
            FROM {p('ie')}
        """)
        print("  [table] fact_independent_expenditures (super PAC -> candidate, S/O)")

    # Phase-2 placeholder: custom interest-group classification (e.g. AIPAC ->
    # 'Israel-related'). Created empty so downstream joins have a stable target.
    con.execute("""
        CREATE TABLE IF NOT EXISTS dim_group_mappings (
            CMTE_ID         VARCHAR,
            custom_category VARCHAR,
            notes           VARCHAR
        )
    """)
    print("  [table] dim_group_mappings (empty Phase-2 placeholder)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FEC bulk-data ingestion (Phase 1)")
    ap.add_argument("--cycle", default="2026", help="election cycle year, e.g. 2026")
    ap.add_argument("--datasets", nargs="+", choices=ALL_DATASETS, default=ALL_DATASETS,
                    help="subset of datasets to process")
    ap.add_argument("--force", action="store_true", help="re-download cached zips")
    ap.add_argument("--raw-only", action="store_true",
                    help="land parquet only; skip building curated tables")
    ap.add_argument("--rebuild-curated", action="store_true",
                    help="rebuild curated tables from existing parquet")
    args = ap.parse_args(argv)

    if len(args.cycle) != 4 or not args.cycle.isdigit():
        ap.error("--cycle must be a 4-digit year, e.g. 2026")

    for d in (RAW_DIR, STAGING_DIR, PARQUET_DIR):
        d.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest()
    existing_cycle = manifest.get("_cycle")
    if existing_cycle and existing_cycle != args.cycle:
        sys.exit(
            f"Existing store is for cycle {existing_cycle}; refusing to mix cycle "
            f"{args.cycle}. Remove generated data/DB artifacts for a fresh cycle."
        )
    manifest["_cycle"] = args.cycle

    con = duckdb.connect(str(DB_PATH))
    changed: set[str] = set()
    failed: list[str] = []
    try:
        for ds in args.datasets:
            print(f"[{ds}] cycle {args.cycle}")
            try:
                zip_path, did_change = download(ds, args.cycle, args.force, manifest)
                parquet = PARQUET_DIR / f"raw_{ds}.parquet"
                # Reprocess if the snapshot changed, or if the parquet is missing
                # (e.g. first run, or a 304 with no prior landing).
                if did_change or not parquet.exists():
                    txt_path = extract(zip_path, ds)
                    land_parquet(con, ds, txt_path)
                    # Drop the extracted .txt — it's a pure intermediate now that
                    # the data lives in Parquet; re-extracted on demand if needed.
                    # For csv datasets extract() returns the downloaded file
                    # itself (the conditional-GET cache), so leave that in place.
                    if txt_path != zip_path:
                        txt_path.unlink(missing_ok=True)
                    changed.add(ds)
                else:
                    print("  [skip]  already current — no reprocessing")
            except requests.HTTPError as e:
                print(f"  [skip]  download failed: {e}", file=sys.stderr)
                failed.append(ds)
            except Exception as e:  # noqa: BLE001 — keep going on per-dataset failure
                print(f"  [error] {ds}: {e}", file=sys.stderr)
                failed.append(ds)

        save_manifest(manifest)

        if not args.raw_only:
            missing_tables = datasets_needing_tables(con, args.datasets)
            rebuild = set(args.datasets) if args.rebuild_curated else changed | missing_tables
            if rebuild:
                print(f"[curated] rebuilding: {', '.join(sorted(rebuild))}")
            else:
                print("[curated] nothing changed — tables already current")
            build_curated(con, rebuild)

        if failed:
            print(f"\nPhase 1 incomplete; failed dataset(s): {', '.join(failed)}",
                  file=sys.stderr)
            return 1

        print("\nPhase 1 complete.")
        print(f"  DuckDB : {DB_PATH}")
        print(f"  Parquet: {PARQUET_DIR}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
