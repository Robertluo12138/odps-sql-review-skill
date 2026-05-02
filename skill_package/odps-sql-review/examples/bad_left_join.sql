-- examples/bad_left_join.sql
-- Anti-pattern: LEFT JOIN invalidated by WHERE clause filtering on the
-- right table.  Below the LEFT JOIN behaves as INNER JOIN because
-- `b.status = 1` filters out unmatched-left rows.

INSERT OVERWRITE TABLE ads_xxx_user_order_di PARTITION (dt = '${bizdate}')
SELECT
    a.user_id,
    a.order_id,
    a.order_amt,
    b.shop_name,
    b.status                                 AS shop_status
FROM   dwd_xxx_order_di a
LEFT JOIN dim_xxx_shop_df b
       ON a.shop_id = b.shop_id
WHERE  a.dt = '${bizdate}'
  AND  b.status = 1                           -- <-- 该过滤把 LEFT JOIN 退化成 INNER JOIN
;
