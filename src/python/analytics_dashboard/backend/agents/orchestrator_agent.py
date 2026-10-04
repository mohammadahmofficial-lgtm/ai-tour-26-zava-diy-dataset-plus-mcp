"""
Orchestrator Agent — central hub of the analytics pipeline.

Pipeline:
  0. Fetch DB metadata (schemas + RLS-visible stores).
  1. Generate a report outline from the metadata.
  2. Derive per-section data requirements.
  3. Run a parallel swarm of Data Retrieve Agents (with recursive subdivision).
  4. Build dashboard tiles from the aggregated data.
  5. Audit and write the final report to disk.
"""
import asyncio
import json
import concurrent.futures

from .config import MAX_REPORT_SECTIONS
from .data_retrieve_agent import fetch_metadata, run_mcp_loop
from .analytics_agent import build_dashboard
from .write_report_agent import finalize_and_save
from .security_agent import scan_initial_prompt
from .prompt_quality_agent import assess_clarity
from .llm_client import call_llm

_MAX_SUBDIVISION_DEPTH = 2
_MAX_SWARM_TASKS = 4


# ── Outline / planning helpers ─────────────────────────────────────────────────

def _llm_json(messages: list[dict], fallback) -> any:
    """Call LLM in JSON mode and return the parsed value, or fallback on error."""
    try:
        return json.loads(call_llm(messages, json_mode=True))
    except Exception:
        return fallback


def generate_report_outline(original_prompt: str, db_metadata: str, corrections: str = "") -> dict:
    """Return an outline dict with up to MAX_REPORT_SECTIONS sections."""
    messages = [
        {
            "role": "system",
            "content": (
                f"You are a Data Planning Agent. Convert the user's prompt into a JSON report outline "
                f"of up to {MAX_REPORT_SECTIONS} sections (do NOT exceed {MAX_REPORT_SECTIONS}). "
                f"Format: {{'sections': [{{'id': int, 'type': 'text|chart|table', 'description': '...'}}]}}. "
                "Base your scope ONLY on the actual available data in the DB Metadata."
            ),
        },
        {
            "role": "user",
            "content": (
                f"User Prompt: {original_prompt}\n\n"
                f"DATABASE METADATA:\n{db_metadata}\n\n"
                f"Corrections to previous outline:\n{corrections}"
            ),
        },
    ]
    data = _llm_json(messages, {"sections": []})
    sections = data.get("sections", [])[:MAX_REPORT_SECTIONS]
    return {"sections": sections}


def generate_data_requirements(original_prompt: str, outline: dict) -> str:
    """Return a JSON string describing exactly what data each section needs."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a Data Planning Agent. Based on the user prompt and report outline, output a JSON "
                "object detailing EXACTLY what data needs to be retrieved. "
                "Include 'required_metrics', 'time_periods', 'comparisons', and 'categories'."
            ),
        },
        {"role": "user", "content": f"User Prompt: {original_prompt}\n\nOutline:\n{json.dumps(outline)}"},
    ]
    return call_llm(messages, json_mode=True)


def plan_data_tasks(requirements_json: str) -> list[str]:
    """Break data requirements into up to _MAX_SWARM_TASKS parallel retrieval tasks."""
    messages = [
        {
            "role": "system",
            "content": (
                f"You are a Data Task Planner. Break the DATA REQUIREMENTS into up to {_MAX_SWARM_TASKS} independent "
                "retrieval tasks that SQL agents can run in parallel (e.g. one per year or per metric). "
                f"Return strict JSON: {{'tasks': ['Task 1', 'Task 2']}}. Maximum {_MAX_SWARM_TASKS} tasks."
            ),
        },
        {"role": "user", "content": f"Requirements:\n{requirements_json}"},
    ]
    result = _llm_json(messages, {"tasks": [requirements_json]})
    return result.get("tasks", [requirements_json])[:_MAX_SWARM_TASKS]


def _subdivide_task(task_desc: str, details: dict) -> list[str]:
    """Ask the LLM to split a failed complex task into 2–3 simpler sub-tasks."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are an Orchestrator. An agent failed a complex data retrieval task and requested subdivision.\n"
                f"Original Task: {task_desc}\n"
                f"Failed Query: {details.get('failed_query')}\n"
                f"Intent: {details.get('intent')}\n"
                f"Feedback: {details.get('validation_feedback')}\n\n"
                "Break this into 2–3 simpler SQL sub-tasks runnable in parallel. "
                "For complex aggregations (e.g. market basket), split by date range or store ID.\n"
                'Return JSON: {"tasks": ["sub-task 1", "sub-task 2"]}'
            ),
        },
    ]
    result = _llm_json([messages[0]], {"tasks": [task_desc]})
    return result.get("tasks", [task_desc])


# ── Async swarm ────────────────────────────────────────────────────────────────

