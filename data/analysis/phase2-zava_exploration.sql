-- =========================================================
-- Query 1: View Zava stores
-- =========================================================
SELECT *
FROM retail.stores;


-- =========================================================
-- Query 2: Total sales performance by year
-- Question: Did sales really fall in 2023, and by how much?
-- =========================================================
SELECT
    EXTRACT(YEAR FROM o.order_date) AS year,
    COUNT(DISTINCT o.order_id) AS total_orders,
    ROUND(SUM(oi.quantity * oi.unit_price), 2) AS total_sales
FROM retail.orders o
JOIN retail.order_items oi
    ON o.order_id = oi.order_id
GROUP BY EXTRACT(YEAR FROM o.order_date)
ORDER BY year;

-- Result:
-- Company sales fell in 2023 compared with 2022.
-- Sales declined by approximately 3.61%, while order volume declined by 3.80%.


-- =========================================================
-- Query 3: Compare store sales between 2022 and 2023
-- Question: Which stores contributed most to the 2023 decline?
-- =========================================================
WITH store_sales AS (
    SELECT
        s.store_name,

        SUM(CASE
            WHEN EXTRACT(YEAR FROM o.order_date) = 2022
            THEN oi.quantity * oi.unit_price
            ELSE 0
        END) AS sales_2022,

        SUM(CASE
            WHEN EXTRACT(YEAR FROM o.order_date) = 2023
            THEN oi.quantity * oi.unit_price
            ELSE 0
        END) AS sales_2023

    FROM retail.stores s
    JOIN retail.orders o
        ON s.store_id = o.store_id
    JOIN retail.order_items oi
        ON o.order_id = oi.order_id

    WHERE EXTRACT(YEAR FROM o.order_date) IN (2022, 2023)

    GROUP BY s.store_id, s.store_name
)

SELECT
    store_name,
    ROUND(sales_2022, 2) AS sales_2022,
    ROUND(sales_2023, 2) AS sales_2023,
    ROUND(sales_2023 - sales_2022, 2) AS sales_change,
    ROUND(
        ((sales_2023 - sales_2022) / sales_2022) * 100,
        2
    ) AS percentage_change
FROM store_sales
ORDER BY sales_change ASC;

-- Result:
-- Online, Seattle and Bellevue were the largest contributors
-- to the 2023 sales decline.


-- =========================================================
-- Query 4: Monthly sales comparison, 2022 vs 2023
-- Question: Was the decline concentrated in particular months?
-- =========================================================
SELECT
    EXTRACT(MONTH FROM o.order_date) AS month,

    ROUND(SUM(CASE
        WHEN EXTRACT(YEAR FROM o.order_date) = 2022
        THEN oi.quantity * oi.unit_price
        ELSE 0
    END), 2) AS sales_2022,

    ROUND(SUM(CASE
        WHEN EXTRACT(YEAR FROM o.order_date) = 2023
        THEN oi.quantity * oi.unit_price
        ELSE 0
    END), 2) AS sales_2023,

    ROUND(
        SUM(CASE
            WHEN EXTRACT(YEAR FROM o.order_date) = 2023
            THEN oi.quantity * oi.unit_price
            ELSE 0
        END)
        -
        SUM(CASE
            WHEN EXTRACT(YEAR FROM o.order_date) = 2022
            THEN oi.quantity * oi.unit_price
            ELSE 0
        END),
        2
    ) AS sales_change

FROM retail.orders o
JOIN retail.order_items oi
    ON o.order_id = oi.order_id

WHERE EXTRACT(YEAR FROM o.order_date) IN (2022, 2023)

GROUP BY EXTRACT(MONTH FROM o.order_date)
ORDER BY month;

-- Result:
-- Sales fell in 2023 for every month except January.


-- =========================================================
-- Supporting Query: Inspect product/category table structure
-- Purpose: Confirm joins before category analysis.
-- =========================================================
SELECT
    table_name,
    column_name
FROM information_schema.columns
WHERE table_schema = 'retail'
  AND table_name IN (
      'order_items',
      'products',
      'product_types',
      'categories'
  )
ORDER BY table_name, ordinal_position;


