"""
Analytics Agent — builds the dashboard from raw data and an approved outline.

Responsibilities:
  1. Generate one UI tile per outline section (text | chart | table).
  2. Validate each tile inline; retry once on failure.
  3. Parallelise tile generation (max 4 workers).
"""
import json
import concurrent.futures

from .llm_client import call_llm
from .config import MAX_REPORT_SECTIONS

_CHART_TYPE_GUIDE = """\
CHART TYPE SELECTION GUIDE — choose the most appropriate type for the data:

- "bar"          Comparing discrete categories (e.g. revenue per store, top products). Default for most comparisons.
- "horizontalBar" Long category labels (product/store names). Use type="bar" with indexAxis="y".
- "line"         Time-series or ordered sequential data (e.g. monthly revenue trend).
- "pie"          Part-of-whole with ≤6 categories (e.g. revenue share by category).
- "doughnut"     Like pie, preferred when a central label/value is useful.
- "scatter"      Two continuous numeric dimensions with no fixed category axis.
- "radar"        Multiple attributes across a few entities (e.g. 3 stores × 5 KPIs).

CRITICAL RULES:
1. NEVER emit null, undefined, or empty arrays in "data". Parse, extract, and transform raw data into real numbers.
2. If data is embedded in text (e.g. "Item A + Item B (45)"), extract the number (45). Aggregate, sum, or compute percentages as needed.
3. Always set "responsive": true and "maintainAspectRatio": false in options.
4. For bar charts with long labels set indexAxis: "y".
5. Always include title.display: true, title.text: "..." in plugins.
6. Use distinct, accessible colours:
   rgba(54,162,235,0.75), rgba(255,99,132,0.75), rgba(75,192,192,0.75),
   rgba(255,159,64,0.75), rgba(153,102,255,0.75), rgba(201,203,207,0.75)"""

_TILE_PROMPTS = {
    "text": (
        "You are a Text Tile Agent. Based on the section instructions and raw data, "
        "generate a concise, HTML-formatted analysis (using <p>, <strong>, <em>, <ul>, <li> etc.). "
        "Be specific — extract, compute, and include real numbers from the data.\n"
        "CONTEXT AWARENESS: Check the 'Accessible Stores' metadata. If the user only has access to a single store, tailor your analysis to a local store manager. If they have access to all stores, provide a Head Office / macro-level perspective.\n"
        'Return JSON: {"tiles": [{"type": "text", "content": "<p>your html here</p>"}]}'
    ),
    "chart": (
        "You are a UI Charting Expert and Chart.js specialist.\n"
        f"{_CHART_TYPE_GUIDE}\n"
        "Based on the section instructions and raw data, produce optimal Chart.js configurations. "
        "PARSE, EXTRACT, and TRANSFORM the raw text data into valid numeric arrays.\n"
        "CONTEXT AWARENESS: Check the 'Accessible Stores' metadata. If this is a Head Office inquiry (multiple stores), ensure your charts reflect a macro view or compare stores. If a single store, focus on internal trends.\n"
        "DATA CONSISTENCY: For time-series data, you MUST ensure x-axis labels use a SINGLE consistent time granularity (e.g. all months OR all quarters). NEVER mix '2020-01' and '2020-Q1' on the same axis.\n"
        "NORMALISATION & SPLITTING: If comparing items with vastly different scales or mixed granularities, normalise the data (e.g. convert to percentages) to make it meaningful, OR generate MULTIPLE charts (one for months, one for quarters) instead of putting all data on one graph.\n"
        'Return JSON: {"tiles": [{"type": "chart", "data": { <Chart.js config> }}]}'
    ),
    "table": (
        "You are a UI Table Expert. Based on the section instructions and raw data, "
        "extract and organise key metrics into a clean tabular structure.\n"
        'Return JSON: {"tiles": [{"type": "table", "data": {"headers": ["Col A", "Col B"], "rows": [["val", "val"]]}}]}'
    ),
}
_DEFAULT_TILE_PROMPT = 'You are a UI Expert. Generate a text summary. Return JSON: {"tiles": [{"type": "text", "content": "..."}]}'


