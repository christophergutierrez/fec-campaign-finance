CREATE OR REPLACE TEMP VIEW candidate_committees AS
SELECT DISTINCT
    b.CAND_ID,
    b.CMTE_ID,
    b.CMTE_TP,
    b.CMTE_DSGN
FROM bridge_candidate_committee b
WHERE b.CMTE_DSGN IN ('P', 'A')
  AND b.CMTE_TP IN ('H', 'S', 'P');
