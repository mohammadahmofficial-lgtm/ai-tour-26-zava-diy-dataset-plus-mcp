# Phase 4 — Agent Architecture

## 1. Architecture Goal

The proposed solution is a **Retail Performance Root-Cause Agent** for regional and retail performance managers. Its purpose is to investigate questions such as why sales performance changed across years, stores, categories, products, or customer segments, and to return an evidence-grounded explanation.

The architecture deliberately keeps the agent responsible for deciding **what to investigate next**. It does not encode a fixed root-cause workflow. Intermediate evidence can change the next query or tool call, and the agent must avoid presenting correlation or incomplete evidence as an established business cause.

The selected architecture combines four agent patterns:

- **Planning** — determine the next useful investigation based on the question and evidence collected so far.
- **Tool Use** — access authoritative Zava data through MCP tools rather than relying on model knowledge.
- **Agentic RAG** — use semantic retrieval when the investigation requires concept-based product discovery rather than exact SQL categories or keywords.
- **Metacognition** — evaluate whether the evidence is sufficient, internally consistent, and capable of supporting the proposed explanation before answering.

### 1.1 Design Evolution: Single-Agent Proposal → Multi-Agent Implementation

The Phase 4 design initially proposed a **single Root-Cause Agent** that would plan, select tools, inspect evidence, and revise its investigation while maintaining one coherent analytical context. The rationale was that the use case did not contain genuinely independent specialist roles that required separate agents, and that introducing multiple agents would add coordination, context-sharing, result-merging, latency, and additional failure modes without a clear benefit for the proof of concept.

During Phase 5 implementation, the design evolved into a **multi-agent hub-and-spoke architecture** to support the full report-generation workflow (outline planning, parallel data retrieval, analytics, and report writing) while preserving the root-cause investigation loop as the core reasoning pattern.

### 1.2 Implemented Multi-Agent Hub-and-Spoke System

The implemented system consists of six agent types coordinated by a central Orchestrator:

- **Security Agent** — screens incoming prompts for malicious inputs or access violations, and runs a final security audit before reports are saved.
- **Prompt Quality Agent** — evaluates whether the user's question is clear and specific enough to produce a meaningful analysis.
- **Orchestrator Agent** — interprets the question, maps out the overall plan, determines which datasets are needed, and coordinates the rest of the agent team.
- **Data Retrieve Agents** — use ReAct-style reasoning alongside MCP tools to inspect database schemas, write SQL queries, pull data, and revise queries if something breaks. Multiple retrieval tasks run concurrently (swarm execution), with recursive task subdivision for complex tasks.
- **Analytics Agent** — takes raw database results and shapes them into summaries, comparison tables, and visual chart elements.
- **Write Report Agent** — compiles the finished report, sends it through a final security pass, and saves the output and metadata.

The single-agent root-cause investigation loop from the Phase 4 proposal remains the **core reasoning pattern** executed by the Data Retrieve Agents under Orchestrator coordination. The multi-agent structure adds parallel retrieval, report assembly, and governance around that loop without changing its adaptive, evidence-driven nature.

## 2. Main Agent Reasoning Loop

The central design is an iterative investigation loop rather than a predetermined sequence. In the implemented system, this loop is executed by the **Data Retrieve Agents** under Orchestrator coordination, and is wrapped by the full report-generation pipeline.

### 2.1 The Data Retrieve ReAct Loop (implemented reasoning pattern)

The implemented root-cause investigation is a **ReAct loop** (`run_mcp_loop` in `data_retrieve_agent.py`). Every LLM turn must return strict JSON with a `thought` key **before** the `action` key. The `thought` is the SQL pre-flight check (schema/column verification, join safety, aggregation safety) — there is no separate validation agent.

