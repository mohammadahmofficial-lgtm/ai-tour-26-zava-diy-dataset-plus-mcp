"""
Orchestrator Agent — central hub of the analytics pipeline.

Pipeline:
  0. Fetch DB metadata (schemas + RLS-visible stores).
  1. Generate a report outline from the metadata.
  2. Derive per-section data requirements.
  3. Run result-driven rounds of Data Retrieve Agents (with recursive subdivision).
  4. Build dashboard tiles from the aggregated data.
  5. Audit and write the final report to disk.
"""
import asyncio
import json

from .config import MAX_REPORT_SECTIONS
from .data_retrieve_agent import PERMISSION_DENIED_MESSAGE, fetch_metadata, run_mcp_loop
from .analytics_agent import build_dashboard
from .write_report_agent import finalize_and_save
from .security_agent import scan_initial_prompt
from .prompt_quality_agent import assess_clarity
from .llm_client import call_llm

_MAX_SUBDIVISION_DEPTH = 2
_MAX_SWARM_TASKS = 4
_MAX_INVESTIGATION_ROUNDS = 3


def _build_permission_denied_message(
    requested_inaccessible_stores: list[str],
    accessible_store_names: list[str],
) -> str:
    """
    Build a clear, user-facing message explaining which store(s) the user
    lacks access to, and which stores they CAN access.
    """
    if not accessible_store_names:
        message = (
            "Your account does not have access to any store data. "
            "Report generation was stopped before any queries were run."
        )
    elif requested_inaccessible_stores:
        store_list = ", ".join(f"'{store}'" for store in requested_inaccessible_stores)
        message = (
            f"You do not have access to {store_list}. "
            "Report generation was stopped before any queries were run."
        )
    else:
        message = (
            "Your request is outside the stores you can access. "
            "Report generation was stopped before any queries were run."
        )

    if accessible_store_names:
        accessible_list = ", ".join(f"'{store}'" for store in accessible_store_names)
        message += f" You can generate reports for: {accessible_list}."

    message += " Please choose a store you can access or contact your administrator."
    return message


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


