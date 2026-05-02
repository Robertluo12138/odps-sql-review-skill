-- examples/bad_count_distinct.sql
-- Anti-pattern: long-period COUNT(DISTINCT user_id) directly on a raw
-- behaviour detail table.  Will scan 365 partitions of the largest fact
-- table in the warehouse and run a single-reducer global distinct.

SELECT
    COUNT(DISTINCT user_id) AS yau_user_cnt,
    COUNT(DISTINCT CASE WHEN action = 'pay' THEN user_id END) AS yau_pay_user_cnt
FROM   dwd_xxx_user_action_di
WHERE  dt BETWEEN '20240101' AND '20241231';