```text
                    ┌──────────────────────────────┐
                    │   DATA RETRIEVE AGENT        │
                    │   (one per swarm task)       │
                    └──────────────┬───────────────┘
                                   │
                    LLM turn: {"thought": "...", "action": "..."}
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │        ACTION DISPATCH       │
                    └──────┬───────┬───────┬───────┘
                           │       │       │
              ┌────────────┘       │       └────────────┐
              ▼                    ▼                    ▼
     ┌────────────────┐  ┌────────────────┐  ┌────────────────────┐
     │  get_schemas   │  │ execute_query  │  │  semantic_search   │
     │  (MCP tool)    │  │  (MCP tool)    │  │  (MCP tool, RAG)   │
     └───────┬────────┘  └───────┬────────┘  └─────────┬──────────┘
             │                   │                     │
             │                   ▼                     │
             │        ┌──────────────────┐             │
             │        │  ZAVA / DATA     │             │
             │        │  (PostgreSQL,    │             │
             │        │   RLS-scoped)    │             │
             │        └───────┬──────────┘             │
             │                │                        │
             │                ▼                        │
             │        ┌──────────────────┐             │
             │        │  OBSERVE result  │◄────────────┘
             │        │  (append to      │
             │        │   message history)│
             │        └───────┬──────────┘
             │                │
             └────────────────┘
                    │
                    ▼
        ┌───────────────────────────┐
        │  finish?  (data complete) │
        └─────────┬─────────────────┘
                  │
        ┌─────────┴─────────┐
        │                   │
       YES                 NO
        │                   │
        ▼                   ▼
  ┌──────────────┐   ┌──────────────────────────┐
  │ return data  │   │ request_subdivision?     │
  │ (success)    │   │ (only after ≥5 queries)  │
  └──────────────┘   └───────────┬──────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                   YES                       NO
                    │                         │
                    ▼                         ▼
        ┌────────────────────┐      ┌─────────────────────┐
        │ Orchestrator splits │      │  next LLM turn      │
        │ into 2–3 sub-tasks  │      │  (loop continues,   │
        │ (recursive, depth≤2)│      │   max 10 iterations)│
        └────────────────────┘      └─────────────────────┘
```

Key behaviours of the implemented loop:

- **`thought`-before-`action`** — every turn must emit a `thought` (SQL pre-flight check) before the `action`. Malformed JSON is fed back as an error message and the loop continues.
- **Valid actions** — `get_schemas`, `execute_query`, `semantic_search`, `finish`, `request_subdivision`. Unknown actions are rejected with the list of valid actions.
- **Semantic-search enforcement** — after a `semantic_search`, every subsequent `execute_query` must filter on all returned `product_id` values via a `product_id IN (...)` predicate; otherwise the query is rejected.
- **Result-size guard** — results over 20,000 characters are truncated with an instruction to aggregate (SUM/COUNT/GROUP BY), never to dump raw rows.
- **Permission-denied detection** — explicit PostgreSQL permission failures stop the retrieval task with a clear message; empty or single-store results are valid and never treated as permission errors.
- **Subdivision** — `request_subdivision` is only accepted from iteration 5 onward; the Orchestrator then splits the task into 2–3 sub-tasks (recursive, max depth 2).
- **Iteration cap** — if the loop exhausts `SQL_VALIDATION_MAX_ITERATION` (10) turns, it returns `{"status": "error"}`.

The **metacognition** responsibilities from the conceptual design are distributed across the implementation: the `thought` field handles SQL pre-flight validation, the system prompt enforces causal restraint and aggregation safety, and the Orchestrator's planner (`plan_data_tasks`) decides when evidence is sufficient (`investigation_complete`) or whether another investigation round is needed.

### 2.2 Implemented End-to-End Pipeline

The implemented system wraps the investigation loop in a full report-generation pipeline:

```text
User question (Flask dashboard)
        │
        ▼
FastAPI backend (/generate)
        │
        ▼
Phase A — Gate:
  1. Security Agent — scan_initial_prompt (rejects injection / privilege escalation)
  2. Prompt Quality Agent — assess_clarity (returns a clarification question if vague)
        │  accepted
        ▼
Phase B — Generation (run_generation_pipeline):
  0. fetch_metadata — one MCP session: all table schemas + RLS-filtered store list
  1. generate_report_outline — LLM → up to MAX_REPORT_SECTIONS sections
  2. generate_data_requirements — LLM → JSON of metrics, periods, comparisons
  3. plan_data_tasks — splits requirements into ≤4 independent tasks
  4. _run_swarm — asyncio.gather of Data Retrieve Agents (recursive subdivision)
  5. build_dashboard — Analytics Agent (ThreadPool, 4 workers) → UI tiles
  6. finalize_and_save — Write Report Agent (final security audit → report.json)
        │
        ▼
Dashboard renders report (charts, tables, text)
```

