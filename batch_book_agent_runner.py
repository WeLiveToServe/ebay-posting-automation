"""
Batch runner for the bibliographic identification agent.

For each subdirectory under `batch-image-sets`, collect its JPG files,
invoke the agent defined in book-id-agent.yaml, and write the resulting
JSON to `batch-JSON-results/<directory-name>.json`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI

# Hardcoded paths for configuration, images, and output
CONFIG_PATH = Path("book-id-agent.yaml")
IMAGE_ROOT = Path("batch-image-sets")
OUTPUT_ROOT = Path("batch-JSON-results")


# Load and parse the agent configuration from the hardcoded YAML file
def load_agent_config() -> dict[str, Any]:
    import yaml

    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("Invalid agent configuration structure.")
    agent_data = data.get("agent", {})
    if not isinstance(agent_data, dict):
        raise ValueError("Agent configuration missing 'agent' block.")
    return agent_data


# Collect all JPG/JPEG image files from a directory
def collect_images(directory: Path) -> list[Path]:
    return sorted(path for path in directory.iterdir() if path.suffix.lower() in {".jpg", ".jpeg"})


# Encode a single image file as a base64 data URL
def encode_image(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        data = handle.read()
    import base64
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "type": "input_image",
        "image_url": f"data:image/jpeg;base64,{encoded}",
    }


# Build the input messages for the OpenAI API call
def build_input(agent_config: dict[str, Any], image_paths: list[Path]) -> list[dict[str, Any]]:
    system_prompt = agent_config.get("system_prompt", "")
    user_prompt = agent_config.get(
        "user_prompt",
        "Analyze the attached book images and produce bibliographic JSON adhering to the provided schema.",
    )
    inputs: list[dict[str, Any]] = []
    if system_prompt:
        inputs.append({"role": "system", "content": [{"type": "input_text", "text": system_prompt}]})

    content = [{"type": "input_text", "text": user_prompt}]
    for image_path in image_paths:
        content.append(encode_image(image_path))
    inputs.append({"role": "user", "content": content})
    return inputs


# Execute the agent by calling the OpenAI API with the configured model
def run_agent(agent_config: dict[str, Any], image_paths: list[Path]) -> str:
    model_config = agent_config.get("model", {})
    if not isinstance(model_config, dict):
        raise ValueError("Model configuration is missing or invalid.")
    model_name = model_config.get("type")
    if not model_name:
        raise ValueError("Model type is not specified in the YAML.")

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    inputs = build_input(agent_config, image_paths)
    response = client.responses.create(
        model=model_name,
        input=inputs,
        max_output_tokens=model_config.get("max_output_tokens"),
    )
    output_text = getattr(response, "output_text", None)
    return output_text if output_text else json.dumps(response.model_dump(), indent=2)


# Process a single directory: collect images, run agent, write JSON output
def process_directory(agent_config: dict[str, Any], directory: Path) -> None:
    image_paths = collect_images(directory)
    if not image_paths:
        print(f"Skipping {directory.name}: no JPG images found.")
        return
    output_text = run_agent(agent_config, image_paths)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_ROOT / f"{directory.name}.json"
    with output_path.open("w", encoding="utf-8") as handle:
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError:
            handle.write(json.dumps({"raw_text": output_text}, ensure_ascii=False, indent=2))
        else:
            json.dump(parsed, handle, ensure_ascii=False, indent=2)
    print(f"{output_path.name} complete")


# Main entry point: load config and process all image directories
def main() -> None:
    agent_config = load_agent_config()
    if not IMAGE_ROOT.exists():
        print(f"Image root '{IMAGE_ROOT}' does not exist.")
        return

    directories = sorted(path for path in IMAGE_ROOT.iterdir() if path.is_dir())
    if not directories:
        print(f"No subdirectories found under '{IMAGE_ROOT}'.")
        return

    for directory in directories:
        process_directory(agent_config, directory)


if __name__ == "__main__":
    main()