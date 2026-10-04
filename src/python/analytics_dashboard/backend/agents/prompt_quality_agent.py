"""
Prompt Quality Agent — clarification gate for the agent pipeline.

Checks whether a user's prompt is specific and actionable enough to generate
a focused analytics report. Returns a clarification question when it is not.
"""
import json

from .llm_client import call_llm
from .config import MAX_REPORT_SECTIONS

_SYSTEM_PROMPT = (
    "You are a strict Prompt Quality Agent evaluating user requests for a retail analytics database. "
    "Your ONLY job is to determine if the prompt is actionable and suitable for generating a focused report "
    f"that consists of at most {MAX_REPORT_SECTIONS} sections (e.g. key summaries, metrics, charts, and tables). "
    "CRITICAL CHECKS:\n"
    "1. ACTIONABILITY: Does the prompt ask for data or analysis? "
    "('sales are bad' is not actionable; 'why are sales bad in Q3' is).\n"
    "2. SPECIFICITY: Does it provide enough context to query data? "
    "Some ambiguity is acceptable if a general analysis can still be run.\n"
    f"3. SCOPE: The request should fit within {MAX_REPORT_SECTIONS} dashboard sections.\n\n"
    "Score clarity/actionability 1–10. "
    "If ≥ 8, set 'clear' to true. "
    "Return JSON: {'clear': bool, 'score': int, 'clarification_question': str}"
)


def assess_clarity(messages: list[dict]) -> dict:
    """
    Check whether the conversation history contains a clear, actionable prompt.
    Returns {'clear': bool, 'score': int, 'clarification_question': str}.
    """
    formatted = [
        {"role": "assistant" if m["role"] == "Agent" else "user", "content": m["content"]}
        for m in messages
    ]
    llm_messages = [{"role": "system", "content": _SYSTEM_PROMPT}] + formatted

    try:
        return json.loads(call_llm(llm_messages, json_mode=True))
    except Exception:
        return {"clear": True, "clarification_question": ""}
