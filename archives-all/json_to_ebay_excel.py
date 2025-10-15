"""
Create an eBay File Exchange workbook from the most recent agent JSON output.

Runs with zero arguments by default. It looks in ./outputs-JSON for JSON files,
grabs the newest one, and produces ebay-auto-listings.xlsx containing the header
row plus a single listing row derived from the JSON.
"""

from __future__ import annotations

import argparse
import json
import warnings
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.workbook import Workbook

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

DEFAULT_JSON_DIR = Path("outputs-JSON")
DEFAULT_OUTPUT = Path("ebay-auto-listings.xlsx")
HEADERS = [
    "*Action(SiteID=US|Country=US|Currency=USD|Version=1193)",
    "Custom label (SKU)",
    "Category ID",
    "Category name",
    "Title",
    "Relationship",
    "Relationship details",
    "Schedule Time",
    "P:ISBN",
    "P:EPID",
    "Start price",
    "Quantity",
    "Item photo URL",
    "VideoID",
    "Condition ID",
    "Description",
    "Format",
    "Duration",
    "Buy It Now price",
    "Best Offer Enabled",
    "Best Offer Auto Accept Price",
    "Minimum Best Offer Price",
    "Immediate pay required",
    "Location",
    "Shipping service 1 option",
    "Shipping service 1 cost",
    "Shipping service 1 priority",
    "Shipping service 2 option",
    "Shipping service 2 cost",
    "Shipping service 2 priority",
    "Max dispatch time",
    "Returns accepted option",
    "Returns within option",
    "Refund option",
    "Return shipping cost paid by",
    "Shipping profile name",
    "Return profile name",
    "Payment profile name",
    "C:Author",
    "C:Book Title",
    "C:Language",
    "C:Topic",
    "C:Publisher",
    "C:Format",
    "C:Genre",
    "C:Book Series",
    "C:Publication Year",
    "C:Original Language",
    "C:Features",
    "C:Type",
    "C:Country/Region of Manufacture",
    "C:Edition",
    "C:Narrative Type",
    "C:Signed",
    "C:Intended Audience",
    "C:Binding",
    "C:Subject",
    "C:Special Attributes",
    "Product Safety Pictograms",
    "Product Safety Statements",
    "Product Safety Component",
    "Regulatory Document Ids",
    "Manufacturer Name",
    "Manufacturer AddressLine1",
    "Manufacturer AddressLine2",
    "Manufacturer City",
    "Manufacturer Country",
    "Manufacturer PostalCode",
    "Manufacturer StateOrProvince",
    "Manufacturer Phone",
    "Manufacturer Email",
    "Manufacturer ContactURL",
    "Responsible Person 1",
    "Responsible Person 1 Type",
    "Responsible Person 1 AddressLine1",
    "Responsible Person 1 AddressLine2",
    "Responsible Person 1 City",
    "Responsible Person 1 Country",
    "Responsible Person 1 PostalCode",
    "Responsible Person 1 StateOrProvince",
    "Responsible Person 1 Phone",
    "Responsible Person 1 Email",
    "Responsible Person 1 ContactURL",
]

