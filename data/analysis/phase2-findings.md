# Zava Dataset Exploration Findings

## Purpose

This document records findings from exploratory analysis of the Zava DIY retail dataset for COMPSCI 714 Phase 2.

The exploration focuses on identifying business problems that could benefit from an AI agent, particularly problems requiring multi-step investigation across stores, time periods, and product categories.

---

## Finding 1: Sales declined in 2023

### Observation

Zava's sales generally increased year-on-year from 2020 to 2026, except in 2023.

Total sales decreased from **$6,591,815.46 in 2022** to **$6,354,129.74 in 2023**, a decline of approximately **$237,685.72 (3.61%)**.

Order volume also decreased from **27,565 orders in 2022** to **26,517 orders in 2023**, a decline of approximately **3.80%**.

### Analysis

Annual sales and order counts were calculated by joining `retail.orders` with `retail.order_items` and grouping transactions by year.

See **Query 2** in `zava_exploration.sql`.

### Who would care?

- Executives
- Regional managers
- Store managers

Management would want to understand why growth stopped in 2023 and which parts of the business caused the decline.

### Would an agent help?

**Yes.**

Identifying that sales declined is simple reporting. However, explaining why they declined requires further investigation across stores, months, categories, and potentially individual products. An agent could perform these investigation steps automatically.

---

## Finding 2: The 2023 decline occurred across almost the entire year

### Observation

Sales in 2023 were lower than the corresponding 2022 month in **11 out of 12 months**.

**January was the only month in which sales increased.**

The largest dollar declines occurred in:

- May: **-$44,080.40**
- February: **-$43,409.71**
- December: **-$29,742.66**

This suggests that the 2023 decline was not caused by a single isolated month.

### Analysis

Monthly sales for 2022 and 2023 were calculated and compared using order dates and order-item sales.

See **Query 4** in `zava_exploration.sql`.

### Who would care?

- Executives
- Regional managers
- Finance and planning teams

The result helps distinguish a broad performance problem from a short-term event.

### Would an agent help?

**Yes.**

An agent could automatically detect the annual decline and then determine whether it was concentrated in particular months or spread across the year.

---

## Finding 3: Online, Seattle, and Bellevue were the largest store contributors to the 2023 decline

### Observation

The largest store-level sales declines between 2022 and 2023 were:

- Zava Retail Online: **-$97,041.36 (-5.25%)**
- Zava Retail Seattle: **-$82,554.64 (-4.56%)**
- Zava Retail Bellevue: **-$65,870.36 (-5.39%)**
- Zava Retail Everett: **-$16,654.96 (-7.65%)**

In contrast, some stores grew. In particular:

- Zava Retail Redmond: **+$23,657.05 (+13.80%)**

Online, Seattle, and Bellevue together lost approximately **$245,466**, while growth at stores such as Redmond partially offset these losses.

### Analysis

Sales were aggregated by store for 2022 and 2023 and the absolute and percentage changes were calculated.

See **Query 3** in `zava_exploration.sql`.

### Who would care?

- Regional managers
- Store managers
- Executives

Management could focus investigation and corrective action on the stores contributing most strongly to the decline.

### Would an agent help?

**Yes.**

Rather than requiring a manager to manually compare every store, an agent could identify which stores are contributing most to an overall performance change and investigate them further.

---

## Finding 4: Power Tools was the largest category contributor to the 2023 sales decline

### Observation

Sales declined in **8 of the 9 product categories** in 2023.

The largest dollar decline occurred in **Power Tools**:

- 2022: **$2,402,876.61**
- 2023: **$2,290,805.21**
- Change: **-$112,071.40 (-4.66%)**

Power Tools therefore accounted for approximately **47% of the overall company sales decline**.

**Lumber & Building Materials** had the largest percentage decline at **-9.75%**.

**Hand Tools** was the only category to grow, increasing by **1.49%**.

### Analysis

Sales were joined from order items through products to product categories and aggregated by category for 2022 and 2023.

See **Query 5** in `zava_exploration.sql`.

### Who would care?

- Category/product managers
- Store managers
- Regional managers
- Executives

This finding identifies which part of the product portfolio should receive the most attention.

### Would an agent help?

**Yes.**

After detecting an overall sales decline, an agent could automatically compare categories and rank them according to their contribution to the change.

---

## Finding 5: The Power Tools decline was concentrated in particular stores

### Observation

The decline in Power Tools was not uniform across Zava stores.

The largest Power Tools sales declines were:

