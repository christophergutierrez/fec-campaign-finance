# TEST_PLAN.md — verification of docs, schema, and SQL

**Role:** You are an auditor. The executable regression checks live in `tests/`;
this file documents the broader manual audit procedure.

**Rules:**
- **READ-ONLY. Do not modify any file.** You only observe and report.
- Run the literal commands given. Read files as needed.
- **Always open the DB read-only** so concurrent auditors don't lock each other:
  invoke the CLI as `duckdb -readonly fec_campaign_finance.db -box "..."`. (The
  scripts `bin/influence.py`, `bin/gen_dict.py`, and `bin/load_interests.py --dry-run`
  already connect read-only.) Never run a plain read-write `duckdb fec_campaign_finance.db`.
- Prefer **derive-and-compare** (compute both sides live and check equality) over
  trusting any hardcoded number. Hardcoded reference values are sanity anchors only.
- For every test, record: `status` (PASS / FAIL), the observed evidence (the actual
  numbers or text you saw), and — if FAIL or anything off — a `finding`.
- A `finding` has: `severity` ∈ {BLOCKER, MAJOR, MINOR, NIT}, a one-line
  description, the file/location, and a concrete suggested fix.
- Do not invent issues. If a test passes cleanly, say PASS with evidence. Only raise
  a finding for something actually wrong, inconsistent, or misleading.
- **Any finding that cites a file must include the exact `file:line` you confirmed
  with `grep -n`** — do not attribute text to a file you did not grep. If a phrase
  you're flagging appears in a different file than expected, cite where it ACTUALLY
  is (or state it was not found).

**Artifacts under test:** `docs/CALCULATIONS.md`, `model/fec.malloy`, `docs/DATA_DICTIONARY.md`,
`model/descriptions.yaml`, `model/committee_interests.csv`, `bin/influence.py`, `bin/gen_dict.py`,
`bin/load_interests.py`, and the DB `fec_campaign_finance.db`.

**Fixed test candidate:** `S6MI00426` (Haley Stevens, MI Senate) — an IE-heavy case.

---

## Section A — Schema ↔ data-dictionary

- **A1 (doc not stale):** Run `bin/gen_dict.py --check`. PASS iff exit code 0.
- **A2 (no undocumented columns):** `grep -c "_(undocumented)_" docs/DATA_DICTIONARY.md`
  must be 0.
- **A3 (no stale columns):** `grep -c "stale" docs/DATA_DICTIONARY.md` must be 0
  (the generator flags described-but-deleted columns with "stale").
- **A4 (row counts match):** For each of the 7 tables, compare the "Rows" value in
  docs/DATA_DICTIONARY.md to live `SELECT count(*) FROM <table>`. All must match.
- **A5 (column coverage):** Confirm the column count per table in the dictionary's
  "Tables at a glance" equals the count from
  `information_schema.columns` for that table.

## Section B — Calculation consistency (`docs/CALCULATIONS.md` ↔ `sql/views`)

- **B1 (SQL layer exists):** The canonical SQL files exist:
  `sql/views/candidate_committees.sql`, `sql/views/clean_individual_contributions.sql`,
  `sql/views/clean_independent_expenditures.sql`, and `sql/views/candidate_money.sql`.
- **B2 (channel filters match):** The four channel filters described in
  docs/CALCULATIONS.md ("the four money channels" table) must match the corresponding
  `UNION ALL` arm of `sql/views/candidate_money.sql`:
  - direct PAC excludes `('24E','24A','24C','24N')` in both
  - IE support / oppose both require `AMNDT_IND='N'` and the `SUPPORT_OPPOSE` value
- **B3 (derived-total definitions match):** In BOTH files, `raised` = individual +
  direct_pac (excludes BOTH IE channels); `total_backing` = raised + ie_support
  (excludes ie_oppose only). Confirm the wording/filters agree.
- **B4 (denominators match):** For each share metric, the denominator stated in
  docs/CALCULATIONS.md equals the denominator used by the SQL query/runtime logic
  (`pac_share`→raised, `out_of_state_share`/`small_dollar_share`→individual,
  `outside_share`→total_backing).

## Section C — SQL correctness (execute the formulas)

