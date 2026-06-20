SELECT
    coalesce(sum(amount), 0) AS amount,
    count(*) AS n,
    coalesce(sum(CASE WHEN donor_state <> $home_state THEN amount ELSE 0 END), 0) AS out_of_state_amount
FROM candidate_money
WHERE cand_id = $cand_id
  AND channel = 'individual'
  AND (/*{pattern_clause}*/);
