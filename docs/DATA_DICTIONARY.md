# Data Dictionary — fec_campaign_finance.db

> **Generated** by `gen_dict.py` from the live DuckDB catalog + `descriptions.yaml`. Do not hand-edit; edit `descriptions.yaml` and rerun `bin/gen_dict.py`.

Local FEC campaign-finance database for the **2026 election cycle** (House +
Senate, some presidential). A DuckDB star schema: two dimension tables
(entities), one bridge, one classification hook, and three fact tables (money
flows). Built from FEC bulk data by `bin/ingest.py`; treat as read-only (the one
exception is `dim_group_mappings`, written by `bin/load_interests.py`).

Every curated table is backed by a lossless `parquet_store/raw_*.parquet`
"cold storage" file that preserves every original FEC column as text. Columns
dropped or renamed in the curated tables (e.g. MEMO_CD) can be recovered there.

## Tables at a glance

| Table | Rows | Cols | Grain |
|---|--:|--:|---|
| `bridge_candidate_committee` | 7,681 | 6 | one row per candidate<->committee linkage |
| `dim_candidates` | 8,078 | 10 | one row per candidate (PK CAND_ID) |
| `dim_committees` | 19,946 | 8 | one row per committee (PK CMTE_ID) |
| `dim_group_mappings` | 41 | 3 | one row per committee that has been tagged |
| `fact_contributions` | 157,846 | 11 | one row per committee->candidate transaction (PK SUB_ID) |
| `fact_independent_expenditures` | 9,609 | 17 | one row per independent-expenditure line (PK IE_ID = file_num-tran_id) |
| `fact_individual_contributions` | 25,454,311 | 16 | one row per individual->committee itemized contribution (PK SUB_ID) |

## Analytical gotchas

### ⚠️ The two contribution fact tables have DIFFERENT join keys

`fact_contributions` is committee->candidate: join SOURCE_CMTE_ID and
TARGET_CAND_ID. `fact_individual_contributions` is individual->committee:
join TARGET_CMTE_ID (NOT "CMTE_ID"); the donor is free-text CONTRIBUTOR_NAME,
not an id. Mixing the keys silently returns wrong/empty joins.

### ⚠️ fact_contributions secretly contains independent-expenditure money

itpas2 mixes transaction types. TRANSACTION_TP 24K is a real direct
contribution; 24E is an independent expenditure FOR the candidate and 24A is
one AGAINST. The 24E/24A rows OVERLAP fact_independent_expenditures, so
adding the two tables double-counts IE. For true direct money filter
TRANSACTION_TP NOT IN ('24E','24A','24C','24N'); for IE use the dedicated
table only.

### ⚠️ Independent expenditures need an amendment + junk filter

fact_independent_expenditures raw sum is ~$39B because of bogus self-filings
(e.g. "$9B from Warren Buffett"). Always filter AMNDT_IND='N' (original
filings) AND join to dim_committees to exclude garbage; clean total is
~$595M. Same-filing duplicate lines still slip through AMNDT_IND='N' — true
dedup needs the prev_file_num/file_num supersede chain.

### ⚠️ IE ties to a candidate are declarative, support != benefit

An IE never pays the candidate; money goes to a vendor (PAYEE). The link is
the filer's self-declared TARGET_CAND_ID + SUPPORT_OPPOSE. A SUPPORT_OPPOSE='O'
row is tied to the candidate it ATTACKS (it benefits the unnamed opponent).
Always group support ('S') and oppose ('O') separately; never sum AMOUNT
across both as if it were one quantity.

### ⚠️ Conduit earmark double-counting (ActBlue / WinRed)

The largest "committees" by individual dollars are pass-through conduits.
Earmarked giving creates paired rows: the conduit's real receipt (15) and a
memo on the recipient's report (15E, MEMO_CD='X'). The executable SQL layer's
clean individual view reads raw_indiv.parquet and filters MEMO_CD <> 'X' OR
MEMO_CD IS NULL.