The **Data Retrieve ReAct loop** (`run_mcp_loop`) is where the root-cause investigation pattern lives: every LLM turn returns JSON with a `thought` key (the SQL pre-flight check) before an `action` (`get_schemas`, `execute_query`, `semantic_search`, `finish`, `request_subdivision`). Each MCP tool call spawns a short-lived subprocess, runs one call, and tears it down.

## 3. Reasoning and Embedding Models

The planned Root-Cause Agent uses the Azure AI Foundry deployment **`gpt-5.4-mini`** as its reasoning and planning model. It is responsible for interpreting the user's question, planning the investigation, selecting tools, interpreting intermediate evidence, and deciding whether further investigation is required.

Semantic retrieval uses a separate Azure OpenAI embedding deployment, **`text-embedding-3-small`**. This model is not the reasoning agent. It converts a semantic product query into an embedding that can be compared with product embeddings stored in PostgreSQL/pgvector.

Therefore the responsibilities are deliberately separated:

```text
gpt-5.4-mini
    └── reasoning, planning, tool selection, evidence interpretation

text-embedding-3-small
    └── semantic query embedding for Agentic RAG
```

## 4. MCP Tool Strategy

The solution uses the **existing generic tools supplied by the Zava MCP servers as its primary tool boundary**. This is intentional.

Generic tools allow the Root-Cause Agent to adapt its investigation to the evidence. A scenario-specific tool such as `analyse_2023_sales_decline()` would encode a predetermined investigation path inside the tool itself and reduce the agent's planning responsibility. The current tools instead expose reusable data capabilities while leaving the reasoning sequence to the agent.

Additional reusable retail-analysis tools may be introduced later if Phase 5 implementation or evaluation reveals a capability that cannot be performed reliably with the existing tools. New tools should solve a reusable capability gap rather than hard-code this particular sales-decline scenario.

### 4.1 Sales Analysis MCP

The **Sales Analysis MCP** is the primary structured analytical interface to Zava PostgreSQL.

| Tool | Purpose | Typical use |
|---|---|---|
| `get_multiple_table_schemas(table_names)` | Retrieves database schemas/columns required to formulate valid queries. | Discover relevant tables and fields before querying. |
| `execute_sales_query(postgresql_query)` | Executes an analytical PostgreSQL query under the configured RLS identity. | Compare sales, orders, stores, categories, products, customers, and other measurable dimensions. |
| `get_current_utc_date()` | Returns the current UTC date. | Resolve date-dependent analysis when required. |

These tools are broad enough to support adaptive investigation. For example, an initial annual comparison can lead the agent to investigate categories; category evidence may then lead to store or product analysis. That sequence is chosen by the agent rather than encoded in a dedicated root-cause tool.

### 4.2 Customer Sales Semantic Search MCP — Agentic RAG

The **Customer Sales Semantic Search MCP** provides the Agentic RAG capability through `semantic_search_products(query_description, max_rows, similarity_threshold)`.

Unlike a structured SQL filter, the tool supports conceptual retrieval. For example, the user may ask about *bathroom renovation products* even though "bathroom renovation" is not an official Zava database category.

```text
Root-Cause Agent
       │
       │ decides semantic retrieval is useful
       ▼
semantic_search_products
       │
       ▼
text-embedding-3-small
       │
       ▼
Query embedding
       │
       ▼
PostgreSQL / pgvector similarity search
       │
       ▼
Semantically relevant product evidence
       │
       ▼
Root-Cause Agent
```

This is **Agentic RAG** because the reasoning agent decides when semantic retrieval is useful, constructs the semantic search description, invokes the retrieval tool, and incorporates the retrieved evidence into its subsequent reasoning. Retrieved products are semantic candidates; they should not be misrepresented as an official database category.

## 5. MCP Server Selection

Two MCP capabilities are selected for the Root-Cause Agent:

1. **Sales Analysis MCP** — primary quantitative and structured analytical access.
2. **Customer Sales Semantic Search MCP** — semantic product retrieval and Agentic RAG.

