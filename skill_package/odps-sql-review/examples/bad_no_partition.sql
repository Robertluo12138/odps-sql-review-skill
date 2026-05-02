-- examples/bad_no_partition.sql
-- Anti-pattern: source table has no partition filter.  ODPS will scan
-- every partition.  This is one of the most common reasons a job gets
-- killed by the queue.

SELECT
    user_id,
    SUM(order_amt) AS total_order_amt,
    COUNT(1)       AS order_cnt
FROM   dwd_xxx_order_di
GROUP BY user_id;
