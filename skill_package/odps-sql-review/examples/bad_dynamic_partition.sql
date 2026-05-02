-- examples/bad_dynamic_partition.sql
-- Anti-pattern: INSERT OVERWRITE with dynamic partition where the
-- number of partitions is unbounded.  May write to wrong dt or
-- generate huge numbers of small files.

INSERT OVERWRITE TABLE ads_xxx_user_action_dpt PARTITION (dt)
SELECT
    user_id,
    action_type,
    COUNT(1) AS action_cnt,
    sale_dt  AS dt           -- 动态分区由源数据决定，分区数不可控
FROM   dwd_xxx_user_action_di
WHERE  sale_dt BETWEEN '20240101' AND '20241231'
GROUP BY user_id, action_type, sale_dt;
