CREATE OR REPLACE TEMP VIEW clean_independent_expenditures AS
SELECT
    i.*
FROM fact_independent_expenditures i
JOIN dim_committees cm ON cm.CMTE_ID = i.SPENDER_CMTE_ID
WHERE i.AMNDT_IND = 'N';