def plan_data_tasks(
    original_prompt: str,
    requirements_json: str,
    previous_findings: str = "",
    completed_tasks: list[str] | None = None,
    accessible_stores: list[str] | None = None,
) -> dict:
    """Choose the next bounded set of retrieval tasks from current evidence."""
    completed_tasks = completed_tasks or []
    store_scope = (
        f"Authorized stores for this user: {', '.join(accessible_stores)}. "
        "Do not plan or request data for any other store."
        if accessible_stores is not None
        else ""
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are the adaptive investigation planner for a retail analytics agent. "
                "Plan an initial broad baseline investigation when no findings are available. "
                "For later rounds, use the returned findings to choose the most useful next analyses "
                "and address the largest remaining evidence gaps. Select only analyses supported by "
                "the database requirements and available evidence; do not claim causes that the data "
                "cannot establish. Tasks in the same round must be independent and may run in parallel. "
                "Do not repeat completed tasks. Return strict JSON in exactly this shape: "
                '{"investigation_complete": false, "tasks": ["concrete retrieval task"]}. '
                "The value of 'investigation_complete' must be a boolean and 'tasks' must always be "
                "an array of non-empty strings. Set investigation_complete=true only when the evidence "
                "is sufficient to answer the user, and then return an empty tasks array. Otherwise return "
                "at least one concrete retrieval task. "
                f"Return at most {_MAX_SWARM_TASKS} tasks."
            ),
        },
        {
            "role": "user",
            "content": (
                f"User request:\n{original_prompt}\n\n"
                f"Data requirements:\n{requirements_json}\n\n"
                f"Completed tasks:\n{json.dumps(completed_tasks)}\n\n"
                f"Findings from previous rounds:\n{previous_findings or '(none; plan the baseline investigation)'}\n\n"
                f"{store_scope}"
            ),
        },
    ]

    for attempt in range(2):
        response = call_llm(messages, json_mode=True)
        try:
            result = json.loads(response)
            if not isinstance(result, dict) or not isinstance(result.get("investigation_complete"), bool):
                raise ValueError("Adaptive investigation planner omitted a boolean investigation_complete value.")

            tasks = result.get("tasks")
            if not isinstance(tasks, list) or any(not isinstance(task, str) or not task.strip() for task in tasks):
                raise ValueError("Adaptive investigation planner must return a tasks array of non-empty strings.")
            if len(tasks) > _MAX_SWARM_TASKS:
                raise ValueError(f"Adaptive investigation planner returned more than {_MAX_SWARM_TASKS} tasks.")
            if result["investigation_complete"] and tasks:
                raise ValueError("Completed investigation plans must not contain more tasks.")
            if not result["investigation_complete"] and not tasks:
                raise ValueError("Incomplete investigation plans must include at least one task.")

            completed_task_keys = {task.strip().casefold() for task in completed_tasks}
            task_keys = [task.strip().casefold() for task in tasks]
            if len(task_keys) != len(set(task_keys)) or any(key in completed_task_keys for key in task_keys):
                raise ValueError("Adaptive investigation planner returned a duplicate task.")

            return {"investigation_complete": result["investigation_complete"], "tasks": tasks}
        except json.JSONDecodeError as exc:
            if attempt == 1:
                raise ValueError("Adaptive investigation planner returned invalid JSON.") from exc
            validation_error = ValueError("Adaptive investigation planner returned invalid JSON.")
        except ValueError as exc:
            validation_error = exc

        if attempt == 1:
            raise validation_error

        messages.extend(
            [
                {"role": "assistant", "content": response},
                {
                    "role": "user",
                    "content": (
                        f"The previous response failed validation: {validation_error}. "
                        "Return a corrected JSON object with a boolean 'investigation_complete' and "
                        "'tasks' as an array of non-empty strings. If investigation_complete is true, "
                        "tasks must be empty; otherwise tasks must contain at least one concrete task. "
                        f"Return no more than {_MAX_SWARM_TASKS} tasks."
                    ),
                },
            ]
        )


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

    if res.get("status") in ("success", "permission_denied"):
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
        permission_denied = False
        for sr in sub_results:
            if isinstance(sr, Exception):
                combined_data += f"Sub-task failed: {sr}\n"
            elif isinstance(sr, dict):
                combined_data += f"{sr.get('data', '')}\n"
                combined_queries.extend(sr.get("queries", []))
                permission_denied = permission_denied or sr.get("status") == "permission_denied"
        return {
            "status": "permission_denied" if permission_denied else "success",
            "data": PERMISSION_DENIED_MESSAGE if permission_denied else combined_data,
            "queries": combined_queries,
        }

    # status == "error" or unexpected
    return {"data": res.get("data", ""), "queries": res.get("queries", [])}


async def _run_swarm(tasks: list[str], rls_user_id: str, context: str) -> list:
    return await asyncio.gather(
        *[_process_task(t, rls_user_id, context, 0, str(i + 1)) for i, t in enumerate(tasks)],
        return_exceptions=True,
    )


