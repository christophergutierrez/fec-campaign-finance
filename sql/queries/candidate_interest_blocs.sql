SELECT
    interest_category,
    coalesce(sum(amount) FILTER (WHERE channel = 'direct_pac'), 0) AS direct_pac_amount,
    coalesce(sum(amount) FILTER (WHERE channel = 'ie_support'), 0) AS ie_support_amount
FROM candidate_money
WHERE cand_id = $cand_id
  AND channel IN ('direct_pac', 'ie_support')
  AND interest_category IS NOT NULL
GROUP BY 1
HAVING coalesce(sum(amount), 0) <> 0
ORDER BY sum(amount) DESC;
