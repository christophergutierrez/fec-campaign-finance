SELECT
    cand_id,
    interest_category,
    sum(amount) AS amount
FROM candidate_money
WHERE channel IN ('direct_pac', 'ie_support')
  AND interest_category IS NOT NULL
GROUP BY 1,2;
