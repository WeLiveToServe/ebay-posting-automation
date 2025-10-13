# test-open-yaml-agent-builder.py
import base64
import csv
import io
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any
from datetime import datetime
import json

import yaml
from openai import OpenAI, OpenAIError

# CHANGE THE NAME OF THE YAML HERE TO CUSTOMIZE TO SPECIFIC YAML
# THEN RENAME THE PY FILE AND YAML AS "AGENT SET" to keep that agent setup
CONFIG_PATH = Path(__file__).with_name("book-id-agent.yaml")
OUTPUT_DIR = CONFIG_PATH.parent / "outputs-JSON"

CHAT_ALLOWED_FIELDS = {
    "temperature",
    "top_p",
    "max_tokens",
    "presence_penalty",
    "frequency_penalty",
    "stop",
    "n",
    "stream",
    "logit_bias",
    "response_format",
    "seed",
    "tools",
    "tool_choice",
    "user",
}

RESPONSES_ALLOWED_FIELDS = {
    "max_output_tokens",
    "reasoning",
    "metadata",
    "parallel_tool_calls",
    "previous_response_id",
    "response_format",
    "tool_choice",
    "tools",
    "user",
}


def load_agent_config() -> dict[str, Any]:
    """Load agent configuration from YAML, returning an empty mapping on failure."""
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        return {}

    if not isinstance(data, dict):
        return {}

    agent_data = data.get("agent", {})
    return agent_data if isinstance(agent_data, dict) else {}


def collect_image_paths(image_dir: str | None) -> list[Path]:
    """Gather sorted image paths from the configured directory."""
    if not image_dir:
        return []

    base_dir = (CONFIG_PATH.parent / image_dir).resolve()
    if not base_dir.is_dir():
        return []

    return sorted(path for path in base_dir.iterdir() if path.is_file())


def encode_image(path: Path) -> str | None:
    """Return a base64 data URI for the image, or None on failure."""
    mime_type, _ = mimetypes.guess_type(path)
    if not mime_type:
        return None

    try:
        data = path.read_bytes()
    except OSError:
        return None

    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def ensure_output_dir() -> None:
    """Create the output directory if it does not already exist."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def save_json_output(content: Any) -> None:
    """Persist model output to the outputs-JSON directory."""
    ensure_output_dir()
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
    output_path = OUTPUT_DIR / f"response_{timestamp}.json"

    payload = content
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            payload = {"raw_text": content}

    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    print(f"Saved response to {output_path}")


def sanitize_value(value: Any) -> str:
    """Convert the provided value to a trimmed string, preserving internal newlines."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    return text.strip()


class SafeDict(dict):
    """Dictionary returning empty strings for missing keys when formatting."""

    def __missing__(self, key: str) -> str:
        return ""


def parse_structured_output(output_text: str) -> dict[str, Any] | None:
    """Attempt to load the model output as a dictionary."""
    if not output_text:
        return None

    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def build_csv_output(data: dict[str, Any], csv_config: dict[str, Any]) -> str | None:
    """Generate a CSV string using configured columns, defaults, and field mappings."""
    columns = csv_config.get("columns")
    if not isinstance(columns, list) or not columns:
        return None

    defaults = csv_config.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}

    field_map = csv_config.get("field_map")
    if not isinstance(field_map, dict):
        field_map = {}

    safe_data = SafeDict({key: sanitize_value(value) for key, value in data.items()})
    row: list[str] = []
    for column in columns:
        value: str | None = None
        mapping = field_map.get(column)
        if isinstance(mapping, str):
            value = mapping.format_map(safe_data)
        elif isinstance(mapping, list):
            parts = [
                sanitize_value(data.get(name))
                for name in mapping
                if sanitize_value(data.get(name))
            ]
            value = " ".join(parts)

        if value is None:
            value = sanitize_value(data.get(column))

        if not value:
            value = sanitize_value(defaults.get(column))

        row.append(value)

    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")

    if csv_config.get("include_header"):
        writer.writerow(columns)

    writer.writerow(row)
    return buffer.getvalue()


def maybe_print_csv_output(agent_output: str, agent_config: dict[str, Any]) -> None:
    """Print an eBay-ready CSV snippet when configuration and data allow."""
    csv_config = agent_config.get("csv_output")
    if not isinstance(csv_config, dict) or not csv_config.get("enabled"):
        return

    parsed = parse_structured_output(agent_output)
    if parsed is None:
        print("CSV output skipped: model response was not valid JSON.", file=sys.stderr)
        return

    csv_text = build_csv_output(parsed, csv_config)
    if not csv_text:
        print("CSV output skipped: configuration incomplete or missing columns.", file=sys.stderr)
        return

    print("\n--- eBay CSV snippet ---")
    print(csv_text.rstrip("\n"))
    print("--- end eBay CSV snippet ---\n")


