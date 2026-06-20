SELECT
    donor_state,
    sum(amount) AS amount,
    count(*) AS n
FROM candidate_money
WHERE cand_id = $cand_id
  AND channel = 'individual'
GROUP BY 1
ORDER BY amount DESC, donor_state
LIMIT $limit;
