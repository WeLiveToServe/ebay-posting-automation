"""The HTML preview: every multi-agent run plus a read-only snapshot of the live eBay listings.

One self-contained file at data/previews/listings.html, rebuilt after every run and served at
/preview by `lister serve`. A hamburger menu switches between listings. The live snapshot is
taken with read-only Trading API calls (GetMyeBaySelling, GetItem) and cached in live.json.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import assemble, categories, crew
from .config import Settings
from .models import Item
from .store import Store

TEMPLATES = Path(__file__).parent / "templates"
CONDITION_NAMES = categories.BOOK_CONDITIONS


def previews_dir(settings: Settings) -> Path:
    return settings.data_dir / "previews"


def live_path(settings: Settings) -> Path:
    return previews_dir(settings) / "live.json"


def _src(path: str, base: Path) -> str:
    """Photo path relative to the preview file, so it works opened from disk and at /preview."""
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:  # another drive on Windows
        return Path(path).resolve().as_uri()


def run_entry(item: Item, base: Path) -> dict | None:
    """Preview data for an item with an assembled multi-agent run (None if it has none)."""
    run = item.crew
    if not run or not run.assembled:
        return None
    listing = run.assembled
    edition = crew.result(item, "edition")
    condition = crew.result(item, "condition")
    pricing = crew.result(item, "pricing")
    by_idx = {p.idx: p for p in item.photos}
    labels = {p.idx: p for p in edition.photos}
    photos = []
    for idx in listing.photo_order:
        if idx in by_idx:
            label = labels.get(idx)
            caption = f"{label.role.replace('_', ' ')}{f', vol {label.volume}' if label.volume else ''}" if label else ""
            photos.append({"src": _src(by_idx[idx].derived, base), "caption": caption})
    return {
        "key": item.sku,
        "kind": "run",
        "menu": listing.title,
        "title": listing.title,
        "sku": item.sku,
        "status": "signed off" if run.signed_off_at else "awaiting orchestrator sign-off",
        "ebay_url": f"https://www.ebay.com/itm/{item.ebay_listing_id}" if item.ebay_listing_id else "",
        "age": crew.age(edition.year),
        "older": crew.is_older(edition.year),
        "edition_rows": assemble.edition_facts(edition),
        "category": categories.get(listing.category).name,
        "grade": condition.grade,
        "ebay_condition": CONDITION_NAMES.get(listing.condition_id, ""),
        "condition_text": condition.summary,
        "description_html": listing.description_html,
        "price": {"suggested": pricing.suggested, "low": pricing.low, "high": pricing.high,
                  "provisional": pricing.provisional, "confidence": pricing.confidence, "reason": pricing.reason,
                  "ebay_history": pricing.ebay_history},
        "comps": [c.model_dump() for c in sorted(pricing.comps, key=lambda c: (c.match != "same", c.status != "sold", c.price))],
        "questions": listing.questions,
        "problems": listing.problems,
        "photos": photos,
    }


def live_entry(live: dict) -> dict:
    """Preview data for a live listing from the snapshot (read-only)."""
    aspects = live.get("aspects") or {}
    return {
        "key": f"live-{live['item_id']}",
        "kind": "live",
        "menu": live["title"],
        "title": live["title"],
        "sku": live.get("sku", ""),
        "status": f"live on eBay · {live.get('watchers', 0)} watchers · listed {live.get('started', '')}",
        "ebay_url": f"https://www.ebay.com/itm/{live['item_id']}",
        "edition_rows": list(aspects.items()),
        "category": next((c.name for c in categories.CATEGORIES.values() if c.id == live.get("category_id")), ""),
        "grade": "",
        "ebay_condition": CONDITION_NAMES.get(live.get("condition_id", ""), ""),
        "condition_text": "",
        "live_description": live.get("description_html", ""),
        "price": {"suggested": live.get("price", 0)},
        "comps": [],
        "questions": [],
        "problems": [],
        "photos": [{"src": url, "caption": ""} for url in live.get("photo_urls", [])],
    }


def snapshot_live(settings: Settings) -> list[dict]:
    """Fetch the seller's active listings with read-only calls and cache them for the preview."""
    from .account import EbayAccount  # needs eBay credentials; imported only when used

    acct = EbayAccount(settings)
    rows = []
    for listing in acct.active_listings():
        detail = acct.get_item(listing.item_id)
        rows.append({**detail, "watchers": listing.watchers, "started": listing.started})
    path = live_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    return rows


def load_live(settings: Settings) -> list[dict]:
    path = live_path(settings)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def render(entries: list[dict], live_taken: str = "") -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    return env.get_template("preview.html").render(
        runs=[e for e in entries if e["kind"] == "run"],
        live=[e for e in entries if e["kind"] == "live"],
        entries=entries, live_taken=live_taken)


def build(settings: Settings, store: Store) -> Path:
    """Rebuild data/previews/listings.html from every run and the cached live snapshot."""
    base = previews_dir(settings)
    base.mkdir(parents=True, exist_ok=True)
    entries = [e for e in (run_entry(i, base) for i in store.list()) if e]
    # A live listing that also has a run (a rewrite draft) appears in both groups, for comparison.
    entries += [live_entry(r) for r in load_live(settings)]
    taken = ""
    if live_path(settings).exists():
        taken = datetime.fromtimestamp(live_path(settings).stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    out = base / "listings.html"
    out.write_text(render(entries, taken), encoding="utf-8")
    return out
