"""
Generate reviewable HTML files from agent outputs and open them in VS Code Live Preview.

For each folder specified (or all folders under batch-image-sets), this script:
  - Reads batch-JSON-results/<folder>.txt ("price ||| html ||| condition")
  - Extracts the HTML portion and writes reviews/<folder>.html
  - Optionally launches `code` to open the file (for Live Preview)
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Iterable, List, Tuple

RESULTS_DIR = Path("batch-JSON-results")
IMAGE_ROOT = Path("batch-image-sets")
REVIEW_DIR = Path("reviews")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract HTML snippets for manual review and open them in VS Code."
    )
    parser.add_argument(
        "--folders",
        nargs="*",
        help="Specific folder names to process (default: all directories under batch-image-sets).",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open each generated HTML file in VS Code after writing.",
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Skip folders without TXT outputs instead of raising an error.",
    )
    return parser.parse_args()


def discover_folders(selected: Iterable[str] | None) -> List[str]:
    if selected:
        return sorted(selected)
    return sorted(path.name for path in IMAGE_ROOT.iterdir() if path.is_dir())


def read_html_from_txt(folder: str) -> Tuple[str, Path]:
    txt_path = RESULTS_DIR / f"{folder}.txt"
    if not txt_path.exists():
        raise FileNotFoundError(f"Agent output not found: {txt_path}")
    raw = txt_path.read_text(encoding="utf-8").strip()
    parts = [segment.strip() for segment in raw.split(" ||| ")]
    if len(parts) != 3:
        raise ValueError(f"Unexpected format in {txt_path.name}")
    _, html, _ = parts
    return html, txt_path


def write_review_html(folder: str, html: str) -> Path:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    path = REVIEW_DIR / f"{folder}.html"
    path.write_text(html, encoding="utf-8")
    return path


def open_in_code(path: Path) -> None:
    commands = [["code", str(path)], ["code.cmd", str(path)]]
    last_error: Exception | None = None
    for command in commands:
        try:
            subprocess.Popen(command, shell=False)
            return
        except FileNotFoundError as exc:
            last_error = exc
            continue
    if last_error:
        raise FileNotFoundError(
            "VS Code command-line launcher not found. Ensure 'code' is on PATH."
        ) from last_error


def main() -> None:
    args = parse_args()
    folders = discover_folders(args.folders)

    if not folders:
        print("No folders found.")
        return

    for folder in folders:
        try:
            html, txt_path = read_html_from_txt(folder)
        except FileNotFoundError as exc:
            if args.skip_missing:
                print(f"Skipping {folder}: {exc}")
                continue
            raise
        except Exception as exc:
            print(f"Skipping {folder}: {exc}")
            continue

        review_path = write_review_html(folder, html)
        print(f"Wrote review file: {review_path}")
        if args.open:
            open_in_code(review_path)


if __name__ == "__main__":
    main()
