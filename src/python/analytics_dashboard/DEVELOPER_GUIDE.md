# Developer Guide — Agent Pipeline

A hands-on guide to how a report is generated and where to change things.
For the high-level design and diagrams, see [architecture.md](backend/agents/architecture.md).

---

## 1. Quick Start

```bash
 # run from the repository root
cp .env.example .env     # the ONLY .env used; fill in AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_KEY / GPT_MODEL_DEPLOYMENT_NAME / POSTGRES_URL
./src/python/analytics_dashboard/start.sh               # installs requirements, starts backend (:8000) and frontend (:5000)
```

Layout:

```
analytics_dashboard/
├── backend/api.py                 FastAPI app — calls the orchestrator
├── backend/agents/                ← everything in this guide
├── frontend/app.py                Flask UI
(MCP server lives in src/python/mcp_server/sales_analysis — shared, enforces Row-Level Security)
└── report_history/           Saved reports: <user_id>/<report_id>/report.json
```

---

## 2. End-to-End Workflow

Entry points live in [orchestrator_agent.py](backend/agents/orchestrator_agent.py), called by `backend/api.py`.

### Phase A — Gate: `check_clarity_only(messages, persona_name)`

| Step | Function | File | Outcome |
|------|----------|------|---------|
| 1 | `scan_initial_prompt` | `security_agent.py` | Rejects injection / privilege escalation |
| 2 | `assess_clarity` | `prompt_quality_agent.py` | Returns a clarification question if the prompt is vague |

Returns `{"status": "accepted"}` or `{"error": "..."}`.

### Phase B — Generation: `run_generation_pipeline(messages, persona_name, user_id, data_dir)`

| # | Step | Function | Notes |
|---|------|----------|-------|
| 0 | Metadata | `fetch_metadata` (`data_retrieve_agent.py`) | One MCP session: all table schemas + the RLS-filtered store list |
| 1 | Outline | `generate_report_outline` | LLM → up to `MAX_REPORT_SECTIONS` sections (`text`/`chart`/`table`). Accepted as-is, no retry loop |
| 2 | Requirements | `generate_data_requirements` | LLM → JSON of metrics, periods, comparisons, categories |
| 3 | Task planning | `plan_data_tasks` | Splits requirements into ≤ `_MAX_SWARM_TASKS` (4) independent tasks |
| 4 | Swarm | `_run_swarm` → `_process_task` | `asyncio.gather` of Data Retrieve agents; recursive subdivision |
| 5 | Tiles | `build_dashboard` (`analytics_agent.py`) | ThreadPool (4 workers), one or more tiles per section |
| 6 | Save | `finalize_and_save` (`write_report_agent.py`) | Final security audit → write `report.json` + `report_meta.json` |

### The Data Retrieve ReAct loop (`run_mcp_loop`)

Every LLM turn must return JSON with a `thought` key **before** `action`. The `thought` is the SQL pre-flight check (there is no separate validation agent).

| Action | What happens |
|--------|--------------|
| `get_schemas` | Calls MCP tool `get_multiple_table_schemas` |
| `execute_query` | Calls MCP tool `execute_sales_query`; results > 20,000 chars are truncated with an instruction to aggregate |
| `finish` | Returns `{"status": "success", "data", "queries"}` |
| `request_subdivision` | Only accepted from iteration 5 onward; returns `{"status": "subdivide", "details"}` |

If the loop uses all `SQL_VALIDATION_MAX_ITERATION` turns it returns `{"status": "error", ...}`.

**MCP lifecycle:** each tool call goes through `_call_mcp_tool`, which spawns a subprocess, runs one call, and tears it down (stderr silenced during teardown). Never hold a session open across loop iterations.

### Subdivision (`_process_task`)

`subdivide` → `_subdivide_task` (LLM splits into 2–3 sub-tasks) → recursive `_process_task` at `depth + 1`. At `_MAX_SUBDIVISION_DEPTH` (2) the task returns `"Task too complex — data unavailable."` and the report still gets written with that gap.

### Tile generation (`generate_section_tile`)

1. Pick prompt from `_TILE_PROMPTS[section["type"]]`.
2. LLM returns `{"tiles": [...]}` (a section may produce several tiles, e.g. split charts).
3. Each tile is checked by `_validate_tile` (type matches, no null/empty chart data, text not trivial, table has headers/rows).
4. On failure: **one** retry with the validator feedback appended.
5. Tile ids are `section_id + i * 0.01` so tiles from one section stay together when sorted.

`db_metadata` is passed in so the prompts can tell a single-store manager from head office.

---

## 3. Recipes — How to Change Things

### Change how many sections / tasks / iterations
- Sections: `MAX_REPORT_SECTIONS` in [config.py](backend/agents/config.py)
- ReAct iterations per agent: `SQL_VALIDATION_MAX_ITERATION` in [config.py](backend/agents/config.py)
- Swarm width and subdivision depth: `_MAX_SWARM_TASKS`, `_MAX_SUBDIVISION_DEPTH` at the top of [orchestrator_agent.py](backend/agents/orchestrator_agent.py)

