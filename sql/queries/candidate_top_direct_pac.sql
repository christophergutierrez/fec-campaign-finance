SELECT
    cm.CMTE_NM,
    cm.CMTE_TP,
    sum(f.AMOUNT) AS amount,
    count(*) AS n
FROM fact_contributions f
JOIN dim_committees cm ON cm.CMTE_ID = f.SOURCE_CMTE_ID
WHERE f.TARGET_CAND_ID = $cand_id
  AND f.TRANSACTION_TP NOT IN ('24E', '24A', '24C', '24N')
GROUP BY 1,2
ORDER BY amount DESC
LIMIT $limit;
