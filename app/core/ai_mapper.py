"""
AI-assisted field mapping using LLM APIs (OpenAI / Anthropic).

Given a source schema and a target schema (both extracted by schema_parser.py),
this module sends them to the configured LLM and receives back a proposed
field_mappings array that can be stored in the integration config's transform
section.

The user can review, edit, and approve the mappings in the visual mapping
editor UI before they are saved.

Phase 3 of the MotoGateway roadmap.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("motorad.core.ai_mapper")

# ---------------------------------------------------------------------------
# Provider abstraction
# ---------------------------------------------------------------------------

SUPPORTED_PROVIDERS = ("openai", "anthropic")
DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-20250514",
}


def get_ai_config(app_config: dict) -> dict:
    """Extract AI configuration from app config.

    Returns dict with keys: provider, api_key, model, enabled.
    """
    ai = app_config.get("ai", {})
    provider = ai.get("provider", "none")
    return {
        "provider": provider,
        "api_key": ai.get("api_key", ""),
        "model": ai.get("model", DEFAULT_MODELS.get(provider, "")),
        "enabled": provider in SUPPORTED_PROVIDERS and bool(ai.get("api_key")),
    }


# ---------------------------------------------------------------------------
# Prompt engineering
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert data integration engineer. Your task is to map fields from a source data schema to a target data schema.

Given:
1. A SOURCE schema — fields available in the incoming data
2. A TARGET schema — fields required in the output

You must produce a JSON array of field mappings. Each mapping is:
{
    "source_path": "path.to.source.field",
    "target_path": "path.to.target.field",
    "transform": null or a transform expression,
    "confidence": 0.0 to 1.0,
    "reasoning": "brief explanation"
}

Available transform expressions:
- null — direct copy, no transformation needed
- "upper" — convert to uppercase
- "lower" — convert to lowercase
- "trim" — strip whitespace
- "to_decimal" — format as 2-decimal number
- "date_format('INPUT_FMT', 'OUTPUT_FMT')" — reformat date (tokens: YYYY, MM, DD, HH, mm, ss)
- "lookup('table_name')" — lookup in a named table

Rules:
1. Map every target field if a reasonable source field exists
2. Use the sample values to understand data semantics, not just field names
3. Set confidence based on how certain you are about the mapping
4. If a source field name is in a different language, still try to map by data semantics
5. If no source field matches a target field, omit that mapping
6. For arrays, use [] notation: "lines[].quantity" → "items[].qty"
7. Return ONLY the JSON array — no markdown, no explanation outside the array"""


def _build_user_prompt(
    source_schema: List[dict],
    target_schema: List[dict],
    context: str = "",
) -> str:
    """Build the user prompt for field mapping."""
    lines = []
    lines.append("=== SOURCE SCHEMA ===")
    for field in source_schema:
        sample_str = f" (sample: {field.get('sample', '')})" if field.get("sample") else ""
        lines.append(f"  {field['path']}  [{field.get('type', 'string')}]{sample_str}")

    lines.append("")
    lines.append("=== TARGET SCHEMA ===")
    for field in target_schema:
        sample_str = f" (sample: {field.get('sample', '')})" if field.get("sample") else ""
        lines.append(f"  {field['path']}  [{field.get('type', 'string')}]{sample_str}")

    if context:
        lines.append("")
        lines.append(f"=== ADDITIONAL CONTEXT ===")
        lines.append(context)

    lines.append("")
    lines.append("Please produce the field mappings JSON array.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM API calls
# ---------------------------------------------------------------------------

def _call_openai(api_key: str, model: str, system: str, user: str) -> Tuple[str, Optional[str]]:
    """Call OpenAI Chat Completions API."""
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=4096,
        )
        return response.choices[0].message.content, None
    except ImportError:
        # Fall back to requests
        return _call_openai_http(api_key, model, system, user)
    except Exception as e:
        return "", f"OpenAI API error: {e}"


def _call_openai_http(api_key: str, model: str, system: str, user: str) -> Tuple[str, Optional[str]]:
    """Call OpenAI using plain HTTP (no SDK dependency)."""
    import requests
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.1,
                "max_tokens": 4096,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"], None
    except Exception as e:
        return "", f"OpenAI HTTP error: {e}"


