# Phase 3 – Use Case Decision

## Candidate Use Cases

The three candidate problems identified during Phase 2 are:

1. Retail Performance Root Cause Analysis
2. Store and Category Performance Investigation
3. Inventory and Demand Misalignment

---

## Five-Test Comparison

| Test | Candidate 1: Root Cause Analysis | Candidate 2: Store & Category Investigation | Candidate 3: Inventory & Demand Misalignment |
|---|---|---|---|
| 1. Can the data answer it? | **Pass** – Phase 2 demonstrated analysis across years, months, stores and categories. | **Pass** – The data supports comparison of category performance across stores. | **Pass** – Inventory and sales data allow stock levels to be compared with sales activity. |
| 2. User, decision and consequence? | **Pass** – Managers need to decide where to investigate and intervene when performance changes. Poor decisions could focus attention and resources on the wrong stores or products. | **Pass** – Store/regional managers need to identify underperforming stores and categories and decide where corrective action is required. | **Pass** – Store/operations managers need to identify possible excess or insufficient inventory and decide where inventory attention is required. |
| 3. Does it genuinely need an agent? | **Strong Pass** – The investigation is iterative. The system must decide what to investigate next based on previous findings, such as moving from company performance to stores, categories and products. | **Partial Pass** – An agent could investigate differences between stores, but much of the core comparison could also be produced by predefined SQL queries or a dashboard. | **Pass** – An agent could identify inventory anomalies and decide which products or stores require deeper investigation, although some anomaly detection could be performed using predefined rules. |
| 4. At least two tools + real reasoning? | **Strong Pass** – The agent can use multiple analytical tools, such as overall performance, store comparison, category analysis and product/inventory investigation, and choose between them dynamically. | **Partial/Pass** – Multiple tools could be used, but the workflow risks becoming a fixed sequence of comparisons rather than genuine reasoning. | **Pass** – Separate sales and inventory tools could be combined, with reasoning used to identify and investigate potential mismatches. |
| 5. Small enough but interesting enough? | **Pass** – The scope can be limited to investigating retail sales-performance changes while still demonstrating multi-step agent reasoning. | **Pass** – Technically manageable, but narrower and potentially less agentic than Candidate 1. | **Pass** – Interesting and manageable if constrained, although the inventory dataset has limitations. |

---

## Preliminary Decision

**Candidate 1: Retail Performance Root Cause Analysis** is the strongest candidate.

It passes all five tests and most clearly passes the critical agent test. A fixed query cannot determine the complete investigation path in advance because each result can determine what should be examined next.

For example, Phase 2 demonstrated the following investigation:

2023 sales decline  
→ investigate monthly performance  
→ investigate store performance  
→ investigate category performance  
→ identify Power Tools as the largest category contributor  
→ investigate Power Tools across stores  
→ identify different behaviour between declining and growing stores.

Candidate 2 is useful but risks becoming a dashboard or predefined comparison workflow. Candidate 3 also has potential for agentic investigation, but the inventory table contains only current stock levels and does not provide historical inventory snapshots or replenishment information, limiting the conclusions that can safely be drawn.

Therefore, Candidate 1 provides the strongest combination of data support, business relevance, multi-step reasoning and achievable project scope.


---

# Chosen Use Case Definition

## 1. Problem Statement

Retail managers can identify when overall sales performance has changed, but understanding why the change occurred requires investigation across multiple dimensions such as time periods, stores, product categories and individual products. Manually performing this investigation requires running and interpreting multiple analyses, and the next question often depends on what the previous analysis reveals. The proposed use case is an AI agent that investigates retail performance changes, identifies the main contributing factors, and provides an evidence-based explanation to support management decisions.

## 2. User

The primary users are **regional managers and retail performance managers** responsible for monitoring sales performance across multiple Zava stores. They are accountable for identifying underperformance, determining where management attention is required, and deciding which stores or product areas require further action.

Store managers and executives may also use the findings, but the primary user is a manager responsible for performance across multiple stores.

## 3. Decision

The user needs to decide **where to focus management attention when retail performance changes and what areas require further investigation or corrective action**.

For example, when company sales decline, the manager needs to determine whether the change is associated with particular time periods, stores, product categories or products.