async def _run_adaptive_investigation(
    original_prompt: str,
    requirements_json: str,
    rls_user_id: str,
    accessible_stores: list[str] | None = None,
) -> dict:
    """Collect evidence in bounded rounds, choosing each follow-up after prior results."""
    findings: list[str] = []
    completed_tasks: list[str] = []
    all_queries: list[str] = []

    for round_number in range(1, _MAX_INVESTIGATION_ROUNDS + 1):
        planner_options = {}
        if accessible_stores is not None:
            planner_options["accessible_stores"] = accessible_stores
        plan = plan_data_tasks(
            original_prompt,
            requirements_json,
            previous_findings="\n\n".join(findings),
            completed_tasks=completed_tasks,
            **planner_options,
        )
        if plan["investigation_complete"]:
            print(f"[Orchestrator] Investigation planner found evidence sufficient after {round_number - 1} round(s).")
            break

        tasks = plan["tasks"]
        print(
            f"[Orchestrator] Investigation round {round_number}/{_MAX_INVESTIGATION_ROUNDS}: "
            f"{len(tasks)} parallel task(s)."
        )
        task_context = requirements_json
        if accessible_stores is not None:
            task_context += (
                "\n\nAUTHORIZED STORES FOR THIS USER: "
                f"{', '.join(accessible_stores) or '(none)'}. "
                "Do not query other stores."
            )
        results = await _run_swarm(tasks, rls_user_id, task_context)

        permission_denied = False
        for task, result in zip(tasks, results):
            completed_tasks.append(task)
            if isinstance(result, Exception):
                findings.append(f"Task: {task}\nResult: Retrieval failed: {result}")
            elif isinstance(result, dict):
                findings.append(f"Task: {task}\nResult:\n{result.get('data', '')}")
                all_queries.extend(result.get("queries", []))
                permission_denied = permission_denied or result.get("status") == "permission_denied"
            else:
                findings.append(f"Task: {task}\nResult:\n{result}")

        if permission_denied:
            return {
                "error": PERMISSION_DENIED_MESSAGE,
                "data": "\n\n".join(findings),
                "queries": all_queries,
            }

    else:
        findings.append(
            f"Investigation stopped after reaching the configured limit of "
            f"{_MAX_INVESTIGATION_ROUNDS} rounds; conclusions must use only evidence collected."
        )

    return {"data": "\n\n".join(findings), "queries": all_queries}


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
    try:
        db_metadata, all_store_names, accessible_store_names = fetch_metadata(user_id)
    except Exception as exc:
        print(f"[Orchestrator] Unable to verify store permissions: {exc}")
        return {
            "error": (
                "We could not verify your store permissions, so no report queries were run. "
                "Please try again or contact your administrator."
            )
        }

    accessible_store_set = {store.casefold() for store in accessible_store_names}
    prompt_lower = original_prompt.casefold()

    def _store_referenced(store: str) -> bool:
        """Check whether the user's prompt references a store, matching both the
        full name ('Zava Retail Seattle') and the short name ('Seattle')."""
        store_lower = store.casefold()
        if store_lower in prompt_lower:
            return True
        # Match the short store name (e.g. 'Seattle' from 'Zava Retail Seattle')
        short_name = store_lower.removeprefix("zava retail ").strip()
        return bool(short_name) and short_name in prompt_lower

    requested_inaccessible_stores = [
        store
        for store in all_store_names
        if store.casefold() not in accessible_store_set and _store_referenced(store)
    ]
    if (all_store_names and not accessible_store_names) or requested_inaccessible_stores:
        print("[Orchestrator] Requested data is outside the user's store permissions; stopping before report queries.")
        return {"error": _build_permission_denied_message(requested_inaccessible_stores, accessible_store_names)}

    # ── 1: Outline ──
    print("[Orchestrator] Generating report outline…")
    outline = generate_report_outline(original_prompt, db_metadata)
    print("[Orchestrator] ✓ Outline ready.")

    # ── 2: Data requirements ──
    print("[Orchestrator] Extracting data requirements…")
    data_requirements = generate_data_requirements(original_prompt, outline)

    # ── 3: Result-driven data investigation ──
    try:
        investigation = asyncio.run(
            _run_adaptive_investigation(
                original_prompt,
                data_requirements,
                user_id,
                accessible_stores=accessible_store_names,
            )
        )
    except Exception as exc:
        print(f"[Orchestrator] Adaptive investigation failed: {exc}")
        return {"error": f"Adaptive investigation failed: {exc}"}

    raw_data = investigation["data"]
    all_queries = investigation["queries"]
    if "error" in investigation:
        return {"error": investigation["error"]}
    print(f"[Orchestrator] ✓ Investigation complete. {len(all_queries)} queries executed.")

    # ── 4: Dashboard tiles ──
    print("[Orchestrator] Building dashboard tiles…")
    ui_components = build_dashboard(outline, raw_data, db_metadata)
    print(f"[Orchestrator] ✓ {len(ui_components)} tiles built.")

    # ── 5: Write report ──
    print("[Orchestrator] Writing report…")
    return finalize_and_save(original_prompt, user_id, ui_components, data_dir, all_queries)
