"""Agent mode: a Claude Code / Codex session does the thinking, the app does the plumbing.

The session reads an item's photos, researches prices with its own web tools, and writes the
description, then hands each result back as JSON through `python -m lister agent save`.
Results are validated against the same schemas and post-processed by the same code as the
Anthropic API path (`python -m lister process`), so both modes produce identical records.
See AGENTS.md for the runbook.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from . import categories, describe, export, identify, pricing
from .config import Settings
from .models import DescriptionDraft, Identification, Item, ListingFields, PriceSuggestion, Status
from .pipeline import refresh_listing

SCHEMAS: dict[str, type[BaseModel]] = {
    "identify": Identification,
    "price": PriceSuggestion,
    "describe": DescriptionDraft,
    "listing": ListingFields,
}


def schema(step: str) -> dict:
    return SCHEMAS[step].model_json_schema()


def guide(step: str, item: Item | None = None) -> str:
    """The same instructions the API path sends to Claude, for an agent to follow."""
    if step == "identify":
        text = identify.SYSTEM + "\n\n" + (identify.build_prompt(item) if item else "")
    elif step == "price":
        text = pricing.RESEARCH_SYSTEM
        if item and item.identification and item.identification.category == "antiquarian":
            text += "\n" + pricing.ANTIQUARIAN_GUIDANCE
        text += "\n\n--- Then set the price ---\n" + pricing.SUGGEST_SYSTEM + (
            "\n\n`rated_active` indexes refer to the `comps` list from `agent comps`. Put every"
            " priced comparable from your own research in `research_comps`, and add a"
            " `research_notes` string summarising what you found, with source URLs.")
    elif step == "describe":
        text = describe.SYSTEM
        if item and item.identification:
            grade = categories.get(item.identification.category).conditions.get(item.identification.condition_id, "")
            text += (f"\n\nCataloguing data (JSON):\n{item.identification.model_dump_json(indent=2)}"
                     f"\n\nCondition grade: {grade}")
    else:
        raise ValueError(f"Unknown step {step!r}; use one of {', '.join(SCHEMAS)}")
    if item and item.notes.strip():
        text += f"\n\nSeller's notes (first-hand facts, trust over photos):\n{item.notes.strip()}"
    return text + f"\n\nOutput: one JSON object matching `python -m lister agent schema {step}`."


def show(item: Item) -> dict:
    """Everything a session needs about an item, with photo paths it can open."""
    return {
        "sku": item.sku,
        "status": item.status.value,
        "notes": item.notes,
        "photos": [p.derived for p in item.photos],
        "done": {s: bool(getattr(item, a)) for s, a in
                 (("identify", "identification"), ("price", "price"), ("describe", "description"))},
        "identification": item.identification.model_dump() if item.identification else None,
        "price": item.price.model_dump(exclude={"rated_active", "research_comps"}) if item.price else None,
        "listing": item.listing.model_dump(exclude={"description_html"}),
        "description_html": item.listing.description_html,
        "ebay_listing_id": item.ebay_listing_id,
        "problems": export.validate(item),
    }


def fetch_comps(settings: Settings, item: Item) -> list[dict]:
    """Current eBay asking prices (own listings excluded), stored on the item for rating."""
    if item.identification is None:
        raise ValueError(f"{item.sku}: save the identify step first")
    active = pricing.active_comps(settings, item)
    item.comps = [c for c in item.comps if c.source != "ebay_active"] + active
    return [{"index": i, **c.model_dump(include={"title", "price", "shipping", "condition", "url"})}
            for i, c in enumerate(active)]


def save(item: Item, step: str, data: dict) -> list[str]:
    """Validate and store one step's JSON; returns remaining validation problems."""
    if step == "identify":
        result = Identification.model_validate(data)
        allowed = set(categories.get(result.category).aspects)
        result.aspects = [a for a in result.aspects if a.name in allowed and a.value.strip()]
        item.identification = result
    elif step == "price":
        research = str(data.pop("research_notes", ""))
        suggestion = PriceSuggestion.model_validate(data)
        active = [c for c in item.comps if c.source == "ebay_active"]
        pricing.apply_suggestion(item, suggestion, active, research)
    elif step == "describe":
        item.description = DescriptionDraft.model_validate(data)
    elif step == "listing":  # direct edits, e.g. a price the seller chose
        item.listing = ListingFields.model_validate({**item.listing.model_dump(), **data})
        return export.validate(item)
    else:
        raise ValueError(f"Unknown step {step!r}; use one of {', '.join(SCHEMAS)}")

    refresh_listing(item, (step,))
    if item.identification and item.price and item.description and item.status in (
            Status.QUEUED, Status.PROCESSING, Status.ERROR, Status.CAPTURING):
        item.status = Status.REVIEW
    item.error = ""
    return export.validate(item)


def dumps(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)

