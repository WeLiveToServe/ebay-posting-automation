#still outputting json not text, codex adjusted process_directory module and removed "try" 
#nope, this was bass ackwards, gpt-4o needs responses, not chat completion
#last iteration called wrong endpoint, gpt-4o needs chat.completion api NOT responses API
#orig pasted partial code, this is all of it
#initial gemini update to get better html return format
"""
Batch runner for the bibliographic identification agent.

For each subdirectory under a specified image directory, collect its JPG files,
invoke the agent defined in a YAML configuration file (specified via --config),
and write the resulting JSON to a specified output directory (specified via --output).
Includes an optional --review flag to open the generated HTML description for quick inspection.
"""

from __future__ import annotations

import argparse
import json
import os
import webbrowser
from pathlib import Path
from typing import Any
from urllib.request import pathname2url

from openai import OpenAI

# Root directory for images (subdirectories here will be processed)
IMAGE_ROOT = Path("batch-image-sets")
URL_MANIFEST_NAME = "uploaded_urls.txt"


def load_agent_config(config_path: Path) -> dict[str, Any]:
    """Load and parse the agent configuration from the specified YAML file."""
    import yaml

    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("Invalid agent configuration structure.")
    agent_data = data.get("agent", {})
    if not isinstance(agent_data, dict):
        raise ValueError("Agent configuration missing 'agent' block.")
    return agent_data


def load_image_urls(directory: Path) -> list[str]:
    """Load pipe-delimited image URLs from the manifest written by the upload script."""
    manifest_path = directory / URL_MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError("no url text file found")
    raw = manifest_path.read_text(encoding="utf-8")
    urls = [part.strip() for part in raw.split("|") if part.strip()]
    if not urls:
        raise ValueError("no url text file found")
    return urls


def build_input(agent_config: dict[str, Any], image_urls: list[str]) -> list[dict[str, Any]]:
    """Build the input messages for the OpenAI API call."""
    system_prompt = agent_config.get("system_prompt", "")
    user_prompt = agent_config.get(
        "user_prompt",
        "Analyze the attached book images and produce bibliographic JSON adhering to the provided schema.",
    )
    inputs: list[dict[str, Any]] = []
    if system_prompt:
        inputs.append({"role": "system", "content": [{"type": "input_text", "text": system_prompt}]})

    content = [{"type": "input_text", "text": user_prompt}]
    for image_url in image_urls:
        content.append({"type": "input_image", "image_url": image_url})
    inputs.append({"role": "user", "content": content})
    return inputs


# Execute the agent by calling the OpenAI API with the configured model
def run_agent(agent_config: dict[str, Any], image_urls: list[str]) -> str:
    """Execute the agent by calling the OpenAI API with the configured model (maintaining original working call)."""
    model_config = agent_config.get("model", {})
    if not isinstance(model_config, dict):
        raise ValueError("Model configuration is missing or invalid.")
    model_name = model_config.get("type")
    if not model_name:
        raise ValueError("Model type is not specified in the YAML.")

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    inputs = build_input(agent_config, image_urls)
    
    # CRITICAL: Using the non-standard but working API call signature
    response = client.responses.create(
        model=model_name,
        input=inputs,
        max_output_tokens=model_config.get("max_output_tokens"),
    )

    # The Responses API does not expose ChatCompletion-style choices.
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    chunks: list[str] = []
    output_messages = getattr(response, "output", []) or []
    for message in output_messages:
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if not content:
            continue
        for item in content:
            item_type = getattr(item, "type", None)
            if item_type is None and isinstance(item, dict):
                item_type = item.get("type")
            if item_type != "output_text":
                continue
            text_part = getattr(item, "text", None)
            if text_part is None and isinstance(item, dict):
                text_part = item.get("text")
            if text_part:
                chunks.append(text_part)

    if chunks:
        return "".join(chunks)

    return json.dumps(response.model_dump(), indent=2)


