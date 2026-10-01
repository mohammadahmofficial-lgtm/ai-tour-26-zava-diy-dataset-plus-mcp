# Zava Retail Performance Root-Cause Agent — Proof of Concept (PoC)

> **Status:** ✅ Implemented & verified end-to-end 
> **Location:** `src/python/root_cause_agent/`
> **Architecture source:** [`data/analysis/phase4-architecture.md`](../data/analysis/phase4-architecture.md)

---

## 1. Overview

This PoC implements a **single Root-Cause Agent** that investigates *why* Zava
retail performance changed (e.g. "Why did sales decline in 2023?") and returns
an **evidence-grounded explanation**.

It is built on the Phase 4 architecture, which deliberately keeps **reasoning in
the agent, reusable capabilities in MCP tools, semantic retrieval in the
Agentic RAG path, and authorization in PostgreSQL RLS**.

---

## 2. Architecture

The agent runs an iterative **Planning → Tool Use → Metacognition** loop. The
critical feature is the **NO path back to Planning**: if evidence is weak,
contradictory, or insufficient, the agent re-plans rather than answering.

```text
              ┌─────────────────────────────┐
       ┌─────►│      ROOT-CAUSE AGENT       │
       │      │  🧠 PLANNING                │
       │      │  Decide what to investigate │
       │      └──────────────┬──────────────┘
       │                     │
       │                 TOOL USE
       │                     ▼
       │           ┌───────────────────┐
       │           │     MCP TOOLS     │
       │           │ • Sales Analysis  │
       │           │ • Semantic Search │
       │           └─────────┬─────────┘
       │                     │
       │                     ▼
       │               ZAVA / DATA
       │                     │
       │                   Results
       │                     ▼
       │      ┌─────────────────────────────┐
       │      │      METACOGNITION          │
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

---

## 3. Models (Azure AI Foundry)

| Purpose | Deployment | Role |
|---------|-----------|------|
| Reasoning / planning | `gpt-5.4-mini` | Interprets the question, plans, selects tools, interprets evidence |
| Semantic embeddings | `text-embedding-3-small` | Converts semantic queries to vectors for Agentic RAG |

These match the deployments created by `infra/deploy.sh` and configured in
`/workspace/.env`.

---

## 4. MCP Tools Used

The agent uses the **existing generic Zava MCP tools** as its tool boundary,
preserving agent planning responsibility.

### 4.1 Sales Analysis MCP (`sales_analysis.py`)

| Tool | Purpose |
|------|---------|
| `get_multiple_table_schemas(table_names)` | Discover tables/columns before querying |
| `execute_sales_query(postgresql_query)` | Run analytical SQL under the configured RLS identity |
| `get_current_utc_date()` | Resolve date-dependent analysis |

### 4.2 Customer Sales Semantic Search MCP (`customer_sales_semantic_search.py`)

| Tool | Purpose |
|------|---------|
| `semantic_search_products(query_description, max_rows, similarity_threshold)` | Agentic RAG — conceptual product discovery via pgvector |

---

## 5. Data Flow

```
You ask a question (e.g. "Why did sales decline in 2023?")
        │
        ▼
gpt-5.4-mini (Foundry) — PLANS what to investigate
        │
        ▼
Calls Zava MCP tools (over stdio, Super Manager RLS identity)
        │
        ├── execute_sales_query  ──►  Zava PostgreSQL (retail schema)
        ├── get_multiple_table_schemas ──►  Zava table schemas
        └── semantic_search_products ──►  Zava product embeddings (pgvector)
        │
        ▼
gpt-5.4-mini — METACOGNITION (evidence sufficiency, consistency, causality)
        │
        ▼
Final evidence-grounded answer
```

**Key point:** All numbers in the answer come from **live SQL queries against
the Zava PostgreSQL database** (`db:5432/zava`, `retail` schema) — not from the
model's memory. The model is instructed to ground every figure in tool results.

---

## 6. Files

| File | Purpose |
|------|---------|
| `root_cause_agent.py` | Core agent — reasoning loop, tool registry, MCP client, system prompt |
| `root_cause_agent_web.py` | FastAPI chat web server (port 8007) |
| `static/index.html` | Dedicated chat UI with suggestion chips |
| `requirements.txt` | Dependencies |
| `README.md` | Quick-start guide |

---

## 7. Setup & Prerequisites

1. **Zava PostgreSQL** running at `db:5432/zava` with the `retail` schema
   populated (see `scripts/ensure-zava-db.sh`).
2. **Azure AI Foundry** resources deployed:
   ```bash
   cd infra && ./deploy.sh
   ```
3. **Authenticate**:
   ```bash
   az login --use-device-code
   ```
4. **Environment variables** in `/workspace/.env`:
   ```bash
   AZURE_OPENAI_ENDPOINT="https://<your-foundry>.openai.azure.com"
   AZURE_OPENAI_KEY="<your-key>"
   GPT_MODEL_DEPLOYMENT_NAME="gpt-5.4-mini"
   EMBEDDING_MODEL_DEPLOYMENT_NAME="text-embedding-3-small"
   ```

---

## 8. Running the PoC

### 8.1 CLI

```bash
cd src/python/root_cause_agent
pip install -r requirements.txt