-- =========================================================
-- Query 5: Compare category sales between 2022 and 2023
-- Question: Which categories drove the 2023 decline?
-- =========================================================
WITH category_sales AS (
    SELECT
        c.category_name,

        SUM(CASE
            WHEN EXTRACT(YEAR FROM o.order_date) = 2022
            THEN oi.quantity * oi.unit_price
            ELSE 0
        END) AS sales_2022,

        SUM(CASE
            WHEN EXTRACT(YEAR FROM o.order_date) = 2023
            THEN oi.quantity * oi.unit_price
            ELSE 0
        END) AS sales_2023

    FROM retail.orders o
    JOIN retail.order_items oi
        ON o.order_id = oi.order_id
    JOIN retail.products p
        ON oi.product_id = p.product_id
    JOIN retail.categories c
        ON p.category_id = c.category_id

    WHERE EXTRACT(YEAR FROM o.order_date) IN (2022, 2023)

    GROUP BY c.category_id, c.category_name
)

SELECT
    category_name,
    ROUND(sales_2022, 2) AS sales_2022,
    ROUND(sales_2023, 2) AS sales_2023,
    ROUND(sales_2023 - sales_2022, 2) AS sales_change,
    ROUND(
        ((sales_2023 - sales_2022) / sales_2022) * 100,
        2
    ) AS percentage_change
FROM category_sales
ORDER BY sales_change ASC;

-- Result:
-- Sales declined in 8 of 9 categories in 2023.
-- Power Tools had the largest dollar decline (-$112,071, -4.66%).
-- Lumber & Building Materials had the largest percentage decline (-9.75%).
-- Hand Tools was the only category that increased (+1.49%).


-- =========================================================
-- Query 6: Power Tools sales by store, 2022 vs 2023
-- Question: Was the Power Tools decline concentrated in particular stores?
-- =========================================================
WITH store_category_sales AS (
    SELECT
        s.store_name,

        SUM(CASE
            WHEN EXTRACT(YEAR FROM o.order_date) = 2022
            THEN oi.quantity * oi.unit_price
            ELSE 0
        END) AS sales_2022,

        SUM(CASE
            WHEN EXTRACT(YEAR FROM o.order_date) = 2023
            THEN oi.quantity * oi.unit_price
            ELSE 0
        END) AS sales_2023

    FROM retail.orders o
    JOIN retail.order_items oi
        ON o.order_id = oi.order_id
    JOIN retail.stores s
        ON o.store_id = s.store_id
    JOIN retail.products p
        ON oi.product_id = p.product_id
    JOIN retail.categories c
        ON p.category_id = c.category_id

    WHERE EXTRACT(YEAR FROM o.order_date) IN (2022, 2023)
      AND c.category_name = 'POWER TOOLS'

    GROUP BY s.store_id, s.store_name
)

SELECT
    store_name,
    ROUND(sales_2022, 2) AS sales_2022,
    ROUND(sales_2023, 2) AS sales_2023,
    ROUND(sales_2023 - sales_2022, 2) AS sales_change,
    ROUND(
        ((sales_2023 - sales_2022) / sales_2022) * 100,
        2
    ) AS percentage_change
FROM store_category_sales
ORDER BY sales_change ASC;

-- Result:
-- The Power Tools decline was concentrated mainly in Seattle,
-- Bellevue, Online and Tacoma.
-- Seattle had the largest dollar decline (-$58,186).
-- Power Tools grew strongly in Redmond (+24.86%),
-- Spokane (+23.08%) and Everett (+9.96%).

-- =========================================================
-- Query 7: Inspect inventory table structure
-- Purpose: Prepare for inventory vs demand analysis.
-- =========================================================
SELECT
    column_name,
    data_type
FROM information_schema.columns
WHERE table_schema = 'retail'
  AND table_name = 'inventory'
ORDER BY ordinal_position;

-- =========================================================
-- Query 8: Inventory vs sales demand by store
-- Question: Are current inventory levels aligned with recent sales demand?
-- =========================================================
WITH store_sales AS (
    SELECT
        oi.store_id,
        SUM(oi.quantity) AS units_sold_2026
    FROM retail.orders o
    JOIN retail.order_items oi
        ON o.order_id = oi.order_id
    WHERE EXTRACT(YEAR FROM o.order_date) = 2026
    GROUP BY oi.store_id
),

store_inventory AS (
    SELECT
        store_id,
        SUM(stock_level) AS current_stock
    FROM retail.inventory
    GROUP BY store_id
)