The supplied basic Customer Sales MCP is not required for the current architecture because the selected analytical and semantic servers cover the capabilities needed by the use case. The HTTP MCP interface is also not required for the local proof of concept; the selected servers can be connected using the existing local stdio configuration. These supplied components do not need to be removed and can be reconsidered if deployment requirements change.

## 6. MCP Connection and Integration Evidence

Before implementing the final Root-Cause Agent, the MCP capabilities were tested using **GitHub Copilot Agent as an integration/test client**. Copilot is not the planned final agent; the final PoC will use the project's `gpt-5.4-mini` deployment. The tests demonstrate that the MCP boundaries work and that an agent can independently choose and use the available tools.

### 6.1 Sales Analysis MCP Test

Copilot was asked:

> **Why did Zava's sales decline in 2023?**

The prompt did not prescribe a sequence of MCP calls. Copilot independently inspected database schemas and performed multiple SQL investigations. The investigation compared annual sales and orders and then explored category, store, units, average order value, and discount-related evidence.

The analysis found:

- 2022 sales: **$6,441,413.26**
- 2023 sales: **$6,209,863.44**
- decline: **$231,549.82 (~3.59%)**
- orders declined by approximately **3.80%**
- units declined by approximately **3.3%**
- average order value remained broadly stable/slightly higher
- **Power Tools** accounted for a decline of approximately **$112,155**
- large store/channel declines included **Online (~$92,917)**, **Seattle (~$82,833)**, and **Bellevue (~$61,354)**.

Importantly, the response did **not** claim that the database had established an underlying causal mechanism such as inventory, marketing, or customer demand. This test therefore provides evidence for **Tool Use**, adaptive **Planning**, quantitative grounding, and causal restraint.

### 6.2 Semantic Search / Agentic RAG Test

Copilot was separately asked:

> **Find Zava products related to bathroom renovation.**

Without being told which MCP tool to use, it independently selected `semantic_search_products` and generated the following broader semantic description:

> products for bathroom renovation including vanity, sink, faucet, shower, tile, lighting, fixtures, hardware, and waterproofing materials

The semantic call used `max_rows = 10` and `similarity_threshold = 45`. After the Azure embedding configuration and `text-embedding-3-small` deployment were available, the end-to-end semantic retrieval succeeded and returned:

- **Shower Faucet Trim Kit**
- SKU **PLFCT004**
- Category: **Plumbing > Faucets**
- Price: **$146.27**
- Stock: **5,561**
- Description: complete shower trim kit with valve, handle, and showerhead.

Only one result meeting the test threshold was returned; this should not be interpreted as proof that the catalog contains no other bathroom-related products.

This test demonstrates the complete Agentic RAG path:

```text
Natural-language request
        ↓
Agent decides semantic retrieval is appropriate
        ↓
Agent constructs richer semantic search description
        ↓
Semantic Search MCP
        ↓
text-embedding-3-small
        ↓
pgvector similarity retrieval
        ↓
Relevant product evidence
```

An earlier semantic-search attempt failed while the Azure embedding endpoint/deployment was not configured. Copilot then used structured SQL as a fallback. That fallback was useful for troubleshooting but **was not considered Agentic RAG**. The successful test above is the evidence used for the semantic architecture.

## 7. Planning

Planning is a first-class responsibility of the Root-Cause Agent.

The agent should not assume that every performance question requires the same sequence. It can begin with a broad quantitative comparison and use each result to determine the next useful dimension. A simplified example is:

```text
Question: Why did sales decline?
        ↓
Compare annual sales/orders
        ↓
Which measure changed?
        ↓
Inspect categories
        ↓
Which category contributes most?
        ↓
Inspect that category by store/product
        ↓
Does evidence support an explanation?
        ↓
Yes → answer
No  → plan another investigation
```

This behaviour is what distinguishes the proposed agent from a scripted analytics pipeline.

### 7.1 Implemented Adaptive Investigation Rounds

In the implemented system, planning is realised as **result-driven investigation rounds** (`_run_adaptive_investigation` in the Orchestrator):