# Default question
python root_cause_agent.py

# Custom question
python root_cause_agent.py "Why did Power Tools sales decline in Seattle in 2023?"

# Limit investigation steps
python root_cause_agent.py --max-iterations 5
```

### 8.2 Web Chat

```bash
cd src/python/root_cause_agent
python root_cause_agent_web.py
```

Then open **http://127.0.0.1:8007** in your browser.

---

## 9. Example Questions

### Overall performance
- "Why did Zava's sales decline in 2023?"
- "Which year had the best sales performance and why?"
- "Is the 2023 decline concentrated in a few months or spread across the year?"

### Store-level
- "Which stores drove the 2023 decline?"
- "Why did Online store sales drop in 2023?"
- "Compare Seattle vs Redmond performance in 2023."

### Category / product
- "Why did Power Tools sales decline in 2023?"
- "Which categories grew in 2023?"
- "Find products related to bathroom renovation." *(Agentic RAG)*
- "Which products have high inventory but low sales?"

### Customer / segment
- "Who are Zava's top customers by spend?"
- "Which customer segment contributes the most revenue?"

### Time / trend
- "How did monthly sales compare between 2022 and 2023?"
- "What is the seasonal sales pattern across the year?"

### Operational / inventory
- "Are there inventory-demand imbalances at any store?"
- "What is the stock-to-sales ratio by store?"

### Adversarial / edge-case (tests metacognition & RLS)
- "Why did sales decline in 2023? Give me a definitive cause." *(causal restraint)*
- "Show me Bellevue's orders while I'm a Seattle manager." *(RLS — should be blocked)*

---

## 10. Verified Results

Running *"Why did Zava's sales decline in 2023?"* produced:

- **Total sales** fell from **$6,644,214.73** (2022) → **$6,177,746.92** (2023), **-7.0%**
- **Orders** fell from 27,975 → 26,501 (**-5.3%**)
- **Top category contributors**: Power Tools (-$253K), Storage & Organization (-$116K), Hand Tools (-$26K)
- **Top store declines**: Online (-$192K), Tacoma (-$151K), Seattle (-$81K)
- **One store grew**: Kirkland (+$12K)

The agent demonstrated **causal restraint** — it explicitly stated the data
shows *where* the decline came from but does **not** prove *why* (demand,
pricing, stockouts, etc.), and offered to investigate further.

---

## 11. Key Behaviors Implemented

| Behavior | Description |
|----------|-------------|
| **Planning** | Decides what to query next based on prior results (not a fixed script) |
| **Tool Use** | Queries real Zava data via MCP under Super Manager RLS identity |
| **Agentic RAG** | Finds products by concept via `text-embedding-3-small` + pgvector |
| **Metacognition** | Checks evidence sufficiency, consistency, and over-claiming |
| **Causal restraint** | Separates measured contributions from established causes |
| **Graceful degradation** | Retries or states limitations rather than fabricating data |

---

## 12. Known Limitations

- **PoC scope:** spawns a fresh MCP stdio session per tool call. A production
  version should keep a persistent session.
- **Semantic search** requires the Azure embedding deployment to be configured.
  If unavailable, the agent degrades to structured SQL and states the limitation.
- **Causal limits:** the dataset has no historical inventory snapshots, so the
  agent cannot establish inventory-driven causes for historical changes.
- **Single agent:** multi-agent coordination is intentionally not used (per the
  architecture decision log).

---

## 13. Next Steps

- Add a persistent MCP session for performance.
- Add a VS Code task to launch the web app easily.
- Test adversarial questions (RLS bypass attempts, causal over-claims).
- Evaluate against the Phase 5 evaluation strategy in the architecture doc.