### ⚠️ Negative amounts and messy free text

AMOUNT can be negative (refunds/redesignations) — decide whether to include.
CONTRIBUTOR_NAME, EMPLOYER, CMTE_NM are free text with variant spellings; use
ILIKE and expect dupes. There are no canonical donor ids in bulk data.

### ⚠️ One cycle per generated store

Everything in the current generated store is 2026. Other cycles require a fresh generated store (`bin/ingest.py --cycle YYYY` refuses to mix with an existing cycle).

## Tables

### `bridge_candidate_committee`

Many-to-many link between candidates and their committees (from FEC ccl). Use to roll individual money (which lands on committees) up to a candidate.

*Grain:* one row per candidate<->committee linkage  
*Rows:* 7,681

| Column | Type | Description |
|---|---|---|
| `CAND_ID` | VARCHAR | Candidate side of the link (-> dim_candidates.CAND_ID). |
| `CMTE_ID` | VARCHAR | Committee side of the link (-> dim_committees.CMTE_ID). |
| `FEC_ELECTION_YR` | INTEGER | Election year of the linkage. |
| `CMTE_TP` | VARCHAR | Committee type at time of linkage (denormalized from dim_committees). |
| `CMTE_DSGN` | VARCHAR | Committee designation at time of linkage. |
| `LINKAGE_ID` | VARCHAR | FEC linkage record id. |

### `dim_candidates`

Candidate master — everyone who filed to run in the cycle (incumbents, challengers, withdrawn).

*Grain:* one row per candidate (PK CAND_ID)  
*Rows:* 8,078

| Column | Type | Description |
|---|---|---|
| `CAND_ID` | VARCHAR | PK. FEC candidate id; first char encodes office (H/S/P), e.g. H8TX22107, S0KY00091. |
| `CAND_NAME` | VARCHAR | Candidate name, 'LAST, FIRST' free text. |
| `CAND_PTY_AFFILIATION` | VARCHAR | Party code: DEM, REP, IND, LIB, GRE, … (free-ish text). |
| `CAND_ELECTION_YR` | INTEGER | Year of the election this filing targets. |
| `CAND_OFFICE_ST` | VARCHAR | State of the office sought (the constituency). For President = 'US'. |
| `CAND_OFFICE` | VARCHAR | H House · S Senate · P President. |
| `CAND_OFFICE_DISTRICT` | VARCHAR | House district ('01'..); '00' for Senate/at-large. |
| `CAND_ICI` | VARCHAR | Incumbency: I incumbent · C challenger · O open seat. |
| `CAND_STATUS` | VARCHAR | Candidacy status (C statutory candidate, F future, N not yet, P prior). |
| `CAND_PCC` | VARCHAR | CMTE_ID of the principal campaign committee. |

### `dim_committees`

Committee master — PACs, party committees, candidate campaign committees, super PACs.

*Grain:* one row per committee (PK CMTE_ID)  
*Rows:* 19,946

| Column | Type | Description |
|---|---|---|
| `CMTE_ID` | VARCHAR | PK. FEC committee id, e.g. C00401224. |
| `CMTE_NM` | VARCHAR | Committee name (free text). |
| `CMTE_TP` | VARCHAR | Type: H/S/P candidate cmte · Q qualified PAC · N non-qualified PAC · O super PAC (IE-only) · V/W hybrid · X/Y party · I IE filer · U single-candidate IE. |
| `CMTE_DSGN` | VARCHAR | Designation: P principal · A authorized · J joint fundraiser · U unauthorized (most PACs) · B lobbyist/registrant PAC. |
| `CMTE_PTY_AFFILIATION` | VARCHAR | Committee's party affiliation, if any. |
| `ORG_TP` | VARCHAR | Organization type (C corp, L labor, M membership, T trade, V cooperative, W corp w/o stock). |
| `CONNECTED_ORG_NM` | VARCHAR | Sponsoring/connected organization name (free text). |
| `AFFILIATED_CAND_ID` | VARCHAR | Candidate this committee is affiliated with, if any. |

