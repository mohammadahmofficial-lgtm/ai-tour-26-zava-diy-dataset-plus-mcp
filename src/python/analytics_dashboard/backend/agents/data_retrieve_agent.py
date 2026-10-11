"""
Data Retrieve Agent — autonomous SQL exploration loop backed by an on-demand MCP server.

Each call to run_mcp_loop:
  1. Drives an LLM in a ReAct loop (think → act → observe).
  2. Spawns a fresh MCP subprocess only for the duration of each tool call, then shuts it down cleanly.
  3. Returns a result dict: {'status': 'success'|'subdivide'|'error', 'data': str, 'queries': list}.
"""
import asyncio
import contextlib
import io
import json
import os
import re

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import SQL_VALIDATION_MAX_ITERATION
from .llm_client import call_llm

# Resolved once at import time
_MCP_SCRIPT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../mcp_server/sales_analysis/sales_analysis.py")
)
_SEMANTIC_MCP_SCRIPT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../mcp_server/customer_sales/customer_sales_semantic_search.py")
)

_PROCESS_ERRORS = ("ProcessLookupError", "No such process", "TimeoutError", "deadline exceeded", "CancelledError")

_SYSTEM_PROMPT_TEMPLATE = """\
You are an autonomous Database Retrieval Agent. Your goal is to retrieve data that fulfills the specific task assigned to you.
You must first check the schemas of relevant tables, then execute SQL queries, and finally synthesise the data.

You MUST respond with a strict JSON object on every turn containing a 'thought' key BEFORE the 'action' key.
Use the 'thought' field to double-check column names against the schema and verify SQL syntax BEFORE executing.

Valid actions:
1. {{"thought": "I need the schemas for customers and orders.", "action": "get_schemas", "tables": ["retail.customers", "retail.orders"]}}
2. {{"thought": "I verified the JOIN columns. Syntax looks correct.", "action": "execute_query", "query": "SELECT ..."}}
3. {{"thought": "The request describes products without exact names.", "action": "semantic_search", "query_description": "weatherproof electrical protection for outdoor connections", "max_rows": 10, "similarity_threshold": 50}}
4. {{"thought": "I have all the required data.", "action": "finish", "data": "Synthesised Markdown tables..."}}
5. {{"thought": "I've tried 5+ times and the task is genuinely too complex.", "action": "request_subdivision", "failed_query": "...", "intent": "...", "validation_feedback": "..."}}

Rules:
- PRE-FLIGHT CHECK: Verify table relationships, JOIN keys, and join cardinality against the schemas before executing SQL.
- JOIN SAFETY: Never join tables merely because they share a column name. Use the actual entity relationship and its identifying keys. For hierarchical tables, distinguish parent-category keys from child-type keys.
- AGGREGATION SAFETY: Ensure each fact row contributes only once to SUM/COUNT calculations unless duplication is explicitly intended. Check for accidental many-to-many joins and inflated totals.
- COMPARISON CONSISTENCY: When comparing periods, stores, categories, or channels, use identical JOIN paths, grouping dimensions, filters, and metric definitions across all compared datasets. Never compare category-level aggregates against product-type-level aggregates. Prefer a single SQL query with conditional aggregation or GROUP BY period to ensure consistency.
- Always fetch schemas first.
- Use semantic_search for vague product descriptions, not ordinary table, metric, or date filtering. After semantic search returns matches, every subsequent SQL query must filter on all returned product_id values.
- For semantic matches, join the products table and use a predicate such as p.product_id IN (123, 456). If no IDs are returned, do not run follow-up SQL.
- Use exact column names.
- NEVER download raw tables. The dataset contains millions of rows.
- Push all math to the database: SUM, COUNT, GROUP BY, DATE_TRUNC, window functions.
- Always JOIN the products table to include product_name — raw IDs are not acceptable.
- For co-purchase / market-basket tasks, use SQL self-joins for pairs (a.product_id < b.product_id). Do NOT use STRING_AGG.
- Use LIMIT only for Top-N aggregated results (e.g. ORDER BY count DESC LIMIT 10).
- You MUST attempt at least 5 queries before using 'request_subdivision' as a last resort.
- Run up to {max_iter} queries to gather all required data.
- Output nothing except the JSON object.{context_block}"""


async def _call_mcp_tool(server_params: StdioServerParameters, tool_name: str, arguments: dict) -> str:
    """Open a short-lived MCP session, call one tool, and shut it down cleanly."""
    cm = stdio_client(server_params)
    read, write = await cm.__aenter__()
    try:
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=arguments)
            return result.content[0].text if result.content else ""
    finally:
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                await write.aclose()
            except Exception:
                pass
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass


