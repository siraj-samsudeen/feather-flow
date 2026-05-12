-- depends_on: silver.silver_orders
-- materialized: true
SELECT
    order_id,
    customer_id,
    order_date,
    amount
FROM silver.silver_orders
