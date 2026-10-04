"""
LLM Client — thin wrapper around AzureOpenAI with exponential-backoff retry.
"""
import os
import time

from dotenv import load_dotenv
from openai import AzureOpenAI, RateLimitError

# Load credentials from the repository-root .env (not from the analytics_dashboard folder)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
load_dotenv(os.path.join(_REPO_ROOT, ".env"))


def _get_client() -> tuple[AzureOpenAI | None, str]:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_key = os.getenv("AZURE_OPENAI_KEY")
    deployment = os.getenv("GPT_MODEL_DEPLOYMENT_NAME", "gpt-4o-mini")

    if not endpoint or not api_key:
        print("WARNING: Azure OpenAI credentials not found in .env.")
        return None, deployment

    client = AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version="2024-02-15-preview",
    )
    return client, deployment


def call_llm(messages: list[dict], json_mode: bool = False, max_retries: int = 5) -> str:
    """
    Call the LLM with automatic exponential-backoff on rate-limit errors.
    Returns the raw content string from the model.
    """
    client, deployment = _get_client()
    if not client:
        return "{}" if json_mode else "MOCK_LLM_RESPONSE"

    kwargs = {
        "model": deployment,
        "messages": messages,
        "temperature": 0.1,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except RateLimitError as e:
            if attempt == max_retries - 1:
                print(f"[LLM Client] Max retries reached. Failing: {e}")
                raise
            wait = 2 ** (attempt + 1)  # 2s, 4s, 8s, 16s …
            print(f"[LLM Client] Rate limit hit. Retrying in {wait}s…")
            time.sleep(wait)
        except Exception as e:
            print(f"[LLM Client] Unexpected error: {e}")
            raise
