"""Step 1: identify the item from its photos."""

from __future__ import annotations

from . import categories, llm
from .config import Settings
from .models import Identification, Item

SYSTEM = """You are an experienced bookseller cataloguing items for eBay from photographs.

Work only from what the photos show plus well-established bibliographic knowledge. Never
invent an ISBN, edition, printing, or signature. When something matters for value but
cannot be confirmed from the photos (first printing, dust jacket state, completeness,
whether a signature is authentic), say so in `uncertainties` instead of guessing.

Condition grades (eBay book condition IDs):
- 1000 Brand New: unread, no defects.
- 2750 Like New: looks new, no visible wear, may have been read once.
- 4000 Very Good: minimal wear, no tears/markings beyond a small name or inscription.
- 5000 Good: noticeable wear, possible minor markings or loose binding; complete and readable.
- 6000 Acceptable: heavily worn but complete and readable; notes, stains, or damaged covers.
Grade conservatively; buyers return books that are worse than described.

Item specifics (`aspects`): use only the aspect names listed for the chosen category, and
only when you can support the value. Leave others out rather than writing "Unknown"."""


def build_prompt(item: Item) -> str:
    lines = ["Catalogue this item for an eBay listing.", "", "Available categories:"]
    for cat in categories.CATEGORIES.values():
        lines.append(f"- {cat.key} ({cat.name}): {cat.guidance}")
        lines.append(f"  aspects: {', '.join(cat.aspects)}")
        if cat.required:
            lines.append(f"  required aspects: {', '.join(cat.required)}")
    if item.notes.strip():
        lines += ["", "Seller's notes about this copy (trust these over the photos):", item.notes.strip()]
    return "\n".join(lines)


def identify(settings: Settings, item: Item) -> Identification:
    if not item.photos:
        raise ValueError(f"{item.sku} has no photos")
    content = llm.image_blocks([p.derived for p in item.photos]) + [{"type": "text", "text": build_prompt(item)}]
    result = llm.parse(settings.models.identify, SYSTEM, content, Identification, effort="high")
    allowed = set(categories.get(result.category).aspects)
    result.aspects = [a for a in result.aspects if a.name in allowed and a.value.strip()]
    return result
