SELECT
    cand_id,
    coalesce(sum(amount), 0) AS amount
FROM candidate_money
WHERE channel = 'individual'
  AND (/*{pattern_clause}*/)
GROUP BY 1;