Instead of following a fixed investigation path, the agent determines which analysis to perform next based on the evidence returned by previous analysis steps.

## 4. Consequence

A poor investigation can lead management to focus on the wrong part of the business. For example, treating a company-wide sales decline as a general problem could result in broad interventions even when the decline is concentrated in particular stores or product categories.

This can waste management time, direct attention toward stores that are performing normally, and delay investigation of the areas contributing most strongly to the performance change. A more targeted investigation allows managers to prioritise the areas where action is most likely to be required.


## 5. Evidence

Phase 2 analysis confirmed that the Zava dataset contains a real performance problem that can be investigated across multiple dimensions.

Sales declined from $6,591,815.46 in 2022 to $6,354,129.74 in 2023, a decrease of approximately 3.61%. The decline occurred in 11 of the 12 months, showing that it was not caused by a single unusual month.

Further investigation showed that the decline was not distributed evenly across the business. Online, Seattle and Bellevue together accounted for approximately $245,466 of decline, while Redmond increased sales by approximately 13.80%.

At category level, eight of the nine categories declined. Power Tools had the largest absolute decline, falling by $112,071.40 and accounting for approximately 47% of the overall sales decline. However, Power Tools performance also differed substantially between stores. Seattle and Bellevue declined while Redmond and Spokane grew strongly.

These findings were produced through Queries 2–6 in the Phase 2 exploration and demonstrate that explaining a performance change requires investigation across several related dimensions rather than examining only the overall sales figure.

## 6. Why an Agent

This use case requires more than translating a natural-language question into a single SQL query. The investigation path depends on what is discovered at each step.

For example, after detecting an overall sales decline, the agent may first examine whether the change is concentrated in particular months. It may then compare stores, identify the stores contributing most strongly to the decline, investigate categories within those stores, and continue to product-level analysis when necessary.

The important reasoning step is deciding what to investigate next based on the results already obtained. Different performance problems may therefore produce different investigation paths.

The agent can use multiple analytical tools for time-period, store, category and product-level analysis and select between them dynamically. It can also determine when enough evidence has been collected to provide an explanation rather than continuing the investigation unnecessarily.

Under the removal test, removing the agent would require replacing this adaptive investigation with a predefined sequence of queries or requiring the manager to manually interpret each result and decide which analysis to run next. The agent therefore provides the reasoning and orchestration required to conduct the investigation rather than simply acting as a language interface over a report.


## 7. Success Criteria

The agent will be considered successful when it can investigate a retail performance change and produce an evidence-based explanation that is consistent with the underlying Zava data.

For the known 2023 performance decline, a successful investigation should:

- correctly identify that sales declined from 2022 to 2023;
- identify the stores and categories that contributed most strongly to the decline;
- recognise that performance differs between stores rather than assuming that all stores followed the same pattern;
- use at least two analytical tools during the investigation;
- use the result of one analysis to determine an appropriate next investigation step;
- ensure that quantitative claims are supported by tool results; and
- clearly state when the available data is insufficient to establish a causal explanation.

A confidently wrong answer would include incorrect sales figures, identify a growing store or category as a major contributor to the decline, invent a cause that is not supported by the data, or use unrelated data as evidence for a historical performance change.

These errors can be detected by comparing the agent's claims with known results from the Phase 2 SQL queries and by reviewing the agent's tool calls to confirm that its conclusions are grounded in retrieved data.

## 8. Scope Boundaries

The project will focus on investigating historical retail sales-performance changes using the Zava dataset.

The project will include analysis across time periods, stores, categories and products where supported by the available data. The agent will identify evidence-backed contributing factors and areas requiring management attention.

The project will deliberately not include:

- sales forecasting or prediction of future performance;
- automatic pricing, ordering or inventory changes;
- autonomous execution of business decisions;
- claims of causal relationships that cannot be established from the available data;
- a full business intelligence dashboard; or
- unnecessary multi-agent functionality when a single reasoning agent with multiple tools can perform the investigation.

Current inventory data may be used as supporting information where appropriate, but it will not be used to claim the cause of historical sales changes because the dataset does not contain historical inventory snapshots or replenishment history.