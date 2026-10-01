#!/usr/bin/env python3

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("root-cause-agent")

# Load .env from workspace root, then script dir
for env_path in [
    Path(__file__).resolve().parents[3] / ".env",  
    Path(__file__).resolve().parent / ".env",
]:
    if env_path.exists():
        load_dotenv(env_path)
        break

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]  # /workspace

# MCP server entry points (stdio)
SALES_ANALYSIS_SERVER = (
    WORKSPACE_ROOT
    / "src/python/mcp_server/sales_analysis/sales_analysis.py"
)
SEMANTIC_SEARCH_SERVER = (
    WORKSPACE_ROOT
    / "src/python/mcp_server/customer_sales/customer_sales_semantic_search.py"
)

# Super Manager RLS identity (cross-store access for regional/performance managers), 
# haven't confured this yet, so using a placeholder
SUPER_MANAGER_RLS_ID = "00000000-0000-0000-0000-000000000000"

# Reasoning model (Azure AI Foundry deployment)
GPT_MODEL_DEPLOYMENT_NAME = os.getenv(
    "GPT_MODEL_DEPLOYMENT_NAME", "gpt-5.4-mini"
)

# Maximum investigation iterations before forcing an answer
MAX_ITERATIONS = int(os.getenv("ROOT_CAUSE_MAX_ITERATIONS", "8"))


