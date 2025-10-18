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
import webbrowser      # ADDED: for opening HTML file
import base64          # ADDED: for image encoding
from pathlib import Path
from typing import Any
from urllib.request import pathname2url # ADDED: for cross-platform file URI

from openai import OpenAI
import yaml # ADDED: import yaml for clarity

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
    """Execute the agent by calling the OpenAI API with the configured model (maintaining original working call)."""
    model_config = agent_config.get("model", {})
    if not isinstance(model_config, dict):
        raise ValueError("Model configuration is missing or invalid.")
    model_name = model_config.get("type")
    if not model_name:
        raise ValueError("Model type is not specified in the YAML.")

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    inputs = build_input(agent_config, image_paths)
    
    # CRITICAL: Using the non-standard but working API call signature
    response = client.responses.create(
        model=model_name,
        input=inputs,
        max_output_tokens=model_config.get("max_output_tokens"),
    )
    
    # Extracting the output text using the property that worked for your environment
    output_text = getattr(response, "output_text", None)
    return output_text if output_text else json.dumps(response.model_dump(), indent=2)

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
        data = {}
        if 'raw_text' in raw_data:
            json_string = raw_data['raw_text'].strip()
            # Clean up potential markdown code block markers
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
        webbrowser.open(f"file://{pathname2url(str(html_file_path.resolve()))}")

    except Exception as e:
        print(f"Error during HTML review process for {json_path.name}: {e}")

# Process a single directory: collect images, run agent, write JSON output to specified location
def process_directory(agent_config: dict[str, Any], directory: Path, output_root: Path, review_mode: bool) -> None:
    """
    Process a single directory: collect images, run agent, write JSON output, 
    and optionally open the HTML description for review.
    """
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
            # Try to parse the output text directly as JSON
            parsed = json.loads(output_text)
            json.dump(parsed, handle, ensure_ascii=False, indent=2)
            json_file_path = output_path # Use this path for review
        except json.JSONDecodeError:
            # If parsing fails, wrap the raw text in a JSON object for inspection
            json_content = json.dumps({"raw_text": output_text}, ensure_ascii=False, indent=2)
            handle.write(json_content)
            json_file_path = output_path # Use this path for review
            
    print(f"{output_path.name} complete")

    # ADDED: Call the new review function if the flag was set
    if review_mode:
        open_html_for_review(json_file_path)
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
    
    # ... (rest of the argument validation and directory loop)

    # Process each directory, passing the new review_mode flag
    for directory in directories:
        process_directory(agent_config, directory, output_root, args.review)

if __name__ == "__main__":
    main()