def _extract_product_ids(semantic_output: str) -> list[object]:
    """Extract deduplicated product IDs from the semantic MCP tool response."""
    _, separator, payload_text = semantic_output.partition("\n")
    if not separator:
        return []

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return []

    results = payload.get("results", []) if isinstance(payload, dict) else []
    product_ids = [
        result["product_id"]
        for result in results
        if isinstance(result, dict) and result.get("product_id") is not None
    ]
    return list(dict.fromkeys(product_ids))


def _query_includes_product_ids(query: str, product_ids: list[object]) -> bool:
    """Require every semantic match in a product_id IN predicate."""
    match = re.search(r"\b(?:\w+\.)?product_id\s+IN\s*\(([^)]*)\)", query, re.IGNORECASE)
    if not match:
        return False

    return all(
        re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(str(product_id))}(?![A-Za-z0-9_-])",
            match.group(1),
            re.IGNORECASE,
        )
        for product_id in product_ids
    )


async def run_mcp_loop(task: str, rls_user_id: str, context: str = "") -> dict:
    """
    Drive the ReAct loop for one data-retrieval task.
    Returns {'status': 'success'|'subdivide'|'error', 'data': str, 'queries': list}.
    """
    server_params = StdioServerParameters(
        command="python",
        args=[_MCP_SCRIPT, "--stdio", f"--RLS_USER_ID={rls_user_id}"],
    )
    semantic_server_params = StdioServerParameters(
        command="python",
        args=[_SEMANTIC_MCP_SCRIPT, "--stdio", f"--RLS_USER_ID={rls_user_id}"],
    )
    context_block = f"\n\nOVERALL REPORT CONTEXT (for reference only):\n{context}" if context else ""
    system_content = _SYSTEM_PROMPT_TEMPLATE.format(max_iter=SQL_VALIDATION_MAX_ITERATION, context_block=context_block)

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"Your specific task:\n{task}"},
    ]
    approved_queries: list[str] = []
    matched_product_ids: list[object] = []
    semantic_search_performed = False

    for i in range(SQL_VALIDATION_MAX_ITERATION):
        print(f"[DataRetrieveAgent] MCP Loop Iteration {i + 1}/{SQL_VALIDATION_MAX_ITERATION}…")
        response_str = call_llm(messages, json_mode=True)

        try:
            action_json = json.loads(response_str)
        except Exception as e:
            print(f"[DataRetrieveAgent] JSON parse error: {e}")
            messages.append({"role": "user", "content": f"JSON parse error: {e}. Return valid JSON with an 'action' key."})
            continue

        messages.append({"role": "assistant", "content": response_str})
        action = action_json.get("action")

        if action == "finish":
            print("[DataRetrieveAgent] ✓ Finished retrieving data.")
            return {"status": "success", "data": action_json.get("data", "No data provided."), "queries": approved_queries}

        if action == "request_subdivision":
            if i < 4:
                print(f"[DataRetrieveAgent] Subdivision requested too early (iter {i + 1}). Forcing retry.")
                messages.append({
                    "role": "user",
                    "content": "You cannot request subdivision until you have attempted at least 5 queries. Try a complex aggregation SQL query first.",
                })
                continue
            print("[DataRetrieveAgent] Task too complex — requesting subdivision.")
            return {"status": "subdivide", "details": action_json}

        if action == "get_schemas":
            tables = action_json.get("tables", [])
            print(f"[DataRetrieveAgent] Fetching schemas for: {tables}")
            try:
                output = await _call_mcp_tool(server_params, "get_multiple_table_schemas", {"table_names": tables})
                messages.append({"role": "user", "content": f"Tool Result (get_schemas):\n{output}"})
            except Exception as e:
                if not any(p in str(e) for p in _PROCESS_ERRORS):
                    print(f"[DataRetrieveAgent] MCP error: {e}")
                    messages.append({"role": "user", "content": f"Error fetching schemas: {e}"})
            continue

        if action == "semantic_search":
            query_description = action_json.get("query_description", "").strip()
            if not query_description:
                messages.append({"role": "user", "content": "Provide a non-empty query_description for semantic_search."})
                continue

            arguments = {
                "query_description": query_description,
                "max_rows": action_json.get("max_rows", 10),
                "similarity_threshold": action_json.get("similarity_threshold", 50.0),
            }
            try:
                output = await _call_mcp_tool(semantic_server_params, "semantic_search_products", arguments)
            except Exception as e:
                if not any(p in str(e) for p in _PROCESS_ERRORS):
                    print(f"[DataRetrieveAgent] Semantic MCP error: {e}")
                    messages.append({"role": "user", "content": f"Error executing semantic search: {e}"})
                continue

            matched_product_ids = _extract_product_ids(output)
            semantic_search_performed = True
            messages.append({
                "role": "user",
                "content": (
                    f"Tool Result (semantic_search):\n{output}\n\n"
                    f"Matched product IDs: {json.dumps(matched_product_ids)}. "
                    "Use every matched ID in a product_id IN (...) predicate for subsequent SQL queries. "
                    "Do not run follow-up SQL if no product IDs were returned."
                ),
            })
            continue

        if action == "execute_query":
            query = action_json.get("query", "")
            if semantic_search_performed and (
                not matched_product_ids or not _query_includes_product_ids(query, matched_product_ids)
            ):
                messages.append({
                    "role": "user",
                    "content": (
                        "Do not execute this query yet. "
                        + (
                            "The semantic search returned no product IDs, so finish with the search result or report no matches."
                            if not matched_product_ids
                            else "Filter using product_id IN (...) and include every matched ID: "
                            f"{json.dumps(matched_product_ids)}. Return a corrected execute_query action."
                        )
                    ),
                })
                continue

            print(f"[DataRetrieveAgent] Executing query: {query[:50]}…")
            try:
                output = await _call_mcp_tool(server_params, "execute_sales_query", {"postgresql_query": query})
            except Exception as e:
                if not any(p in str(e) for p in _PROCESS_ERRORS):
                    print(f"[DataRetrieveAgent] MCP error: {e}")
                    messages.append({"role": "user", "content": f"Error executing query: {e}"})
                continue

            if len(output) > 20_000:
                truncated = (
                    output[:20_000]
                    + "\n\n…[TRUNCATED — result too large]\n"
                    "CRITICAL ERROR: Aggregate the data using SUM/COUNT/GROUP BY or self-joins. "
                    "Do NOT use STRING_AGG to dump raw rows."
                )
                messages.append({"role": "user", "content": f"Tool Result (execute_query):\n{truncated}"})
            else:
                print("[DataRetrieveAgent] ✓ SQL execution successful.")
                approved_queries.append(query)
                messages.append({
                    "role": "user",
                    "content": f"Tool Result (execute_query):\n{output}\n\nDATA FETCH SUCCESSFUL. You may now 'finish' if you have all required data.",
                })
            continue

        # Unknown action
        messages.append({
            "role": "user",
            "content": f"Unknown action '{action}'. Valid actions: get_schemas, semantic_search, execute_query, request_subdivision, finish.",
        })

    return {
        "status": "error",
        "data": f"Exceeded maximum iterations ({SQL_VALIDATION_MAX_ITERATION}) without finishing.",
        "queries": approved_queries,
    }