def _call_anthropic(api_key: str, model: str, system: str, user: str) -> Tuple[str, Optional[str]]:
    """Call Anthropic Messages API."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=0.1,
        )
        return response.content[0].text, None
    except ImportError:
        return _call_anthropic_http(api_key, model, system, user)
    except Exception as e:
        return "", f"Anthropic API error: {e}"


def _call_anthropic_http(api_key: str, model: str, system: str, user: str) -> Tuple[str, Optional[str]]:
    """Call Anthropic using plain HTTP (no SDK dependency)."""
    import requests
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 4096,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "temperature": 0.1,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"], None
    except Exception as e:
        return "", f"Anthropic HTTP error: {e}"


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_mapping_response(raw_text: str) -> Tuple[List[dict], Optional[str]]:
    """Parse the LLM response into a list of field mappings.

    Handles JSON wrapped in markdown code blocks.
    """
    text = raw_text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (``` markers)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        mappings = json.loads(text)
        if not isinstance(mappings, list):
            return [], "LLM response is not a JSON array"

        # Validate and clean each mapping
        cleaned = []
        for m in mappings:
            if not isinstance(m, dict):
                continue
            if not m.get("source_path") or not m.get("target_path"):
                continue
            cleaned.append({
                "source_path": m["source_path"],
                "target_path": m["target_path"],
                "transform": m.get("transform"),
                "confidence": min(1.0, max(0.0, float(m.get("confidence", 0.5)))),
                "reasoning": m.get("reasoning", ""),
            })
        return cleaned, None

    except json.JSONDecodeError as e:
        return [], f"Failed to parse LLM response as JSON: {e}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest_mappings(
    source_schema: List[dict],
    target_schema: List[dict],
    app_config: dict,
    context: str = "",
) -> Tuple[List[dict], Optional[str]]:
    """Use AI to suggest field mappings between source and target schemas.

    Args:
        source_schema: List of source field descriptors from schema_parser.
        target_schema: List of target field descriptors from schema_parser.
        app_config: App config (contains AI provider settings).
        context: Optional additional context for the AI (e.g. "This is a
                 Priority ERP invoice export mapped to TECOM XML format").

    Returns:
        (mappings, error) — mappings is a list of field mapping dicts,
        error is None on success.
    """
    ai_cfg = get_ai_config(app_config)
    if not ai_cfg["enabled"]:
        return [], "AI is not configured. Set provider and API key in Settings."

    provider = ai_cfg["provider"]
    api_key = ai_cfg["api_key"]
    model = ai_cfg["model"]

    user_prompt = _build_user_prompt(source_schema, target_schema, context)

    logger.info("Requesting AI mapping suggestion from %s/%s (%d source fields, %d target fields)",
                provider, model, len(source_schema), len(target_schema))

    if provider == "openai":
        raw_response, err = _call_openai(api_key, model, SYSTEM_PROMPT, user_prompt)
    elif provider == "anthropic":
        raw_response, err = _call_anthropic(api_key, model, SYSTEM_PROMPT, user_prompt)
    else:
        return [], f"Unsupported AI provider: {provider}"

    if err:
        return [], err

    mappings, parse_err = _parse_mapping_response(raw_response)
    if parse_err:
        return [], parse_err

    logger.info("AI suggested %d field mappings", len(mappings))
    return mappings, None


def mappings_to_transform_config(mappings: List[dict]) -> dict:
    """Convert AI-suggested mappings into a transform config section.

    Strips AI metadata (confidence, reasoning) and produces a config
    ready to be stored in an integration.
    """
    field_mappings = []
    for m in mappings:
        fm = {
            "source_path": m["source_path"],
            "target_path": m["target_path"],
            "transform": m.get("transform"),
        }
        field_mappings.append(fm)

    return {
        "type": "mapping",
        "field_mappings": field_mappings,
        "lookup_tables": {},
        "constants": {},
        "defaults": {},
    }


def validate_mapping(mapping: dict, source_schema: List[dict], target_schema: List[dict]) -> List[str]:
    """Validate a single field mapping against known schemas.

    Returns a list of warning strings (empty = valid).
    """
    warnings = []
    source_paths = {f["path"] for f in source_schema}
    target_paths = {f["path"] for f in target_schema}

    src = mapping.get("source_path", "")
    tgt = mapping.get("target_path", "")

    if src and src not in source_paths:
        warnings.append(f"Source path '{src}' not found in source schema")
    if tgt and tgt not in target_paths:
        warnings.append(f"Target path '{tgt}' not found in target schema")

    return warnings
