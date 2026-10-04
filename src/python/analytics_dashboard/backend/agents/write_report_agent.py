"""
Write Report Agent — formats and persists the final analytics report.

Responsibilities:
  1. Generate a short title and summary via LLM.
  2. Run a final security audit.
  3. Write report_meta.json and report.json to disk.
"""
import json
import os
import uuid
from datetime import datetime

from .llm_client import call_llm
from .security_agent import final_audit


def write_report_to_disk(
    base_dir: str,
    user_id: str,
    prompt: str,
    components: list,
    sql_queries: list | None = None,
) -> str:
    """
    Save the report to the local file system.
    Returns the new report_id (UUID string).
    """
    report_id = str(uuid.uuid4())
    report_dir = os.path.join(base_dir, user_id, report_id)
    os.makedirs(report_dir, exist_ok=True)

    # Generate title + summary
    resp = call_llm(
        [
            {
                "role": "system",
                "content": (
                    "You are a report metadata agent. Given the user's prompt, generate a short "
                    "'title' (max 6 words) and a concise 'summary' (1–2 sentences). "
                    "Return JSON: {'title': '...', 'summary': '...'}"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        json_mode=True,
    )
    try:
        meta = json.loads(resp)
    except Exception:
        meta = {}

    metadata = {
        "report_id": report_id,
        "user_id": user_id,
        "prompt": prompt,
        "title": meta.get("title", "Analytics Report"),
        "summary": meta.get("summary", prompt),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "executed_sql_queries": sql_queries or [],
    }

    with open(os.path.join(report_dir, "report_meta.json"), "w") as f:
        json.dump(metadata, f, indent=4)

    with open(os.path.join(report_dir, "report.json"), "w") as f:
        json.dump({**metadata, "components": components}, f, indent=4)

    return report_id


def finalize_and_save(
    prompt: str,
    user_id: str,
    components: list,
    base_dir: str,
    sql_queries: list | None = None,
) -> dict:
    """
    Run the security audit then persist the report.
    Returns {'report_id': str} or {'error': str}.
    """
    audit = final_audit(
        {"user_id": user_id, "prompt": prompt, "component_count": len(components)},
        prompt,
        user_id,
    )
    if not audit.get("passed", False):
        return {"error": f"Final security audit failed: {audit.get('reason')}"}

    report_id = write_report_to_disk(base_dir, user_id, prompt, components, sql_queries)
    return {"report_id": report_id}