- Seattle: **-$58,186.20 (-8.35%)**
- Bellevue: **-$42,127.81 (-9.37%)**
- Online: **-$27,255.68 (-4.09%)**
- Tacoma: **-$25,778.51 (-7.71%)**

However, Power Tools sales increased strongly at several other stores:

- Redmond: **+24.86%**
- Spokane: **+23.08%**
- Everett: **+9.96%**

This indicates that the Power Tools problem was concentrated in particular locations rather than being a uniform company-wide category decline.

### Analysis

Power Tools sales were filtered by category and then compared by store between 2022 and 2023.

See **Query 6** in `zava_exploration.sql`.

### Who would care?

- Store managers
- Regional managers
- Category managers

The differences between stores could help managers investigate why the same category performs well in some locations while declining in others.

### Would an agent help?

**Yes — strongly.**

This requires multiple investigation steps:

1. Detect the company-level decline.
2. Identify contributing categories.
3. Identify contributing stores.
4. Compare poorly performing stores with stores where the same category is growing.
5. Continue investigating products, demand, customers, or inventory to identify possible causes.

This type of iterative investigation is a stronger agent use case than simply displaying a KPI.

---

# Emerging Agent Use Case

## Retail Performance Root Cause Agent

The findings suggest a potential **Retail Performance Root Cause Agent**.

A manager could ask a question such as:

> "Why did Zava's sales decline in 2023?"

The agent could progressively investigate:

Company performance  
→ time period  
→ stores  
→ categories  
→ store/category combinations  
→ products, customers or inventory  
→ explanation and recommended actions.

For example, the current analysis has already established:

**2023 sales declined by approximately $237.7K**  
→ sales declined in 11 of 12 months  
→ Online, Seattle and Bellevue were major negative contributors  
→ Power Tools was the largest category contributor  
→ Power Tools losses were concentrated particularly in Seattle and Bellevue, while Redmond and Spokane experienced strong Power Tools growth.

This demonstrates why an agent may be useful: the business question requires multiple analytical steps and the next investigation depends on the results of the previous step.

---

# Candidate Problems

These are preliminary candidates and should be validated with further dataset exploration.

## Candidate 1: Retail Performance Root Cause Analysis

**Problem:** Managers can see that a KPI such as sales has changed, but determining why requires manually investigating multiple dimensions of the retail data.

**Potential agent:** An agent that detects and explains performance changes by dynamically investigating time periods, stores, categories, products, customers and inventory.

**Evidence:** The 2023 investigation required progressively moving from annual sales to monthly, store, category, and store-category analysis.

---

## Candidate 2: Store and Category Performance Investigation

**Problem:** The same product category can perform very differently between stores, making it difficult to determine where intervention is required.

**Potential agent:** An agent that compares stores, identifies abnormal category performance, finds contrasting high-performing stores, and investigates possible contributing factors.

**Evidence:** Power Tools declined substantially in Seattle and Bellevue while growing strongly in Redmond and Spokane.

---

## Candidate 3: Inventory and Demand Misalignment

**Problem:** Inventory may not be aligned with sales demand across stores and seasons, potentially leaving one store with excess stock while another has stronger demand.

**Potential agent:** An agent that compares inventory levels with sales velocity and identifies potential stock imbalances or transfer opportunities.

**Evidence:** Inventory analysis identified substantial differences between
current stock levels and 2026 sales activity. The stock-to-sales ratio was
calculated as:

Stock-to-sales ratio = Current stock / Units sold in 2026

At store level, Kirkland had a stock-to-sales ratio of 71.00 compared with
26.79 for Seattle and 27.04 for Online.

Product-level analysis identified more extreme cases. For example, Spokane
held 1,427 units of the Trim Router 1 HP while only 1 unit was sold during
2026. Redmond held 1,029 Cordless Angle Grinders while only 1 unit was sold.

The analysis also identified products with inventory but no recorded 2026
sales. Redmond held 808 Compound Miter Saws and 703 Baseboard Moldings with
zero recorded sales, while Everett held 536 Copper Pipes with zero recorded
sales.

These results provide evidence of potential inventory-demand misalignment.
However, because the inventory table contains only current stock levels and
does not contain historical inventory snapshots or replenishment information,
the analysis cannot establish the exact cause of the imbalance.

---

# Next Exploration

The next analysis should investigate:

- Individual products contributing to Power Tools declines.
- Differences between declining and growing Power Tools stores.
- Inventory levels compared with sales demand.
- Customer behaviour where supported by the dataset.
- Row Level Security and how the analysis changes for individual store managers.