-- examples/bad_join_explosion.sql
-- Anti-pattern: two fact tables joined on a non-unique key without
-- right-side deduplication.  Order rows can be multiplied by the
-- number of payments per order, inflating SUM(amt) below.

SELECT
    o.user_id,
    SUM(o.order_amt) AS total_order_amt,
    SUM(p.pay_amt)   AS total_pay_amt
FROM   dwd_xxx_order_di o
JOIN   dwd_xxx_payment_di p
       ON o.order_id = p.order_id
WHERE  o.dt = '${bizdate}'
  AND  p.dt = '${bizdate}'
GROUP BY o.user_id;