1. The Orchestrator calls `plan_data_tasks` with the user prompt, data requirements, findings from previous rounds, and completed tasks.
2. The planner returns up to `_MAX_SWARM_TASKS` (4) independent tasks, or signals `investigation_complete`.
3. The swarm runs the tasks in parallel (`asyncio.gather`).
4. Findings from the round feed the next planning call, so each round adapts to prior evidence.
5. The loop stops when the planner decides evidence is sufficient, or after `_MAX_INVESTIGATION_ROUNDS` (3) rounds.

The planner is also constrained by the **authorized store scope**: it receives the list of stores the user can access and is instructed not to plan or request data for any other store.

## 8. Metacognition and Causal Restraint

After tool results are returned, the agent evaluates its own evidence before producing a conclusion.

The metacognitive check asks:

- Is there enough evidence to answer the question?
- Are numerical claims grounded in returned data?
- Are different pieces of evidence consistent?
- Are there unresolved contradictions?
- Is the agent describing a measured contribution/correlation as though it were an established cause?
- Is another tool call likely to materially improve the answer?

If the answer is **NO**, the architecture loops back to **Planning**. If further investigation cannot establish the cause, the final response should explicitly distinguish what the evidence shows from what remains unknown.

For example, a decline in Power Tools sales can establish that the category contributed to the total decline. It does not by itself establish *why* customers purchased fewer Power Tools.

## 9. Context Strategy

The agent should keep its working context focused rather than loading the entire Zava database structure or large result sets into the model context.

The strategy is to:

- provide the agent with the user's question, system instructions, and MCP tool descriptions;
- retrieve table schemas on demand when required;
- use targeted SQL queries rather than broad data dumps;
- retain relevant intermediate findings needed for subsequent planning;
- request limited semantic-search results rather than the full embedding collection;
- summarize prior evidence when the investigation becomes long; and
- preserve provenance between claims and the MCP evidence used to support them.

This reduces unnecessary context usage while retaining enough state for adaptive reasoning.

## 10. Security and Row-Level Security

Security is enforced at the **PostgreSQL data layer**, not only through instructions to the language model.

The Sales Analysis PostgreSQL implementation sets the session configuration `app.current_rls_user_id` before executing SQL. PostgreSQL Row-Level Security policies then determine which records are visible to that identity.

The Root-Cause Agent's intended regional/performance-management use case uses the configured **Super Manager** identity because cross-store comparison is required. The all-zero UUID is defined by the Zava data setup as the Super Manager identity.

Normal store-manager identities remain restricted by RLS. Policies cover transactional data including orders, order items, inventory, and customers. Customer access is more nuanced than order access: a manager may see customers associated with their primary store or customers who have ordered from that manager's store. Reference/product data has different visibility requirements and should not be described as having identical restrictions to transactional customer/order data.

### 10.1 Pre-Flight Store-Access Check

Before any report queries run, the Orchestrator performs a **pre-flight store-access check**:

1. `fetch_metadata` retrieves all store names and the RLS-filtered accessible store list for the user.
2. The user's prompt is checked against every store name, matching both the full name (`Zava Retail Seattle`) and the short name (`Seattle`).
3. If the prompt references a store outside the user's authorized scope — or the user has no accessible stores at all — the pipeline **stops immediately** and returns a clear message naming the inaccessible store(s) and listing the stores the user can access.

For example, a Tacoma manager asking for "Seattle results" receives:

> *"You do not have access to 'Zava Retail Seattle'. Report generation was stopped before any queries were run. You can generate reports for: 'Zava Retail Tacoma'. Please choose a store you can access or contact your administrator."*

This check prevents the LLM from silently substituting an accessible store for a requested inaccessible one, and ensures no data is queried outside the user's RLS scope.

### 10.2 RLS Verification

RLS was tested using the Seattle store-manager identity.

```text
                 PostgreSQL RLS
                       │
           ┌───────────┴───────────┐
           │                       │
     Super Manager           Seattle Manager
           │                       │
     Cross-store access       Restricted scope
                                   │
                   ┌───────────────┼────────────────┐
                   ▼               ▼                ▼
             All-store query   Bellevue order   Bellevue-only
                   │             attempt          customer
                   ▼               ▼                ▼
             Seattle only          0           NOT VISIBLE
             54,744 orders
```

Three tests were performed:

