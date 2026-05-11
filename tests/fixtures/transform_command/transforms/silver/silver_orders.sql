-- depends_on: bronze.orders_src_orders
SELECT
    order_id,
    customer_id,
    order_date,
    amount
FROM bronze.orders_src_orders
