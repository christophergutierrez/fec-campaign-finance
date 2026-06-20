SELECT
    SPENDER_NAME,
    SUPPORT_OPPOSE,
    sum(AMOUNT) AS amount
FROM clean_independent_expenditures
WHERE TARGET_CAND_ID = $cand_id
GROUP BY 1,2
ORDER BY amount DESC
LIMIT $limit;
