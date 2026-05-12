-- depends_on: silver.silver_orders
SELECT
    customer_id,
    COUNT(*) AS order_count,
    SUM(amount) AS total_amount
FROM silver.silver_orders
GROUP BY customer_id
