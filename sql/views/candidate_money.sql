CREATE OR REPLACE TEMP VIEW candidate_money AS
SELECT
    cc.CAND_ID AS cand_id,
    'individual' AS channel,
    i.AMOUNT AS amount,
    i.STATE AS donor_state,
    i.CONTRIBUTOR_NAME AS contributor_name,
    i.EMPLOYER AS employer,
    CAST(NULL AS VARCHAR) AS interest_category
FROM clean_individual_contributions i
JOIN candidate_committees cc ON cc.CMTE_ID = i.TARGET_CMTE_ID

UNION ALL
SELECT
    f.TARGET_CAND_ID AS cand_id,
    'direct_pac' AS channel,
    f.AMOUNT AS amount,
    CAST(NULL AS VARCHAR) AS donor_state,
    CAST(NULL AS VARCHAR) AS contributor_name,
    CAST(NULL AS VARCHAR) AS employer,
    g.custom_category AS interest_category
FROM fact_contributions f
LEFT JOIN dim_group_mappings g ON g.CMTE_ID = f.SOURCE_CMTE_ID
WHERE f.TRANSACTION_TP NOT IN ('24E', '24A', '24C', '24N')

UNION ALL
SELECT
    i.TARGET_CAND_ID AS cand_id,
    'ie_support' AS channel,
    i.AMOUNT AS amount,
    CAST(NULL AS VARCHAR) AS donor_state,
    CAST(NULL AS VARCHAR) AS contributor_name,
    CAST(NULL AS VARCHAR) AS employer,
    g.custom_category AS interest_category
FROM clean_independent_expenditures i
LEFT JOIN dim_group_mappings g ON g.CMTE_ID = i.SPENDER_CMTE_ID
WHERE i.SUPPORT_OPPOSE = 'S'

UNION ALL
SELECT
    i.TARGET_CAND_ID AS cand_id,
    'ie_oppose' AS channel,
    i.AMOUNT AS amount,
    CAST(NULL AS VARCHAR) AS donor_state,
    CAST(NULL AS VARCHAR) AS contributor_name,
    CAST(NULL AS VARCHAR) AS employer,
    g.custom_category AS interest_category
FROM clean_independent_expenditures i
LEFT JOIN dim_group_mappings g ON g.CMTE_ID = i.SPENDER_CMTE_ID
WHERE i.SUPPORT_OPPOSE = 'O';