async def run_fetch_metadata(rls_user_id: str) -> str:
    """Fetch all table schemas and the RLS-filtered store list in a single MCP session."""
    server_params = StdioServerParameters(
        command="python",
        args=[_MCP_SCRIPT, "--stdio", f"--RLS_USER_ID={rls_user_id}"],
    )
    all_tables = [
        "retail.customers", "retail.stores", "retail.categories",
        "retail.product_types", "retail.products", "retail.orders",
        "retail.order_items", "retail.inventory",
    ]

    cm = stdio_client(server_params)
    read, write = await cm.__aenter__()
    try:
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("[DataRetrieveAgent] Fetching database schemas…")
            schema_result = await session.call_tool("get_multiple_table_schemas", arguments={"table_names": all_tables})
            schema_output = schema_result.content[0].text if schema_result.content else ""

            print("[DataRetrieveAgent] Fetching accessible stores (RLS filtered)…")
            store_result = await session.call_tool(
                "execute_sales_query",
                arguments={"postgresql_query": "SELECT store_id, store_name FROM retail.stores ORDER BY store_id;"},
            )
            store_output = store_result.content[0].text if store_result.content else "None"

        return (
            f"=== ACCESSIBLE STORES FOR THIS USER (RLS FILTERED) ===\n{store_output}\n\n"
            f"=== DATABASE SCHEMAS ===\n{schema_output}"
        )
    finally:
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                await write.aclose()
            except Exception:
                pass
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass


def fetch_data(task: str, rls_user_id: str) -> dict:
    """Synchronous wrapper around run_mcp_loop."""
    return asyncio.run(run_mcp_loop(task, rls_user_id))


def fetch_metadata(rls_user_id: str) -> str:
    """Synchronous wrapper around run_fetch_metadata."""
    return asyncio.run(run_fetch_metadata(rls_user_id))
