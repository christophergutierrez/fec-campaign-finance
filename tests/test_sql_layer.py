from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "fec_campaign_finance.db"


def duckdb(sql: str) -> str:
    result = subprocess.run(
        ["duckdb", "-readonly", str(DB), "-csv", "-c", sql],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout


def sql_file(path: str) -> str:
    return (ROOT / "sql" / path).read_text()


class SqlLayerTests(unittest.TestCase):
    @unittest.skipUnless(DB.exists(), "local DuckDB artifact is required")
    def test_canonical_views_execute(self) -> None:
        setup = "\n".join([
            sql_file("views/candidate_committees.sql"),
            sql_file("views/clean_individual_contributions.sql"),
            sql_file("views/clean_independent_expenditures.sql"),
            sql_file("views/candidate_money.sql"),
        ])
        out = duckdb(setup + "\nSELECT channel, count(*) FROM candidate_money GROUP BY 1;")
        self.assertIn("individual", out)
        self.assertIn("direct_pac", out)
        self.assertIn("ie_support", out)

    @unittest.skipUnless(DB.exists(), "local DuckDB artifact is required")
    def test_candidate_money_filters_memo_rows_and_non_individual_entities(self) -> None:
        setup = "\n".join([
            sql_file("views/candidate_committees.sql"),
            sql_file("views/clean_individual_contributions.sql"),
            sql_file("views/clean_independent_expenditures.sql"),
            sql_file("views/candidate_money.sql"),
        ])
        out = duckdb(setup + """
            SELECT
              count(*) FILTER (WHERE channel='individual' AND amount IS NULL) AS null_amounts,
              count(*) FILTER (WHERE channel='individual' AND contributor_name IS NULL) AS null_names
            FROM candidate_money;
        """)
        self.assertIn("0,0", out)

    @unittest.skipUnless(DB.exists(), "local DuckDB artifact is required")
    def test_candidate_committees_excludes_joint_and_unauthorized_links(self) -> None:
        setup = sql_file("views/candidate_committees.sql")
        out = duckdb(setup + """
            SELECT
              count(*) FILTER (WHERE CMTE_DSGN NOT IN ('P', 'A')) AS bad_designations,
              count(*) FILTER (WHERE CMTE_TP NOT IN ('H', 'S', 'P')) AS bad_types
            FROM candidate_committees;
        """)
        self.assertIn("0,0", out)

    @unittest.skipUnless(DB.exists(), "local DuckDB artifact is required")
    def test_data_dictionary_ie_counts_are_current(self) -> None:
        out = duckdb("""
            SELECT
              (SELECT count(*) FROM fact_independent_expenditures) AS db_ie,
              (SELECT count(*) FROM read_parquet('parquet_store/raw_ie.parquet')) AS raw_ie;
        """)
        self.assertIn("9609,9609", out)


if __name__ == "__main__":
    unittest.main()