DEFAULT_VALUES = {
    "*Action(SiteID=US|Country=US|Currency=USD|Version=1193)": "Add",
    "Category ID": "261186",
    "Category name": "/Books & Magazines/Books",
    "Quantity": 1,
    "Start price": "5.00",
    "Item photo URL": "https://keith-ebay-images.s3.us-east-2.amazonaws.com/IMG_4929.JPG",
    "Condition ID": "5000-Good",
    "Format": "FixedPrice",
    "Duration": "GTC",
    "Max dispatch time": "3",
    "Returns accepted option": "ReturnsAccepted",
    "Returns within option": "Days_30",
    "Refund option": "MoneyBack",
    "Return shipping cost paid by": "Buyer",
    "Location": "Newfields, NH",
    "Shipping profile name": "USPS Media Mail",
    "Return profile name": "Returns allowed within 30 days",
    "Payment profile name": "Immediate payment managed via eBay",
    "C:Language": "English",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        type=Path,
        help="Specific JSON file (defaults to newest in outputs-JSON).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Destination workbook path (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument("--start-price", type=str, help="Start price to apply.")
    parser.add_argument("--quantity", type=int, help="Quantity available.")
    parser.add_argument("--condition-id", type=str, help="eBay numeric condition code.")
    parser.add_argument("--category-id", type=str, help="Override category ID.")
    parser.add_argument("--title", type=str, help="Override listing title.")
    parser.add_argument("--image-url", type=str, help="Primary image URL.")
    parser.add_argument("--location", type=str, help="Item location (city, state).")
    parser.add_argument("--shipping-profile", type=str, help="Business policy name.")
    parser.add_argument("--return-profile", type=str, help="Business policy name.")
    parser.add_argument("--payment-profile", type=str, help="Business policy name.")
    return parser.parse_args()


def newest_json(path: Path) -> Path:
    directory = path if path.is_dir() else DEFAULT_JSON_DIR
    if directory.is_file():
        return directory
    candidates = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No JSON files found in {directory}")
    return candidates[0]


def load_json(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return "" if value is None else str(value)
    replacements = {
        "\uFFFD": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "…": "...",
        "•": "-",
        "©": "(c)",
        "�": "'",
    }
    result = value
    for bad, good in replacements.items():
        result = result.replace(bad, good)
    return result.strip()


def build_description(payload: Mapping[str, Any]) -> str:
    blurb = clean_text(payload.get("blurb"))
    condition = clean_text(payload.get("condition"))
    details = clean_text(payload.get("details"))

    parts: list[str] = []
    if blurb:
        parts.append(blurb)
    if condition:
        parts.append(f"Condition Notes:\n{condition}")
    if details:
        parts.append(f"Collector Details:\n{details}")
    return "\n\n".join(parts)


def make_row(payload: Mapping[str, Any], args: argparse.Namespace) -> list[Any]:
    row = ["" for _ in HEADERS]
    value_map = dict(DEFAULT_VALUES)

    json_lower = {str(k).lower(): v for k, v in payload.items()}
    value_map["Title"] = args.title or clean_text(json_lower.get("title"))
    value_map["C:Author"] = clean_text(json_lower.get("author"))
    value_map["C:Edition"] = clean_text(json_lower.get("edition"))
    value_map["C:Publication Year"] = clean_text(json_lower.get("year"))
    value_map["C:Publisher"] = clean_text(json_lower.get("publisher"))
    value_map["C:Book Title"] = value_map.get("Title", "")
    value_map["Description"] = build_description(json_lower)

    if args.start_price is not None:
        value_map["Start price"] = args.start_price
    if args.quantity is not None:
        value_map["Quantity"] = args.quantity
    if args.condition_id is not None:
        value_map["Condition ID"] = args.condition_id
    if args.category_id:
        value_map["Category ID"] = args.category_id
    if args.image_url is not None:
        value_map["Item photo URL"] = args.image_url
    if args.location is not None:
        value_map["Location"] = args.location
    if args.shipping_profile is not None:
        value_map["Shipping profile name"] = args.shipping_profile
    if args.return_profile is not None:
        value_map["Return profile name"] = args.return_profile
    if args.payment_profile is not None:
        value_map["Payment profile name"] = args.payment_profile

    # Ensure columns AE (31) through AI (35) are blank.
    blank_columns = {"Max dispatch time", "Returns accepted option", "Returns within option", "Refund option", "Return shipping cost paid by"}

    for idx, header in enumerate(HEADERS):
        if header in blank_columns:
            row[idx] = ""
        else:
            row[idx] = value_map.get(header, "")
    return row


def build_workbook(row: list[Any]) -> Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Listings"
    ws.append(HEADERS)
    ws.append(row)

    info_sheet = wb.create_sheet("INFO")
    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    info_sheet["A1"] = "Generated"
    info_sheet["B1"] = timestamp
    return wb


def main() -> None:
    args = parse_args()
    json_path = newest_json(args.json if args.json else DEFAULT_JSON_DIR)
    payload = load_json(json_path)
    row = make_row(payload, args)
    wb = build_workbook(row)
    wb.save(args.output)
    print(f"Wrote {args.output} using {json_path.name}")


if __name__ == "__main__":
    main()
