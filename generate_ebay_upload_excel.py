"""
Generate a minimal eBay upload workbook from agent outputs.

Reads a model output TXT file containing `price ||| html ||| condition` and the
corresponding `uploaded_urls.txt`, then writes a new Excel file named
`ebay-upl-MM-DD-HH-MM.xlsx` with the required headers and constants populated.
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from openpyxl import Workbook

RESULTS_DIR = Path("batch-JSON-results")
IMAGE_ROOT = Path("batch-image-sets")
URL_MANIFEST = "uploaded_urls.txt"

HEADERS: List[str] = [
    "*Action(SiteID=US|Country=US|Currency=USD|Version=1193)",
    "Category ID",
    "Category name",
    "Title",
    "Start price",
    "Quantity",
    "Item photo URL",
    "Condition ID",
    "Description",
    "Format",
    "Duration",
    "Location",
    "Shipping profile name",
    "Return profile name",
    "Payment profile name",
    "C:Author",
    "C:Book Title",
    "C:Language",
]

CONSTANTS: Dict[str, str] = {
    "*Action(SiteID=US|Country=US|Currency=USD|Version=1193)": "Add",
    "Category ID": "261186",
    "Category name": "/Books & Magazines/Books",
    "Quantity": "1",
    "Format": "FixedPrice",
    "Duration": "GTC",
    "Location": "Newfields, NH",
    "Shipping profile name": "USPS Media Mail",
    "Return profile name": "Returns allowed within 30 days",
    "Payment profile name": "Immediate payment managed via eBay",
    "C:Language": "English",
}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an eBay upload workbook from agent output and URL manifest."
    )
    parser.add_argument(
        "--folder",
        required=True,
        help="Folder name under batch-image-sets / batch-JSON-results (e.g., arden-book-01).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Directory where the Excel workbook will be written (default: current directory).",
    )
    return parser.parse_args()


def load_agent_output(path: Path) -> Tuple[str, str, str]:
    raw = path.read_text(encoding="utf-8").strip()
    parts = [segment.strip() for segment in raw.split(" ||| ")]
    if len(parts) != 3:
        raise ValueError(f"Unexpected agent output format in {path.name}")
    price, html, condition = parts
    return price, html, condition


def load_image_urls(manifest_path: Path) -> str:
    if not manifest_path.exists():
        raise FileNotFoundError(f"URL manifest not found: {manifest_path}")
    content = manifest_path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"URL manifest is empty: {manifest_path}")
    return content


def extract_metadata(description_html: str) -> Tuple[str, str]:
    def extract(field: str) -> str:
        pattern = rf"<li>\s*{re.escape(field)}:\s*(.*?)</li>"
        match = re.search(pattern, description_html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        value = re.sub(r"<.*?>", "", match.group(1)).strip()
        return value

    author = extract("Author")
    title = extract("Title")
    return author, title


def truncate(text: str, limit: int = 60) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def build_row(price: str, condition: str, description_html: str, image_urls: str) -> Dict[str, str]:
    author, book_title = extract_metadata(description_html)
    clean_title = truncate(book_title or "Untitled Listing")

    row: Dict[str, str] = {header: "" for header in HEADERS}
    row.update(CONSTANTS)
    row["Start price"] = price
    row["Condition ID"] = condition
    row["Description"] = description_html
    row["Item photo URL"] = image_urls
    row["C:Author"] = author or ""
    row["C:Book Title"] = book_title or ""
    row["Title"] = clean_title
    return row


def write_workbook(row: Dict[str, str], output_dir: Path) -> Path:
    timestamp = datetime.now()
    filename = f"ebay-upl-{timestamp:%m-%d-%H-%M}.xlsx"
    output_path = output_dir / filename

    wb = Workbook()
    ws = wb.active

    ws.append(HEADERS)
    ws.append([row.get(header, "") for header in HEADERS])

    wb.save(output_path)
    return output_path


def main() -> None:
    args = parse_arguments()
    folder = args.folder
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    txt_path = RESULTS_DIR / f"{folder}.txt"
    if not txt_path.exists():
        raise FileNotFoundError(f"Agent output not found: {txt_path}")

    manifest_path = IMAGE_ROOT / folder / URL_MANIFEST

    price, description_html, condition = load_agent_output(txt_path)
    image_urls = load_image_urls(manifest_path)
    row = build_row(price, condition, description_html, image_urls)
    workbook_path = write_workbook(row, output_dir)

    print(f"Wrote workbook: {workbook_path}")


if __name__ == "__main__":
    main()

