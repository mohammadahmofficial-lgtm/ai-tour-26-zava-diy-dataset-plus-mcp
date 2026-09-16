# Phase 4 — Agent Architecture

## 1. Architecture Goal

The proposed solution is a **Retail Performance Root-Cause Agent** for regional and retail performance managers. Its purpose is to investigate questions such as why sales performance changed across years, stores, categories, products, or customer segments, and to return an evidence-grounded explanation.

The architecture deliberately keeps the agent responsible for deciding **what to investigate next**. It does not encode a fixed root-cause workflow. Intermediate evidence can change the next query or tool call, and the agent must avoid presenting correlation or incomplete evidence as an established business cause.

The selected architecture combines four agent patterns:

- **Planning** — determine the next useful investigation based on the question and evidence collected so far.
- **Tool Use** — access authoritative Zava data through MCP tools rather than relying on model knowledge.
- **Agentic RAG** — use semantic retrieval when the investigation requires concept-based product discovery rather than exact SQL categories or keywords.
- **Metacognition** — evaluate whether the evidence is sufficient, internally consistent, and capable of supporting the proposed explanation before answering.

A **multi-agent architecture is intentionally not used**. The use case does not currently contain genuinely independent specialist roles that require separate agents. Introducing multiple agents would add coordination, context-sharing, result-merging, latency, and additional failure modes without a clear benefit for this proof of concept. A single agent can plan, select tools, inspect evidence, and revise its investigation while maintaining one coherent analytical context.

## 2. Main Agent Reasoning Loop

The central design is an iterative investigation loop rather than a predetermined sequence.

```text
              ┌─────────────────────────────┐
       ┌─────►│      ROOT-CAUSE AGENT       │
       │      │                             │
       │      │  🧠 PLANNING                │
       │      │  Decide what to investigate │
       │      └──────────────┬──────────────┘
       │                     │
       │                 TOOL USE
       │                     ▼
       │           ┌───────────────────┐
       │           │     MCP TOOLS     │
       │           │                   │
       │           │ • Sales Analysis  │
       │           │ • Semantic Search │
       │           │   (Agentic RAG)   │
       │           └─────────┬─────────┘
       │                     │
       │                     ▼
       │               ZAVA / DATA
       │                     │
       │                   Results
       │                     ▼
       │      ┌─────────────────────────────┐
       │      │      METACOGNITION          │
       │      │                             │
       │      │ • Evidence sufficient?      │
       │      │ • Claims supported?         │
       │      │ • Contradictions?           │
       │      │ • Unsupported causality?    │
       │      └──────────────┬──────────────┘
       │                     │
       │              Enough evidence?
       │                /           \
       │              NO             YES
       │              │               │
       │              ▼               ▼
       │        ┌───────────┐    ┌───────────┐
       │        │ PLAN NEXT │    │   FINAL   │
       │        │ TOOL CALL │    │  ANSWER   │
       │        └─────┬─────┘    └───────────┘
       │              │
       └──────────────┘
          BACK TO PLANNING
```

The important feature is the **NO path back to Planning**. Metacognition is not only a final confidence check. If the available evidence is weak, contradictory, incomplete, or does not support causality, the agent returns to the planning stage and chooses another investigation.

## 3. Reasoning and Embedding Models

The planned Root-Cause Agent uses the Azure AI Foundry deployment **`gpt-5-mini`** as its reasoning and planning model. It is responsible for interpreting the user's question, planning the investigation, selecting tools, interpreting intermediate evidence, and deciding whether further investigation is required.

Semantic retrieval uses a separate Azure OpenAI embedding deployment, **`text-embedding-3-small`**. This model is not the reasoning agent. It converts a semantic product query into an embedding that can be compared with product embeddings stored in PostgreSQL/pgvector.

Therefore the responsibilities are deliberately separated:

```text
gpt-5-mini
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

Before implementing the final Root-Cause Agent, the MCP capabilities were tested using **GitHub Copilot Agent as an integration/test client**. Copilot is not the planned final agent; the final PoC will use the project's `gpt-5-mini` deployment. The tests demonstrate that the MCP boundaries work and that an agent can independently choose and use the available tools.

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

### 10.1 RLS Verification

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
| Use a **single Root-Cause Agent** | One coherent analytical role is sufficient; multi-agent coordination would add complexity without a demonstrated benefit. |
| Use **Planning** | The investigation path must adapt to intermediate evidence rather than follow a fixed script. |
| Use **Tool Use** | Quantitative conclusions must be grounded in Zava's authoritative data. |
| Use **Agentic RAG** | Semantic concepts such as "bathroom renovation" cannot always be represented by exact database categories or keywords. |
| Use **Metacognition** | The agent must test evidence sufficiency, contradictions, and causal support and be able to return to Planning. |
| Use existing **generic MCP tools** | They preserve agent planning responsibility and avoid hard-coding the 2023 decline scenario. |
| Allow future reusable tools if needed | Phase 5 may reveal capability/reliability gaps; additions should remain reusable rather than scenario-specific. |
| Select **Sales Analysis MCP + Semantic Search MCP** | Together they provide structured quantitative analysis and semantic retrieval required by the use case. |
| Do not require basic Customer Sales MCP in current design | Its capability is not necessary for the current PoC given the selected interfaces. |
| Use local **stdio MCP connections** for the PoC | Sufficient for local integration; the supplied HTTP interface is not required for the current architecture. |
| Use **`gpt-5-mini`** for reasoning | Planned Foundry reasoning/planning model for the Root-Cause Agent. |
| Use **`text-embedding-3-small`** for semantic retrieval | Dedicated embedding model for semantic product search through pgvector. |
| Enforce authorization with **PostgreSQL RLS** | Security remains at the data boundary rather than depending on model compliance. |
| Use **Super Manager** for the target cross-store use case | Regional/performance analysis requires cross-store evidence. |
| Treat Copilot as an **integration/test client only** | MCP testing validates connectivity and agent-tool behaviour; it is not the final Root-Cause Agent. |
| Use graceful degradation rather than claim failover | MCP capabilities are complementary, not redundant high-availability replicas. |

## 14. Phase 4 Outcome

The Phase 4 architecture provides a single adaptive Root-Cause Agent that can plan an investigation, access quantitative and semantic evidence through MCP, evaluate the sufficiency of that evidence, and re-plan when necessary.

The architecture deliberately keeps **reasoning in the agent, reusable capabilities in MCP tools, semantic retrieval in the Agentic RAG path, and authorization in PostgreSQL RLS**.

The completed Phase 4 validation has established:

- successful Sales Analysis MCP connectivity and quantitative querying;
- successful end-to-end Semantic Search MCP / Agentic RAG retrieval using `text-embedding-3-small`;
- adaptive MCP usage demonstrated through an agent integration client;
- PostgreSQL RLS isolation for store-level orders and customers; and
- a defined failure-handling, metacognition, and evaluation strategy for the Phase 5 PoC.

The next phase is to implement and evaluate the Root-Cause Agent itself using the selected `gpt-5-mini` reasoning deployment.