def _validate_tile(section: dict, tile: dict) -> dict:
    """
    Fast inline validation of a generated tile.
    Returns {'valid': bool, 'feedback': str}.
    """
    tile_type = tile.get("type")
    sec_type = section.get("type", "text")

    if tile_type != sec_type:
        return {"valid": False, "feedback": f"Expected type '{sec_type}' but got '{tile_type}'."}

    if tile_type == "chart":
        datasets = tile.get("data", {}).get("data", {}).get("datasets", [])
        if not datasets:
            return {"valid": False, "feedback": "Chart has no datasets."}
        for ds in datasets:
            values = ds.get("data", [])
            if not values:
                return {"valid": False, "feedback": "Chart dataset 'data' array is empty."}
            null_count = sum(1 for v in values if v is None)
            if null_count == len(values):
                return {"valid": False, "feedback": "All chart values are null — extract real numbers from the raw data."}
            if null_count > 0:
                return {"valid": False, "feedback": f"{null_count} null value(s) in dataset — extract actual numbers."}

    elif tile_type == "text":
        content = tile.get("content", "")
        if not content or len(content.strip()) < 20:
            return {"valid": False, "feedback": "Text content is too short or empty."}

    elif tile_type == "table":
        data = tile.get("data", {})
        if not data.get("headers") or not data.get("rows"):
            return {"valid": False, "feedback": "Table is missing headers or rows."}

    return {"valid": True, "feedback": "OK"}


def generate_section_tile(section: dict, raw_data: str, db_metadata: str = "") -> list[dict]:
    """
    Generate one or more UI tiles for a section; validate inline and retry once on failure.
    """
    sec_type = section.get("type", "text")
    sys_msg = _TILE_PROMPTS.get(sec_type, _DEFAULT_TILE_PROMPT)
    user_msg = (
        f"Section Instructions:\n{json.dumps(section)}\n\n"
        f"User Store Access / Database Metadata (Use this to know if this is a single store or head office inquiry):\n{db_metadata}\n\n"
        f"Raw Data (parse, extract, compute, and transform — NEVER output null):\n{raw_data}"
    )

    last_tiles: list[dict] = []
    last_feedback: str = ""

    for attempt in range(2):
        messages = [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}]
        if attempt == 1 and last_tiles:
            messages += [
                {"role": "assistant", "content": json.dumps({"tiles": last_tiles})},
                {"role": "user", "content": f"VALIDATOR REJECTED: {last_feedback}\nPlease fix and return the corrected JSON."},
            ]

        try:
            resp_json = json.loads(call_llm(messages, json_mode=True))
            if "tiles" in resp_json:
                tiles = resp_json["tiles"]
            else:
                tiles = [resp_json]
        except Exception:
            tiles = [{"type": "text", "content": f"<p>Error generating {sec_type} tile.</p>"}]

        all_valid = True
        feedbacks = []
        for i, t in enumerate(tiles):
            # Hack to keep tiles from the same section sorted together
            t["id"] = section.get("id", 0) + (i * 0.01)
            val = _validate_tile(section, t)
            if not val["valid"]:
                all_valid = False
                feedbacks.append(f"Tile {i+1}: {val['feedback']}")

        if all_valid:
            label = "on retry" if attempt > 0 else "first attempt"
            print(f"[TileAgent] ✓ Section id={section.get('id')} passed ({label}) with {len(tiles)} tile(s).")
            return tiles

        last_feedback = " | ".join(feedbacks)
        print(f"[TileAgent] ❌ Section id={section.get('id')} rejected (attempt {attempt + 1}): {last_feedback}")
        last_tiles = tiles

    print(f"[TileAgent] ⚠ Section id={section.get('id')} returned after max retries.")
    return last_tiles


def build_dashboard(outline: dict, raw_data: str, db_metadata: str = "") -> list:
    """
    Generate all tiles in parallel (max 4 workers) and return them sorted by section ID.
    """
    sections = outline.get("sections", [])
    components: list[dict] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        future_to_sec = {executor.submit(generate_section_tile, sec, raw_data, db_metadata): sec for sec in sections}
        for future in concurrent.futures.as_completed(future_to_sec):
            try:
                # extend instead of append because generate_section_tile returns a list
                components.extend(future.result())
            except Exception as exc:
                sec = future_to_sec[future]
                print(f"[TileAgent] Exception for section id={sec.get('id')}: {exc}")

    components.sort(key=lambda x: x.get("id", 999))
    return components


def analyze_and_revise_outline(prompt: str, outline: dict, raw_data: str) -> dict:
    """
    Placeholder — outline revision is currently handled by the Orchestrator.
    Returns the outline unchanged.
    """
    return outline
