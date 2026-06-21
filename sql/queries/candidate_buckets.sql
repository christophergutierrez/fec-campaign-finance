SELECT
    CASE
        WHEN amount < 0 THEN '5 refund/negative'
        WHEN amount <= $small_max THEN '1 small  (<=$200)'
        WHEN amount <= 999 THEN '2 mid    ($201-999)'
        WHEN amount < $near_max THEN '3 large  ($1k-max)'
        ELSE '4 max+   (>=$3500)'
    END AS bucket,
    count(*) AS n,
    sum(amount) AS amount
FROM candidate_money
WHERE cand_id = $cand_id
  AND channel = 'individual'
  AND amount <> 0
GROUP BY 1
ORDER BY 1;