def open_html_for_review(json_path: Path):
    """
    Parses the JSON output, extracts the description_html, writes it to a 
    temporary HTML file, and opens it in the default web browser for review.
    """
    try:
        # 1. Load the JSON file content
        with json_path.open('r', encoding='utf-8') as f:
            raw_data = json.load(f)

        # 2. Parse the output: Handle potential 'raw_text' wrapper 
        # (where the final JSON is a string inside raw_text)
        data = {}
        if 'raw_text' in raw_data:
            json_string = raw_data['raw_text'].strip()
            # Clean up potential markdown code block markers (```json\n...\n```)
            if json_string.startswith("```json"):
                json_string = json_string[7:].strip()
            if json_string.endswith("```"):
                json_string = json_string[:-3].strip()
            data = json.loads(json_string)
        else:
            data = raw_data
            
        # Extract the HTML description
        html_content = data.get('description_html')
        
        if not html_content:
            print(f"Warning: JSON file {json_path.name} is missing 'description_html'. Skipping HTML review.")
            return

        # 3. Define the path for the temporary HTML file
        html_file_path = json_path.parent / (json_path.stem + "_review.html")

        # 4. Create a basic HTML wrapper with the clean sans-serif style
        full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Review: {json_path.stem}</title>
    <style>
        /* Modern, clean sans-serif style for easy review */
        body {{ font-family: Arial, Helvetica, sans-serif; font-size: 14pt; line-height: 1.6; padding: 20px; }}
        h2 {{ font-weight: bold; color: #333; border-bottom: 2px solid #EEE; padding-bottom: 5px; }}
        h3 {{ color: #666; margin-top: 1.5em; }}
        ul {{ list-style-type: none; padding-left: 0; }}
        ul ul {{ list-style-type: square; padding-left: 20px; }}
        strong {{ color: #000; }}
    </style>
</head>
<body>
{html_content}
</body>
</html>"""

        html_file_path.write_text(full_html, encoding='utf-8')

        # 5. Open the file in the default web browser
        print(f"Opening HTML file for review: {html_file_path.name}")
        # Use file:// URI for cross-platform compatibility
        webbrowser.open(f"file://{pathname2url(str(html_file_path.resolve()))}")

    except Exception as e:
        print(f"Error during HTML review process for {json_path.name}: {e}")


def process_directory(agent_config: dict[str, Any], directory: Path, output_root: Path, review_mode: bool) -> None:
    """
    Process a single directory: collect images, run agent, write JSON output, 
    and optionally open the HTML description for review.
    """
    image_urls = load_image_urls(directory)
    output_text = run_agent(agent_config, image_urls)

    # Create the output directory if it doesn't exist
    output_root.mkdir(parents=True, exist_ok=True)
    
    # Build the output file path using the directory name
    output_path = output_root / f"{directory.name}.txt"

    with output_path.open("w", encoding="utf-8") as handle:
        handle.write(output_text)

    print(f"{output_path.name} complete")

    # Call the new review function if the flag was set
    if review_mode:
        open_html_for_review(output_path)


def main() -> None:
    """Main entry point: parse command-line arguments and process all image directories."""
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
    # ADDED NEW ARGUMENT FOR REVIEW MODE
    parser.add_argument(
        "--review",
        action="store_true",
        help="If set, automatically opens the generated description_html in a browser after JSON output."
    )
    args = parser.parse_args()
    
    config_path = Path(args.config)
    if not config_path.exists() or not config_path.is_file():
        print(f"Error: Config file not found: {config_path}")
        return
    
    output_root = Path(args.output)
    
    try:
        agent_config = load_agent_config(config_path)
    except Exception as e:
        print(f"Error loading agent configuration: {e}")
        return
    
    if not IMAGE_ROOT.exists():
        print(f"Image root '{IMAGE_ROOT}' does not exist.")
        return

    directories = sorted(path for path in IMAGE_ROOT.iterdir() if path.is_dir())
    if not directories:
        print(f"No subdirectories found under '{IMAGE_ROOT}'.")
        return

    # Process each directory, passing the new review_mode flag
    for directory in directories:
        try:
            process_directory(agent_config, directory, output_root, args.review)
        except (FileNotFoundError, ValueError):
            print("no url text file found")
            return


if __name__ == "__main__":
    main()
