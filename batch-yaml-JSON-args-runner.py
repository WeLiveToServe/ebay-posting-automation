"""
Batch runner for the bibliographic identification agent.

For each subdirectory under a specified image directory, collect its JPG files,
invoke the agent defined in a YAML configuration file (specified via --config),
and write the resulting JSON to a specified output directory (specified via --output).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI

# Root directory for images (subdirectories here will be processed)
IMAGE_ROOT = Path("batch-image-sets")


# Load and parse the agent configuration from the specified YAML file
def load_agent_config(config_path: Path) -> dict[str, Any]:
    import yaml

    with config_path.open("r", encoding="utf-8") as handle:
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


# Process a single directory: collect images, run agent, write JSON output to specified location
def process_directory(agent_config: dict[str, Any], directory: Path, output_root: Path) -> None:
    image_paths = collect_images(directory)
    if not image_paths:
        print(f"Skipping {directory.name}: no JPG images found.")
        return
    output_text = run_agent(agent_config, image_paths)

    # Create the output directory if it doesn't exist
    output_root.mkdir(parents=True, exist_ok=True)
    
    # Build the output file path using the directory name
    output_path = output_root / f"{directory.name}.json"
    
    with output_path.open("w", encoding="utf-8") as handle:
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError:
            handle.write(json.dumps({"raw_text": output_text}, ensure_ascii=False, indent=2))
        else:
            json.dump(parsed, handle, ensure_ascii=False, indent=2)
    print(f"{output_path.name} complete")


# Main entry point: parse command-line arguments and process all image directories
def main() -> None:
    # Parse command-line arguments
    # Both YAML config path AND JSON output path are specified here via flags
    parser = argparse.ArgumentParser(
        description="Batch runner for book identification agent with configurable YAML and output paths"
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to YAML configuration file (e.g., agents/book-id-agent.yaml)"
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to directory where JSON results will be written (e.g., results/batch-01)"
    )
    args = parser.parse_args()
    
    # Convert the config argument to a Path object and validate it exists
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}")
        return
    if not config_path.is_file():
        print(f"Error: Config path is not a file: {config_path}")
        return
    
    # Convert the output argument to a Path object
    # Note: We don't check if it exists yet - we'll create it when needed
    output_root = Path(args.output)
    
    # Load the agent configuration from the specified YAML file
    agent_config = load_agent_config(config_path)
    
    # Verify image root directory exists
    if not IMAGE_ROOT.exists():
        print(f"Image root '{IMAGE_ROOT}' does not exist.")
        return

    # Find all subdirectories under the image root
    directories = sorted(path for path in IMAGE_ROOT.iterdir() if path.is_dir())
    if not directories:
        print(f"No subdirectories found under '{IMAGE_ROOT}'.")
        return

    # Process each directory, passing the output_root to each call
    for directory in directories:
        process_directory(agent_config, directory, output_root)


if __name__ == "__main__":
    main()