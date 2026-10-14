"""eBay Seller Hub Reports CSV (fx_category_template_EBAY_US) export."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from . import categories
from .config import Settings
from .models import Item

ACTION = "*Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)"
BASE_COLUMNS = [
    ACTION, "Custom label (SKU)", "Category ID", "Category name", "Title", "P:ISBN",
    "Start price", "Quantity", "Item photo URL", "Condition ID", "Description", "Format",
    "Duration", "Best Offer Enabled", "Best Offer Auto Accept Price", "Minimum Best Offer Price",
    "Location", "Shipping profile name", "Return profile name", "Payment profile name",
]


def columns() -> list[str]:
    aspects: list[str] = []
    for cat in categories.CATEGORIES.values():
        aspects += [a for a in cat.aspects if a not in aspects]
    return BASE_COLUMNS + [f"C:{a}" for a in aspects]


def validate(item: Item) -> list[str]:
    errors = []
    listing = item.listing
    try:
        cat = categories.get(listing.category)
    except KeyError as exc:
        return [str(exc)]
    if not listing.title.strip():
        errors.append("title is empty")
    elif len(listing.title) > 80:
        errors.append(f"title is {len(listing.title)} characters (max 80)")
    if not listing.price or listing.price <= 0:
        errors.append("price is missing")
    if listing.condition_id not in cat.conditions:
        errors.append(f"condition {listing.condition_id!r} is not valid for {cat.name}")
    if not item.photos:
        errors.append("no photos")
    elif any(not p.url for p in item.photos):
        errors.append("photos are not uploaded to R2 yet")
    if not listing.description_html.strip():
        errors.append("description is empty")
    for name in cat.required:
        if not listing.aspects.get(name, "").strip():
            errors.append(f"required item specific '{name}' is empty")
    return errors


def row(settings: Settings, item: Item) -> dict[str, str]:
    listing = item.listing
    cat = categories.get(listing.category)
    out = {
        ACTION: "Add",
        "Custom label (SKU)": item.sku,
        "Category ID": cat.id,
        "Category name": cat.name,
        "Title": listing.title.strip(),
        "P:ISBN": listing.isbn,
        "Start price": f"{listing.price:.2f}",
        "Quantity": "1",
        "Item photo URL": "|".join(p.url for p in item.photos if p.url),
        "Condition ID": listing.condition_id,
        "Description": listing.description_html,
        "Format": settings.listing.format,
        "Duration": settings.listing.duration,
        "Location": settings.seller.location,
        "Shipping profile name": settings.seller.shipping_profile,
        "Return profile name": settings.seller.return_profile,
        "Payment profile name": settings.seller.payment_profile,
    }
    if settings.listing.best_offer and settings.listing.format == "FixedPrice":
        out["Best Offer Enabled"] = "1"
        if settings.listing.best_offer_auto_accept:
            out["Best Offer Auto Accept Price"] = f"{listing.price * settings.listing.best_offer_auto_accept:.2f}"
        if settings.listing.best_offer_minimum:
            out["Minimum Best Offer Price"] = f"{listing.price * settings.listing.best_offer_minimum:.2f}"
    allowed = set(cat.aspects)
    for name, value in listing.aspects.items():
        if name in allowed and value.strip():
            out[f"C:{name}"] = value.strip()
    return out


def write_csv(settings: Settings, items: list[Item], path: Path | None = None) -> Path:
    if path is None:
        settings.exports_dir.mkdir(parents=True, exist_ok=True)
        path = settings.exports_dir / f"ebay-upload-{datetime.now():%Y%m%d-%H%M%S}.csv"
    header = columns()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            writer.writerow({k: row(settings, item).get(k, "") for k in header})
    return path
