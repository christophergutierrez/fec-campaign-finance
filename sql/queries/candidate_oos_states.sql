SELECT
    donor_state,
    sum(amount) AS amount
FROM candidate_money
WHERE cand_id = $cand_id
  AND channel = 'individual'
  AND donor_state <> $home_state
  AND amount > 0
GROUP BY 1
ORDER BY amount DESC;