**Test 1 — broad order query.**  
A query grouped orders across all stores while operating under the Seattle identity. The result exposed only **Zava Retail Seattle — 54,744 orders**. Other stores were not exposed.

**Test 2 — explicit cross-store order attempt.**  
While still operating as the Seattle manager, SQL explicitly requested orders belonging to **Zava Retail Bellevue**. The visible count was **0**.

**Test 3 — explicit cross-store customer attempt.**  
A clean Bellevue-only customer was first identified using authorized cross-store access: **customer ID 11, Laura Day**, whose primary store was Bellevue and who had not ordered from Seattle. The Seattle identity then explicitly attempted to retrieve customer ID 11. The result was **not visible**.

These tests demonstrate that generated SQL cannot simply request another store's protected transactional/customer records and bypass the database security boundary. The agent must operate within the data scope authorized by PostgreSQL RLS.

## 11. Failure Handling and Graceful Degradation

The PoC does not claim production-grade high availability or infrastructure failover. Instead, it uses **retry, graceful degradation, and re-planning**.

Examples include:

- malformed/failed SQL → inspect the error or schema, revise the query, and retry;
- empty result → determine whether there is genuinely no matching evidence or whether authorized scope/RLS may limit visibility;
- semantic query with weak/no matches → revise the semantic description or threshold where appropriate;
- Semantic Search MCP or embedding service unavailable → continue structured analysis where appropriate, but explicitly state that semantic retrieval is unavailable;
- Sales Analysis MCP unavailable → do not treat semantic retrieval as a replacement for quantitative analysis;
- PostgreSQL unavailable → do not manufacture data-grounded conclusions;
- RLS-restricted data → respect the authorized scope and never infer or attempt to bypass hidden records;
- insufficient or contradictory evidence → Metacognition returns **NO** and the agent re-enters Planning.

The two MCP servers are **complementary rather than redundant**, so this behaviour should be described as graceful degradation rather than automatic failover.

### 11.1 Implemented Failure Behaviours

The implemented system adds concrete failure-handling mechanisms:

- **Permission-denied detection** — the Data Retrieve Agent recognises explicit PostgreSQL permission failures (`permission denied`, `insufficient privilege`, SQLSTATE 42501, etc.) and stops that retrieval task with a clear message. Empty or single-store query results are **valid** and never treated as permission errors.
- **Recursive task subdivision** — if a retrieval task is too complex, the agent requests subdivision and the Orchestrator splits it into 2–3 simpler sub-tasks (up to `_MAX_SUBDIVISION_DEPTH` = 2). If the depth limit is reached, the task returns "Task too complex — data unavailable" and the report is still written with that gap.
- **LLM retry with validation feedback** — the adaptive investigation planner retries once with the validation error appended; tile generation retries once with validator feedback.
- **Rate-limit backoff** — the LLM client retries with exponential backoff (2s, 4s, 8s, 16s) on rate-limit errors.
- **MCP teardown noise** — short-lived MCP subprocess teardown may raise `ProcessLookupError`; this is handled silently and is harmless.

## 12. Evaluation Strategy

Phase 5 evaluation should test whether the architecture behaves as an agent rather than merely producing plausible prose.

| Evaluation area | Expected behaviour |
|---|---|
| Quantitative grounding | Numerical claims match authoritative database results. |
| Planning/adaptivity | Intermediate evidence influences the next investigation/tool call. |
| Tool selection | The agent selects structured or semantic retrieval according to the question. |
| Agentic RAG | Conceptual product questions can trigger semantic retrieval through the embedding/vector path. |
| Metacognition | Weak, contradictory, or insufficient evidence causes re-planning rather than unsupported conclusions. |
| Causal restraint | Contributions/correlations are not presented as established causal mechanisms without evidence. |
| Security | Restricted identities cannot retrieve protected records outside their RLS scope. |
| Failure behaviour | Tool/configuration/data failures lead to retry, qualified partial results, or explicit limitations rather than fabrication. |

Evaluation should include both successful cases and adversarial/failure cases, including attempts to request cross-store information under a restricted identity.

## 13. Architecture Decision Log

