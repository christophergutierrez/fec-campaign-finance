WITH cand AS (
    SELECT
        CAND_ID,
        CAND_NAME,
        CAND_OFFICE,
        CAND_OFFICE_ST,
        CAND_OFFICE_DISTRICT,
        CAND_PTY_AFFILIATION
    FROM dim_candidates
    WHERE ($office IS NULL OR CAND_OFFICE = $office)
      AND ($state IS NULL OR CAND_OFFICE_ST = $state)
      AND ($incumbents = false OR CAND_ICI = 'I')
),
agg AS (
    SELECT
        cm.cand_id,
        sum(amount) FILTER (WHERE channel = 'individual') AS indiv_total,
        sum(amount) FILTER (
            WHERE channel = 'individual'
              AND donor_state = cand.CAND_OFFICE_ST
        ) AS in_state,
        sum(amount) FILTER (
            WHERE channel = 'individual'
              AND amount > 0
              AND amount <= 200
        ) AS small,
        sum(amount) FILTER (WHERE channel = 'direct_pac') AS pac_total,
        sum(amount) FILTER (WHERE channel = 'ie_support') AS ie_support
    FROM candidate_money cm
    JOIN cand ON cand.CAND_ID = cm.cand_id
    GROUP BY 1
)
SELECT
    cand.CAND_ID,
    cand.CAND_NAME,
    cand.CAND_OFFICE,
    cand.CAND_OFFICE_ST,
    cand.CAND_OFFICE_DISTRICT,
    cand.CAND_PTY_AFFILIATION,
    coalesce(agg.indiv_total, 0) AS indiv_total,
    coalesce(agg.in_state, 0) AS in_state,
    coalesce(agg.small, 0) AS small,
    coalesce(agg.pac_total, 0) AS pac_total,
    coalesce(agg.ie_support, 0) AS ie_support
FROM agg
JOIN cand ON cand.CAND_ID = agg.cand_id
WHERE coalesce(agg.indiv_total, 0) >= $min_individual;
