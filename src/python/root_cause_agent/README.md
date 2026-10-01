# Zava Retail Performance Root-Cause Agent — Proof of Concept

A single-agent PoC implementing the **Phase 4 architecture**
(`data/analysis/phase4-architecture.md`).

## What it does

Investigates *why* Zava retail performance changed (e.g. "Why did sales decline
in 2023?") using an iterative **Planning → Tool Use → Metacognition** loop:

1. **Planning** — the agent decides what to investigate next based on the
   question and evidence collected so far.
2. **Tool Use** — it calls the Zava MCP tools for authoritative data:
   - `get_multiple_table_schemas` — discover tables/columns
   - `execute_sales_query` — run analytical SQL (RLS-scoped)
   - `get_current_utc_date` — date context
   - `semantic_search_products` — Agentic RAG for conceptual product discovery
3. **Metacognition** — before answering, it checks evidence sufficiency,
   consistency, and causal restraint. If evidence is weak/contradictory, it
   loops back to Planning.

## Models (Azure AI Foundry)

| Purpose | Deployment |
|---------|-----------|
| Reasoning / planning | `gpt-5.4-mini` |
| Semantic embeddings (Agentic RAG) | `text-embedding-3-small` |

These match the deployments created by `infra/deploy.sh`.

## Prerequisites

- Zava PostgreSQL running (`db:5432/zava`) with the `retail` schema populated.
- Azure AI Foundry resources deployed (`cd infra && ./deploy.sh`) and
  authenticated (`az login`).
- Environment variables (written to `.env` by `infra/deploy.sh`):
  - `AZURE_OPENAI_ENDPOINT`
  - `AZURE_OPENAI_KEY` (or use `az login` + DefaultAzureCredential)
  - `GPT_MODEL_DEPLOYMENT_NAME` (default `gpt-5.4-mini`)

## Run

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

## How it connects

The agent launches the existing MCP servers over **stdio** using the `mcp`
Python SDK, running under the **Super Manager** RLS identity
(`00000000-0000-0000-0000-000000000000`) for cross-store analysis.

## Notes / limitations

- This is a PoC: it spawns a fresh MCP stdio session per tool call. A
  production version should keep a persistent session.
- Semantic search requires the Azure embedding deployment to be configured.
  If it is unavailable, the agent should degrade to structured SQL and state
  the limitation.
- The agent is instructed to practice **causal restraint**: a category/store
  contributing to a decline is not presented as proof of *why* customers
  bought less.