SELECT
    coalesce(sum(amount) FILTER (WHERE channel = 'individual'), 0) AS individual_amount,
    count(*) FILTER (WHERE channel = 'individual') AS individual_count,
    coalesce(sum(amount) FILTER (WHERE channel = 'direct_pac'), 0) AS direct_pac_amount,
    count(*) FILTER (WHERE channel = 'direct_pac') AS direct_pac_count,
    coalesce(sum(amount) FILTER (WHERE channel = 'ie_support'), 0) AS ie_support_amount,
    coalesce(sum(amount) FILTER (WHERE channel = 'ie_oppose'), 0) AS ie_oppose_amount,
    coalesce(sum(amount) FILTER (WHERE channel IN ('individual', 'direct_pac')), 0) AS raised,
    coalesce(sum(amount) FILTER (WHERE channel <> 'ie_oppose'), 0) AS total_backing
FROM candidate_money
WHERE cand_id = $cand_id;