### `dim_group_mappings`

Curated committee -> interest-group classification. Populated by
bin/load_interests.py from model/committee_interests.csv (the editable source
of truth), NOT by bin/ingest.py. Left-join to fact tables to group by interest.

*Grain:* one row per committee that has been tagged  
*Rows:* 41

| Column | Type | Description |
|---|---|---|
| `CMTE_ID` | VARCHAR | Tagged committee (-> dim_committees.CMTE_ID). |
| `custom_category` | VARCHAR | Interest label; stance is encoded in it (e.g. 'Israel-aligned' vs 'Israel-dovish (J Street)' vs 'Israel-critical (anti-AIPAC)') so opposed groups are never summed together. |
| `notes` | VARCHAR | Free-text note: which group/why, or provenance. |

### `fact_contributions`

Money from committees TO candidates (FEC itpas2). NOTE this includes
independent-expenditure rows (24E/24A) — see the IE-overlap gotcha; it is NOT
purely direct contributions.

*Grain:* one row per committee->candidate transaction (PK SUB_ID)  
*Rows:* 157,846

| Column | Type | Description |
|---|---|---|
| `SUB_ID` | BIGINT | PK. Verified-unique FEC submission id. |
| `SOURCE_CMTE_ID` | VARCHAR | Giving/spending committee (-> dim_committees.CMTE_ID). |
| `TARGET_CAND_ID` | VARCHAR | Recipient/target candidate (-> dim_candidates.CAND_ID). |
| `AMOUNT` | DECIMAL(14,2) | Transaction amount. Negative = refund/redesignation. |
| `TRANSACTION_DT` | DATE | Transaction date. |
| `TRANSACTION_TP` | VARCHAR | Type: 24K direct contribution · 24E IE FOR · 24A IE AGAINST · 24C/24N coordinated party exp · 11 etc. |
| `ENTITY_TP` | VARCHAR | Entity type of the source (PAC, PTY, CCM, …). |
| `OTHER_ID` | VARCHAR | Linked entity id (counterparty), filer-dependent. |
| `IMAGE_NUM` | VARCHAR | FEC image (scanned filing) number. |
| `FILE_NUM` | VARCHAR | FEC file number of the filing. |
| `TRAN_ID` | VARCHAR | Filer-assigned transaction id (unique only within a filing). |

### `fact_independent_expenditures`

Independent expenditures — money spent BY a committee FOR/AGAINST a candidate,
not given to them (FEC independent_expenditure file, a real CSV). Uncapped;
this is where super-PAC power (AIPAC/UDP, Fairshake) shows up. Requires the
AMNDT_IND='N' + committee-join filter (junk gotcha).

*Grain:* one row per independent-expenditure line (PK IE_ID = file_num-tran_id)  
*Rows:* 9,609

| Column | Type | Description |
|---|---|---|
| `IE_ID` | VARCHAR | PK. Synthetic = FILE_NUM \|\| '-' \|\| TRAN_ID (no native SUB_ID in source). |
| `SPENDER_CMTE_ID` | VARCHAR | Committee that made the expenditure (-> dim_committees.CMTE_ID). |
| `TARGET_CAND_ID` | VARCHAR | Candidate the spend is about (-> dim_candidates.CAND_ID). ~87% match this cycle's master. |
| `SUPPORT_OPPOSE` | VARCHAR | 'S' supports the target, 'O' opposes the target. Keep separate. |
| `AMOUNT` | DECIMAL(14,2) | Expenditure amount. |
| `EXPENDITURE_DT` | DATE | Date of the expenditure (blank/NULL on ~1,400 source rows — legitimately empty). |
| `AGGREGATE_AMOUNT` | DECIMAL(14,2) | FEC running aggregate for the spender/target/cycle (do not sum across rows). |
| `PURPOSE` | VARCHAR | What the money bought (e.g. 'Media Placement'). |
| `PAYEE` | VARCHAR | Vendor paid (the money goes here, NOT to the candidate). |
| `SPENDER_NAME` | VARCHAR | Spending committee name as filed. |
| `TARGET_CAND_NAME` | VARCHAR | Target candidate name as filed (kept for unmatched ids). |
| `ELECTION_TYPE` | VARCHAR | Election the spend targets (P primary, G general, …). |
| `DISSEMINATION_DT` | DATE | Date the communication was disseminated. |
| `IMAGE_NUM` | VARCHAR | FEC image number. |
| `FILE_NUM` | VARCHAR | FEC file number (part of PK; supersede-chain key). |
| `TRAN_ID` | VARCHAR | Filer transaction id (part of PK). |
| `AMNDT_IND` | VARCHAR | Amendment indicator: N original, A1/A2/… amendments. Filter N to avoid double-counting refilings. |