| Decision | Rationale |
|---|---|
| Start with a **single Root-Cause Agent** (Phase 4) | One coherent analytical role is sufficient for the core investigation loop; multi-agent coordination would add complexity without a demonstrated benefit for the reasoning itself. |
| Evolve to a **multi-agent hub-and-spoke** system (Phase 5) | The full report-generation workflow (outline, parallel retrieval, analytics, report writing, security gates) benefits from specialised roles coordinated by an Orchestrator, while the root-cause loop remains the core reasoning pattern. |
| Use **Planning** | The investigation path must adapt to intermediate evidence rather than follow a fixed script. |
| Use **Tool Use** | Quantitative conclusions must be grounded in Zava's authoritative data. |
| Use **Agentic RAG** | Semantic concepts such as "bathroom renovation" cannot always be represented by exact database categories or keywords. |
| Use **Metacognition** | The agent must test evidence sufficiency, contradictions, and causal support and be able to return to Planning. |
| Use existing **generic MCP tools** | They preserve agent planning responsibility and avoid hard-coding the 2023 decline scenario. |
| Allow future reusable tools if needed | Phase 5 may reveal capability/reliability gaps; additions should remain reusable rather than scenario-specific. |
| Select **Sales Analysis MCP + Semantic Search MCP** | Together they provide structured quantitative analysis and semantic retrieval required by the use case. |
| Do not require basic Customer Sales MCP in current design | Its capability is not necessary for the current PoC given the selected interfaces. |
| Use local **stdio MCP connections** for the PoC | Sufficient for local integration; the supplied HTTP interface is not required for the current architecture. |
| Use **`gpt-5.4-mini`** for reasoning | Planned Foundry reasoning/planning model for the Root-Cause Agent. |
| Use **`text-embedding-3-small`** for semantic retrieval | Dedicated embedding model for semantic product search through pgvector. |
| Enforce authorization with **PostgreSQL RLS** | Security remains at the data boundary rather than depending on model compliance. |
| Use **Super Manager** for the target cross-store use case | Regional/performance analysis requires cross-store evidence. |
| Treat Copilot as an **integration/test client only** | MCP testing validates connectivity and agent-tool behaviour; it is not the final Root-Cause Agent. |
| Use graceful degradation rather than claim failover | MCP capabilities are complementary, not redundant high-availability replicas. |
| Use **result-driven investigation rounds** | The planner re-plans each round from prior findings, so the investigation adapts to evidence (up to 3 rounds). |
| Use **swarm execution** for retrieval | Independent tasks run in parallel via `asyncio.gather`, bounded to 4 tasks per round. |
| Use **recursive task subdivision** | Complex tasks are split into 2–3 sub-tasks (max depth 2) instead of failing. |
| Use **pre-flight store-access check** | The prompt is checked against the user's RLS-visible stores (full + short names) before any query runs, with a clear denial message. |
| Use **inline tile validation** | Analytics tiles are validated (type, non-empty data, headers/rows) with one retry on failure. |
| Use **short-lived MCP sessions** | Each tool call spawns a subprocess, runs one call, and tears it down — no session held across loop iterations. |

## 14. Phase 4 Outcome

The Phase 4 architecture provides an adaptive Root-Cause Agent that can plan an investigation, access quantitative and semantic evidence through MCP, evaluate the sufficiency of that evidence, and re-plan when necessary. The Phase 5 implementation wraps this core loop in a multi-agent hub-and-spoke system that adds parallel data retrieval (swarm execution), recursive task subdivision, analytics tile generation, and report writing under Orchestrator coordination.

The architecture deliberately keeps **reasoning in the agent, reusable capabilities in MCP tools, semantic retrieval in the Agentic RAG path, and authorization in PostgreSQL RLS**.

The completed Phase 4 validation has established:

- successful Sales Analysis MCP connectivity and quantitative querying;
- successful end-to-end Semantic Search MCP / Agentic RAG retrieval using `text-embedding-3-small`;
- adaptive MCP usage demonstrated through an agent integration client;
- PostgreSQL RLS isolation for store-level orders and customers;
- a pre-flight store-access check that stops out-of-scope requests before any query runs; and
- a defined failure-handling, metacognition, and evaluation strategy for the Phase 5 PoC.

The next phase is to implement and evaluate the Root-Cause Agent itself using the selected `gpt-5.4-mini` reasoning deployment.