### Change SQL behaviour / rules for the retrieval agent
Edit `_SYSTEM_PROMPT_TEMPLATE` in [data_retrieve_agent.py](backend/agents/data_retrieve_agent.py). Note that `{max_iter}` and `{context_block}` are `.format()` placeholders, and literal JSON braces must be doubled (`{{ }}`).

### Add a new action to the ReAct loop
1. Document it in `_SYSTEM_PROMPT_TEMPLATE` under "Valid actions".
2. Add an `if action == "your_action":` block in `run_mcp_loop`. End the block with `continue` (the early-continue pattern keeps the loop flat).
3. Update the "Unknown action" message listing valid actions.

### Add a new MCP tool
1. Implement the tool in `src/python/mcp_server/sales_analysis/sales_analysis.py`.
2. Call it from the agent via `_call_mcp_tool(server_params, "tool_name", {...})`.
3. Teach the agent about it in the system prompt.

### Add a new tile type (e.g. `kpi`)
1. Add a prompt to `_TILE_PROMPTS` in [analytics_agent.py](backend/agents/analytics_agent.py), returning `{"tiles": [{"type": "kpi", ...}]}`.
2. Add a branch in `_validate_tile`.
3. Allow `kpi` in the outline prompt (`generate_report_outline`, the `'type': 'text|chart|table'` string).
4. Render it in the frontend template that consumes `report.json` components.

### Improve chart quality
Edit `_CHART_TYPE_GUIDE` and the `"chart"` entry in `_TILE_PROMPTS`. Time-series consistency, normalisation and splitting rules live there.

### Change the pipeline order / add a stage
Edit `run_generation_pipeline` in [orchestrator_agent.py](backend/agents/orchestrator_agent.py). Keep stages as small helper functions and the pipeline body as a readable sequence of numbered steps.

### Add a new agent
1. Create `<name>_agent.py` with a module docstring, imports at the top, and a single public entry function.
2. Use `call_llm(messages, json_mode=True)` from `llm_client.py` and wrap `json.loads` in try/except with a safe fallback (see `_llm_json` in the orchestrator).
3. Call it only from the Orchestrator — agents do not call each other.
4. Add it to the file inventory and diagram in [architecture.md](backend/agents/architecture.md).

### Change what is saved to disk
`write_report_to_disk` in [write_report_agent.py](backend/agents/write_report_agent.py). Output: `<data_dir>/<user_id>/<report_id>/report.json` (metadata + `components`) and `report_meta.json` (metadata including `executed_sql_queries`).

---

## 4. Conventions

- Imports at top of file; no mid-function imports.
- Module docstring describes responsibilities; keep existing docstrings/comments you don't touch.
- Private helpers prefixed `_`; module-level constants for prompts.
- Prompts are the "logic" — change them deliberately and re-run a report to verify.
- Don't add `warnings.filterwarnings()` to hide the Pydantic `IncompleteFieldDefinitionWarning`; it is intentionally left visible.
- All LLM calls are stateless via `call_llm`; conversation history exists only inside `run_mcp_loop`.

---

## 5. Security Invariants (do not break)

- The Security Agent gates input before anything else runs, and audits again before writing.
- RLS: the user id is passed to the MCP server with `--RLS_USER_ID=...`; PostgreSQL enforces row visibility. Store metadata shown to the planner/retriever is limited to stores assigned to that user, and an explicitly requested inaccessible store is rejected before report queries start.
- If PostgreSQL returns an explicit permission-denied error, stop that retrieval task and the remaining investigation rounds; the chat displays the access message. Empty or single-store query results are **valid**, so never treat them as permission errors.
- Never build SQL from the user's raw text outside the LLM/MCP path.

---

## 6. Debugging Checklist

| Symptom | Where to look |
|---------|---------------|
| Report has "Task too complex" gaps | Log lines `Task too complex, requesting subdivision`; adjust the retrieval prompt or `_MAX_SUBDIVISION_DEPTH` |
| Chart mixes monthly and quarterly labels | Swarm tasks used different granularities; tighten `plan_data_tasks` or the chart prompt |
| Tile shows "Error generating … tile" | LLM JSON failure in `generate_section_tile`; check the `[TileAgent]` log lines |
| Agent exhausts iterations | Review SQL in logs; see `executed_sql_queries` in `report_meta.json` |
| `ProcessLookupError` / `Failed to kill process` tracebacks | MCP teardown noise; harmless, handled in `_call_mcp_tool` |
| Which SQL produced a report | `report_meta.json` → `executed_sql_queries` |

---

## 7. Known Limitations

- Swarm agents run in parallel without sharing a time granularity, so mixed month/quarter results are possible.
- Every swarm agent re-fetches schemas via MCP even though the orchestrator already has them (`fetch_metadata`).
- The Analytics Agent receives one pre-collected data blob; it does not ask the Data Retrieve Agent for follow-up data.
- `analyze_and_revise_outline` in `analytics_agent.py` is a placeholder (returns the outline unchanged).
- `VALIDATION_THRESHOLD` in `config.py` is unused internally.