- **C1 (unified model runs):** Install the temp views in `sql/views/` and run a
  `count(*)` against `candidate_money`. PASS iff it executes without error.
- **C2 (Malloy SQL == influence.py):** For candidate `S6MI00426`, compute from the
  `candidate_money` CTE: individual, direct_pac, ie_support, raised
  (= individual+direct_pac), total_backing (= all except ie_oppose). **Round each SQL
  sum to whole dollars with `ROUND(sum(amount),0)`** before comparing — `influence.py`
  displays dollars rounded to the nearest integer, so compare whole-dollar to
  whole-dollar (a sub-$1 difference is display rounding, not a defect; do NOT report
  it). Then run `bin/influence.py S6MI00426` and read section 1 + section 7. The four
  whole-dollar numbers **must match** between the SQL and the tool. Report both
  columns side by side.
- **C3 (IE double-count gotcha is real):** Run EXACTLY this query (use
  `category = 'Israel-aligned'` precisely — do NOT include other categories, all of
  dim_group_mappings, or name matching):
  ```
  SELECT f.TRANSACTION_TP, count(*) n, sum(f.AMOUNT) amt
  FROM fact_contributions f
  JOIN dim_group_mappings g ON g.CMTE_ID = f.SOURCE_CMTE_ID
  WHERE g.custom_category = 'Israel-aligned'
  GROUP BY 1 ORDER BY amt DESC;
  ```
  PASS iff types `24K` (direct), `24E` (IE-for) and `24A` (IE-against) are all
  present, AND re-running with `AND f.TRANSACTION_TP NOT IN ('24E','24A','24C','24N')`
  removes exactly the `24E` and `24A` rows (leaving the direct types). Report the
  per-type rows you saw.
- **C4 (IE junk filter):** Compare `sum(AMOUNT)` over ALL of
  `fact_independent_expenditures` (raw, expect ≈$39.2B incl. junk self-filings) vs the
  clean total (`AMNDT_IND='N'` AND join to `dim_committees`; expect ≈$595M). PASS iff
  raw ≫ clean; the clean figure is **~2 orders of magnitude (≈66×) smaller**. Report
  both figures and the ratio.
- **C5 (interest bloc):** Compute Israel-aligned IE support for `S6MI00426`
  (join `dim_group_mappings` category='Israel-aligned', `SUPPORT_OPPOSE='S'`,
  `AMNDT_IND='N'`). Confirm it equals the AIPAC/UDP figure shown in
  `bin/influence.py S6MI00426` section 4b / section 7 (UNITED DEMOCRACY PROJECT line).

## Section D — Documented gotchas are real

- **D1 (different join keys):** Confirm `fact_contributions` has `SOURCE_CMTE_ID` +
  `TARGET_CAND_ID`, and `fact_individual_contributions` has `TARGET_CMTE_ID` (NOT
  `CMTE_ID`), via `DESCRIBE`.
- **D2 (MEMO_CD location):** Confirm `MEMO_CD` is present in
  `read_parquet('parquet_store/raw_indiv.parquet')` and filtered by
  `sql/views/clean_individual_contributions.sql`.
- **D3 (interest dict loaded):** `SELECT count(*) FROM dim_group_mappings` equals the
  number of valid data rows in `model/committee_interests.csv`, and the per-category counts
  match `bin/load_interests.py --dry-run`.

## Section E — Runtime sanity (`influence.py`)

- **E1 (profile runs):** `bin/influence.py "leger fernandez"` exits 0 and prints
  sections 1–7.
- **E2 (rank runs):** `bin/influence.py --rank --state NM` exits 0 and prints a ranked
  table with a headline per member.

---

## Report format (return EXACTLY this structure)

```
SUMMARY: <n> PASS, <n> FAIL out of 22 tests.

RESULTS:
A1: PASS — exit 0
A2: PASS — 0 undocumented
... (one line per test ID A1..E2, with evidence)

FINDINGS:
1. [SEVERITY] <description> — <file:location> — fix: <suggestion>
2. ...
(or "FINDINGS: none")
```

Return only that report as your final message. It is data for the coordinator, not a
human-facing summary — be terse and precise.