### `fact_individual_contributions`

Itemized donations from individuals TO committees (FEC itcont), 25M+ rows.
Includes conduit earmark memo rows — see the ActBlue/WinRed gotcha. MEMO_CD
is NOT here; it lives in raw_indiv.parquet.

*Grain:* one row per individual->committee itemized contribution (PK SUB_ID)  
*Rows:* 25,454,311

| Column | Type | Description |
|---|---|---|
| `SUB_ID` | BIGINT | PK. Verified-unique FEC submission id. |
| `TARGET_CMTE_ID` | VARCHAR | Recipient committee (-> dim_committees.CMTE_ID). NOT named CMTE_ID. |
| `CONTRIBUTOR_NAME` | VARCHAR | Donor name, free text (no canonical id). |
| `CITY` | VARCHAR | Donor city. |
| `STATE` | VARCHAR | Donor state (2-letter). Best available 'is this a constituent' proxy. |
| `ZIP_CODE` | VARCHAR | Donor ZIP. |
| `EMPLOYER` | VARCHAR | Donor employer, free text (industry analysis via ILIKE). |
| `OCCUPATION` | VARCHAR | Donor occupation, free text. |
| `AMOUNT` | DECIMAL(14,2) | Contribution amount. Negative = refund. |
| `TRANSACTION_DT` | DATE | Contribution date. |
| `TRANSACTION_TP` | VARCHAR | Type: 15 individual · 15E earmarked memo · 22Y refund · 24T/24R earmarked/redesignated. |
| `ENTITY_TP` | VARCHAR | Entity type (IND individual, etc.). |
| `OTHER_ID` | VARCHAR | Linked entity id (e.g. conduit), if any. |
| `IMAGE_NUM` | VARCHAR | FEC image number. |
| `FILE_NUM` | VARCHAR | FEC file number. |
| `TRAN_ID` | VARCHAR | Filer-assigned transaction id. |

## Raw cold storage (`parquet_store/`)

Lossless originals — every FEC column as text. Useful for columns the curated tables drop (e.g. `MEMO_CD`).

| File | Rows | Cols | Description |
|---|--:|--:|---|
| `raw_ccl.parquet` | 7,681 | 7 | Raw candidate-committee linkages (FEC ccl). Source of bridge_candidate_committee. |
| `raw_cm.parquet` | 19,946 | 15 | Raw committee master (FEC cm). Source of dim_committees. |
| `raw_cn.parquet` | 8,078 | 15 | Raw candidate master (FEC cn). Source of dim_candidates. |
| `raw_ie.parquet` | 9,609 | 23 | Raw independent expenditures (FEC CSV). Source of fact_independent_expenditures. All 23 original columns as text. |
| `raw_indiv.parquet` | 25,454,311 | 21 | Raw itemized individual contributions (FEC itcont). Source of fact_individual_contributions. Has MEMO_CD (not in curated table). |
| `raw_pas2.parquet` | 157,846 | 22 | Raw committee->candidate transactions (FEC itpas2). Source of fact_contributions. |