def drop_nulls(value: Any) -> Any:
    """Recursively strip None values from dictionaries and lists."""
    if value is None:
        return None

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            cleaned = drop_nulls(item)
            if cleaned is not None:
                result[key] = cleaned
        return result or None

    if isinstance(value, list):
        result_list: list[Any] = []
        for item in value:
            cleaned = drop_nulls(item)
            if cleaned is not None:
                result_list.append(cleaned)
        return result_list or None

    return value


def build_chat_messages(system_prompt: str, user_instruction: str, image_paths: list[Path]) -> list[dict[str, Any]]:
    """Create Chat Completions-style messages with text and image parts."""
    messages: list[dict[str, Any]] = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    user_content: list[dict[str, Any]] = []
    if user_instruction:
        user_content.append({"type": "text", "text": user_instruction})

    for image_path in image_paths:
        data_uri = encode_image(image_path)
        if not data_uri:
            print(f"Skipping unreadable image: {image_path}", file=sys.stderr)
            continue

        user_content.append({"type": "image_url", "image_url": {"url": data_uri}})

    if user_content:
        messages.append({"role": "user", "content": user_content})

    return messages


def build_response_inputs(system_prompt: str, user_instruction: str, image_paths: list[Path]) -> list[dict[str, Any]]:
    """Create Responses API input blocks compatible with multimodal models."""
    inputs: list[dict[str, Any]] = []

    if system_prompt:
        inputs.append(
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            }
        )

    user_content: list[dict[str, Any]] = []
    if user_instruction:
        user_content.append({"type": "input_text", "text": user_instruction})

    for image_path in image_paths:
        data_uri = encode_image(image_path)
        if not data_uri:
            print(f"Skipping unreadable image: {image_path}", file=sys.stderr)
            continue

        user_content.append({"type": "input_image", "image_url": data_uri})

    if user_content:
        inputs.append({"role": "user", "content": user_content})

    return inputs


def extract_fields(config: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    """Return only the allowed keys from the model config."""
    return {key: config.get(key) for key in allowed if key in config}


def build_response_format(agent_config: dict[str, Any]) -> dict[str, Any] | None:
    """Construct a json_schema response_format block when an output schema is present."""
    schema = agent_config.get("output_schema")
    if not schema:
        return None

    schema_name = agent_config.get("name", "agent_output").replace(" ", "_")
    return {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "schema": schema},
    }


def main() -> None:
    agent_config = load_agent_config()
    if not agent_config:
        print("Agent configuration not found or invalid.", file=sys.stderr)
        return

    model_config = agent_config.get("model", {})
    if not isinstance(model_config, dict):
        print("Model configuration is missing or malformed.", file=sys.stderr)
        return

    model_name = model_config.get("type")
    if not model_name:
        print("Model type is not specified in the YAML.", file=sys.stderr)
        return

    image_dir = agent_config.get("image_dir")
    image_paths = collect_image_paths(image_dir)
    if not image_paths:
        print("No images found to analyze. Check the 'image_dir' setting.")
        return

    user_instruction = agent_config.get(
        "user_prompt",
        "Analyze the attached images and return the requested bibliographic JSON.",
    )
    system_prompt = agent_config.get("system_prompt", "")
    response_format = build_response_format(agent_config)

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    is_responses_model = model_name.lower().startswith("o")

    try:
        if is_responses_model:
            response_inputs = build_response_inputs(system_prompt, user_instruction, image_paths)
            if not response_inputs:
                print("No content generated for Responses API request.", file=sys.stderr)
                return

            params = {
                "model": model_name,
                "input": response_inputs,
            }
            params.update(extract_fields(model_config, RESPONSES_ALLOWED_FIELDS))

            if response_format:
                ## response_format currently not supported on Responses API; left for future handling
                pass

            cleaned_params = drop_nulls(params)
            response = client.responses.create(**(cleaned_params or {}))
            output_text = getattr(response, "output_text", None)
            final_output = output_text if output_text else str(response)
            print(final_output)
            save_json_output(final_output)
            maybe_print_csv_output(final_output, agent_config)
        else:
            messages = build_chat_messages(system_prompt, user_instruction, image_paths)
            if not messages:
                print("No messages built for Chat Completions request.", file=sys.stderr)
                return

            params = {
                "model": model_name,
                "messages": messages,
            }
            params.update(extract_fields(model_config, CHAT_ALLOWED_FIELDS))

            if response_format:
                params["response_format"] = response_format

            cleaned_params = drop_nulls(params)
            response = client.chat.completions.create(**(cleaned_params or {}))
            reply_content = response.choices[0].message.content
            if isinstance(reply_content, list):
                text_segments = [
                    segment.get("text", "")
                    for segment in reply_content
                    if isinstance(segment, dict) and segment.get("type") == "output_text"
                ]
                reply_text = "\n".join(part for part in text_segments if part)
            else:
                reply_text = reply_content

            final_output = reply_text if reply_text else "No content returned from model."
            print(final_output)
            save_json_output(final_output)
            maybe_print_csv_output(final_output, agent_config)
    except OpenAIError as exc:
        print(f"OpenAI API error: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