async def _process_task(task_desc: str, rls_user_id: str, context: str, depth: int, agent_id: str) -> dict:
    """
    Run one retrieval task, recursively subdividing if necessary.
    Returns {'data': str, 'queries': list}.
    """
    prompt = f"You are Agent {agent_id} in a swarm. Your SOLE responsibility:\n{task_desc}"
    res = await run_mcp_loop(prompt, rls_user_id, context=context)

    if not isinstance(res, dict):
        return {"data": str(res), "queries": []}

    if res.get("status") == "success":
        return res

    if res.get("status") == "subdivide":
        if depth >= _MAX_SUBDIVISION_DEPTH:
            print(f"[Orchestrator] Task {agent_id}: max subdivision depth reached. Aborting.")
            return {"data": "Task too complex — data unavailable.", "queries": []}

        sub_tasks = _subdivide_task(task_desc, res.get("details", {}))
        print(f"[Orchestrator] Agent {agent_id} subdivided into {len(sub_tasks)} sub-tasks.")
        sub_results = await asyncio.gather(
            *[_process_task(st, rls_user_id, context, depth + 1, f"{agent_id}.{i + 1}") for i, st in enumerate(sub_tasks)],
            return_exceptions=True,
        )
        combined_data = f"--- ASSEMBLED RESULTS FOR TASK {agent_id} ---\n"
        combined_queries: list[str] = []
        for sr in sub_results:
            if isinstance(sr, Exception):
                combined_data += f"Sub-task failed: {sr}\n"
            elif isinstance(sr, dict):
                combined_data += f"{sr.get('data', '')}\n"
                combined_queries.extend(sr.get("queries", []))
        return {"data": combined_data, "queries": combined_queries}

    # status == "error" or unexpected
    return {"data": res.get("data", ""), "queries": res.get("queries", [])}


async def _run_swarm(tasks: list[str], rls_user_id: str, context: str) -> list:
    return await asyncio.gather(
        *[_process_task(t, rls_user_id, context, 0, str(i + 1)) for i, t in enumerate(tasks)],
        return_exceptions=True,
    )


# ── Public entry points ────────────────────────────────────────────────────────

def check_clarity_only(messages: list[dict], persona_name: str) -> dict:
    """
    Security scan + prompt clarity check. Returns {'status': 'accepted'} or {'error': str}.
    """
    latest = messages[-1]["content"] if messages else ""

    print("[Orchestrator] Requesting Security Scan…")
    sec = scan_initial_prompt(latest, persona_name)
    if not sec.get("passed", False):
        return {"error": f"Security violation: {sec.get('reason')}"}

    print("[Orchestrator] Checking prompt clarity…")
    clarity = assess_clarity(messages)
    if not clarity.get("clear", True):
        return {"error": clarity.get("clarification_question")}

    return {"status": "accepted", "message": "Prompt accepted! Starting outline generation and data retrieval…"}


def run_generation_pipeline(messages: list[dict], persona_name: str, user_id: str, data_dir: str) -> dict:
    """
    Full report generation pipeline. Returns {'report_id': str} or {'error': str}.
    """
    original_prompt = messages[0]["content"] if messages else ""

    # ── 0: DB Metadata ──
    print("[Orchestrator] Fetching DB metadata…")
    db_metadata = fetch_metadata(user_id)

    # ── 1: Outline ──
    print("[Orchestrator] Generating report outline…")
    outline = generate_report_outline(original_prompt, db_metadata)
    print("[Orchestrator] ✓ Outline ready.")

    # ── 2: Data requirements ──
    print("[Orchestrator] Extracting data requirements…")
    data_requirements = generate_data_requirements(original_prompt, outline)

    # ── 3: Parallel data retrieval swarm ──
    tasks = plan_data_tasks(data_requirements)
    print(f"[Orchestrator] Swarm: {len(tasks)} parallel tasks.")

    results = asyncio.run(_run_swarm(tasks, user_id, data_requirements))

    data_pieces: list[str] = []
    all_queries: list[str] = []
    for res in results:
        if isinstance(res, Exception):
            print(f"[Orchestrator] Swarm agent failed: {res}")
        elif isinstance(res, dict):
            data_pieces.append(f"--- SWARM AGENT RESULTS ---\n{res.get('data', '')}")
            all_queries.extend(res.get("queries", []))
        else:
            data_pieces.append(f"--- SWARM AGENT RESULTS ---\n{res}")

    raw_data = "\n\n".join(data_pieces)
    print(f"[Orchestrator] ✓ Swarm complete. {len(all_queries)} queries executed.")

    # ── 4: Dashboard tiles ──
    print("[Orchestrator] Building dashboard tiles…")
    ui_components = build_dashboard(outline, raw_data, db_metadata)
    print(f"[Orchestrator] ✓ {len(ui_components)} tiles built.")

    # ── 5: Write report ──
    print("[Orchestrator] Writing report…")
    return finalize_and_save(original_prompt, user_id, ui_components, data_dir, all_queries)