SELECT
    s.store_name,
    ss.units_sold_2026,
    si.current_stock,

    ROUND(
        si.current_stock::numeric /
        NULLIF(ss.units_sold_2026, 0),
        2
    ) AS stock_to_sales_ratio

FROM retail.stores s
JOIN store_sales ss
    ON s.store_id = ss.store_id
JOIN store_inventory si
    ON s.store_id = si.store_id

ORDER BY stock_to_sales_ratio DESC;

-- Result:
-- Inventory relative to 2026 sales varies substantially between stores.
-- Kirkland has the highest stock-to-sales ratio (71.00), followed by
-- Redmond (60.21), while Seattle (26.79) and Online (27.04) have the lowest.
-- This suggests a possible inventory-demand imbalance, but store-level
-- totals alone are not sufficient to confirm it.

-- =========================================================
-- Query 9: Product-level inventory vs sales demand
-- Question: Which store/product combinations have high stock
-- relative to their 2026 sales?
-- =========================================================
WITH product_sales AS (
    SELECT
        oi.store_id,
        oi.product_id,
        SUM(oi.quantity) AS units_sold_2026
    FROM retail.orders o
    JOIN retail.order_items oi
        ON o.order_id = oi.order_id
    WHERE EXTRACT(YEAR FROM o.order_date) = 2026
    GROUP BY oi.store_id, oi.product_id
)

SELECT
    s.store_name,
    p.product_name,
    c.category_name,
    i.stock_level,
    COALESCE(ps.units_sold_2026, 0) AS units_sold_2026,

    ROUND(
        i.stock_level::numeric /
        NULLIF(ps.units_sold_2026, 0),
        2
    ) AS stock_to_sales_ratio

FROM retail.inventory i
JOIN retail.stores s
    ON i.store_id = s.store_id
JOIN retail.products p
    ON i.product_id = p.product_id
JOIN retail.categories c
    ON p.category_id = c.category_id
LEFT JOIN product_sales ps
    ON i.store_id = ps.store_id
    AND i.product_id = ps.product_id

WHERE COALESCE(ps.units_sold_2026, 0) > 0

ORDER BY stock_to_sales_ratio DESC
LIMIT 20;
-- Result:
-- Several store/product combinations have very high current inventory
-- relative to their 2026 sales.
-- For example, Spokane held 1,427 Trim Router 1 HP units while only
-- 1 unit was sold, and Redmond held 1,029 Cordless Angle Grinders
-- while only 1 unit was sold.
-- This indicates potential inventory-demand misalignment at product level.


-- =========================================================
-- Query 10: Products with inventory but zero 2026 sales
-- Question: Are stores holding stock for products with no
-- recorded sales during 2026?
-- =========================================================
WITH product_sales AS (
    SELECT
        oi.store_id,
        oi.product_id,
        SUM(oi.quantity) AS units_sold_2026
    FROM retail.orders o
    JOIN retail.order_items oi
        ON o.order_id = oi.order_id
    WHERE EXTRACT(YEAR FROM o.order_date) = 2026
    GROUP BY oi.store_id, oi.product_id
)

SELECT
    s.store_name,
    p.product_name,
    c.category_name,
    i.stock_level,
    COALESCE(ps.units_sold_2026, 0) AS units_sold_2026

FROM retail.inventory i
JOIN retail.stores s
    ON i.store_id = s.store_id
JOIN retail.products p
    ON i.product_id = p.product_id
JOIN retail.categories c
    ON p.category_id = c.category_id
LEFT JOIN product_sales ps
    ON i.store_id = ps.store_id
    AND i.product_id = ps.product_id

WHERE COALESCE(ps.units_sold_2026, 0) = 0
  AND i.stock_level > 0

ORDER BY i.stock_level DESC
LIMIT 20;

-- Result:
-- Several store/product combinations have substantial current inventory
-- despite recording zero sales during 2026.
-- Examples include:
-- Redmond: Compound Miter Saw 10-inch (808 units, 0 sold)
-- Redmond: Baseboard Molding 8ft (703 units, 0 sold)
-- Everett: Copper Pipe 1-inch Type L (536 units, 0 sold)
-- Kirkland: Wall Mount Cabinet (505 units, 0 sold)
--
-- This provides evidence of potential inventory-demand misalignment,
-- although the dataset does not contain enough inventory history to
-- determine the exact cause.