# ---------------------------------------------------------------------------
# MCP client helpers
# ---------------------------------------------------------------------------
async def _run_mcp_tool(server_script: Path, tool_name: str, arguments: Dict[str, Any]) -> str:
    """Run a single MCP tool call against a stdio MCP server and return its text result."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[
            str(server_script),
            "--stdio",
            f"--RLS_USER_ID={SUPER_MANAGER_RLS_ID}",
        ],
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            # Extract text content
            parts = []
            if result and result.content:
                for item in result.content:
                    if hasattr(item, "text"):
                        parts.append(item.text)
                    else:
                        parts.append(str(item))
            return "\n".join(parts)


async def sales_tool(tool_name: str, **kwargs) -> str:
    """Call a Sales Analysis MCP tool."""
    logger.info("🔧 [Sales Analysis] %s(%s)", tool_name, kwargs)
    return await _run_mcp_tool(SALES_ANALYSIS_SERVER, tool_name, kwargs)


async def semantic_tool(tool_name: str, **kwargs) -> str:
    """Call a Semantic Search MCP tool."""
    logger.info("🔧 [Semantic Search] %s(%s)", tool_name, kwargs)
    return await _run_mcp_tool(SEMANTIC_SEARCH_SERVER, tool_name, kwargs)


# ---------------------------------------------------------------------------
# LLM client (Azure AI Foundry / OpenAI-compatible)
# ---------------------------------------------------------------------------
def _get_llm_client():
    """Return an OpenAI-compatible client for the Foundry gpt-5.4-mini deployment."""
    from openai import AzureOpenAI

    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_key = os.getenv("AZURE_OPENAI_KEY")

    if not endpoint:
        raise RuntimeError(
            "AZURE_OPENAI_ENDPOINT is not set. Run `infra/deploy.sh` or set it in .env"
        )

    if api_key:
        return AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )

    # Fall back to DefaultAzureCredential (az login)
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
    )
    return AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
    )


# ---------------------------------------------------------------------------
# Tool registry exposed to the LLM
# ---------------------------------------------------------------------------
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_multiple_table_schemas",
            "description": (
                "Retrieve database schemas/columns for Zava retail tables. "
                "Use this to discover relevant tables and fields before querying. "
                "Valid tables: retail.customers, retail.stores, retail.categories, "
                "retail.product_types, retail.products, retail.orders, "
                "retail.order_items, retail.inventory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "table_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of table names to inspect.",
                    }
                },
                "required": ["table_names"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_sales_query",
            "description": (
                "Execute an analytical PostgreSQL query against Zava data under "
                "the configured RLS identity. Use exact column names from schemas, "
                "join related tables, aggregate results, and limit output."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "postgresql_query": {
                        "type": "string",
                        "description": "A well-formed PostgreSQL query.",
                    }
                },
                "required": ["postgresql_query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_utc_date",
            "description": "Get the current UTC date/time. Useful for date-based analysis.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search_products",
            "description": (
                "Search Zava products by semantic similarity to a natural-language "
                "description (Agentic RAG). Use when the question is about product "
                "concepts that may not match exact category names (e.g. 'bathroom "
                "renovation'). Retrieved products are semantic candidates, not an "
                "official category."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query_description": {
                        "type": "string",
                        "description": "Natural-language description of the products to find.",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "Maximum number of rows to return.",
                    },
                    "similarity_threshold": {
                        "type": "number",
                        "description": "Minimum similarity threshold (0-100).",
                    },
                },
                "required": ["query_description"],
            },
        },
    },
]


async def _dispatch_tool(name: str, args: Dict[str, Any]) -> str:
    """Dispatch a tool call to the appropriate MCP server."""
    if name == "get_multiple_table_schemas":
        return await sales_tool(name, table_names=args["table_names"])
    if name == "execute_sales_query":
        return await sales_tool(name, postgresql_query=args["postgresql_query"])
    if name == "get_current_utc_date":
        return await sales_tool(name)
    if name == "semantic_search_products":
        return await semantic_tool(
            name,
            query_description=args["query_description"],
            max_rows=args.get("max_rows", 10),
            similarity_threshold=args.get("similarity_threshold", 50.0),
        )
    return f"Error: unknown tool '{name}'"


# ---------------------------------------------------------------------------
# System prompt (embeds the architecture's metacognition + causal restraint)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are the Zava Retail Performance Root-Cause Agent.

You investigate WHY retail performance changed (e.g. sales declined) across
years, stores, categories, products, or customer segments, and return an
evidence-grounded explanation.

You operate in an iterative investigation loop:

1. PLANNING — decide the next useful investigation based on the question and
   the evidence you have collected so far. Do not assume a fixed sequence.
2. TOOL USE — call the Zava MCP tools to get authoritative data:
   - get_multiple_table_schemas: discover tables/columns before querying.
   - execute_sales_query: run analytical SQL (aggregate, join, limit output).
   - get_current_utc_date: resolve date-dependent analysis.
   - semantic_search_products: use for CONCEPTUAL product discovery (Agentic
     RAG) when exact categories/keywords are not enough.
3. METACOGNITION — before answering, check:
   - Is there enough evidence to answer the question?
   - Are numerical claims grounded in returned data?
   - Are different pieces of evidence consistent?
   - Are there unresolved contradictions?
   - Am I presenting a measured contribution/correlation as an established
     cause? (Do NOT. Distinguish what the evidence shows from what is unknown.)
   - Would another tool call materially improve the answer?
   If evidence is weak/contradictory/incomplete, PLAN ANOTHER investigation
   instead of answering.

RULES:
- Always fetch table schemas before writing SQL. Use exact column names.
- Ground every number in tool results. Never invent figures.
- A category/store contributing to a decline is NOT proof of WHY customers
  bought less. State what the data shows and what remains unknown.
- Respect Row Level Security scope. Do not attempt to bypass it.
- If a tool fails, retry with a corrected query, or degrade gracefully and
  state the limitation. Never fabricate data.
- When you have enough evidence, produce a final answer that clearly separates
  evidence-backed findings from open questions.
"""


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------
async def run_investigation(question: str) -> str:
    """Run the Root-Cause Agent investigation loop for a question."""
    client = _get_llm_client()

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    # Track evidence for the final summary
    evidence_log: List[str] = []

    for iteration in range(1, MAX_ITERATIONS + 1):
        logger.info("=" * 60)
        logger.info("🔄 Iteration %d/%d — PLANNING", iteration, MAX_ITERATIONS)
        logger.info("=" * 60)

        response = client.chat.completions.create(
            model=GPT_MODEL_DEPLOYMENT_NAME,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            max_completion_tokens=4096,
        )

        message = response.choices[0].message

        # No tool calls → the agent decided to answer
        if not message.tool_calls:
            logger.info("Agent produced a final answer (no more tool calls).")
            return message.content or "(empty answer)"

        # Append assistant message with tool calls
        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ],
            }
        )

        # Execute each tool call
        for tc in message.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            logger.info("🔧 Calling tool: %s", name)
            result = await _dispatch_tool(name, args)

            # Truncate very long results for the log
            log_result = result if len(result) <= 2000 else result[:2000] + "...[truncated]"
            logger.info("📦 Tool result (%d chars):\n%s", len(result), log_result)
            evidence_log.append(f"[{name}] {log_result}")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )

    # Max iterations reached without a final answer
    logger.warning("⚠️  Max iterations (%d) reached without a final answer.", MAX_ITERATIONS)
    return (
        "I reached the maximum number of investigation steps without a final "
        "answer. Here is the evidence collected so far:\n\n"
        + "\n\n".join(evidence_log[-10:])
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    global MAX_ITERATIONS

    parser = argparse.ArgumentParser(
        description="Zava Retail Performance Root-Cause Agent (PoC)"
    )
    parser.add_argument(
        "question",
        nargs="?",
        default="Why did Zava's sales decline in 2023?",
        help="The performance question to investigate.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=MAX_ITERATIONS,
        help="Maximum investigation iterations.",
    )
    args = parser.parse_args()

    MAX_ITERATIONS = args.max_iterations

    logger.info("🚀 Zava Root-Cause Agent (PoC)")
    logger.info("   Model: %s", GPT_MODEL_DEPLOYMENT_NAME)
    logger.info("   Question: %s", args.question)
    logger.info("   Max iterations: %d", MAX_ITERATIONS)

    answer = asyncio.run(run_investigation(args.question))

    print("\n" + "=" * 60)
    print("FINAL ANSWER")
    print("=" * 60)
    print(answer)
    print("=" * 60)


if __name__ == "__main__":
    main()