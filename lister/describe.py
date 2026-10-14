"""Step 3: write the buyer-facing description."""

from __future__ import annotations

from html import escape

from . import categories, llm
from .config import Settings
from .models import DescriptionDraft, Item

SYSTEM = """You write eBay listing descriptions for a small used and antiquarian bookseller.

Voice: knowledgeable, warm, plain English. No hype words ("rare", "stunning", "must-have")
unless the facts support them, no ALL CAPS, no emoji, no claims about value or investment.
Only state facts present in the cataloguing data; never add edition, printing, or provenance
details that are not there. The condition paragraph must match the condition grade and
mention every listed flaw. Keep the whole description under about 200 words.

`uncertainties` and notes about what was or wasn't photographed are the seller's checklist,
not buyer copy: never tell the buyer something "has not been examined" or that only part of
the item was photographed. Leave an unknown fact out rather than drawing attention to it.
Set `closing` to null unless the seller's notes say something about shipping or packing;
do not invent packing or shipping promises."""


def describe(settings: Settings, item: Item) -> DescriptionDraft:
    ident = item.identification
    if ident is None:
        raise ValueError(f"{item.sku} must be identified before writing a description")
    grade = categories.get(ident.category).conditions.get(ident.condition_id, ident.condition_id)
    prompt = (
        "Write the description for this item.\n\n"
        f"Cataloguing data (JSON):\n{ident.model_dump_json(indent=2)}\n\n"
        f"Condition grade: {grade}\n"
    )
    if item.notes.strip():
        prompt += f"\nSeller's notes:\n{item.notes.strip()}\n"
    return llm.parse(settings.models.describe, SYSTEM, prompt, DescriptionDraft, effort="medium")


def render_html(draft: DescriptionDraft) -> str:
    """Simple, mobile-friendly HTML that eBay accepts in the Description column."""
    parts = [f"<p>{escape(draft.intro)}</p>"]
    if draft.details:
        parts.append("<ul>" + "".join(f"<li>{escape(d)}</li>" for d in draft.details) + "</ul>")
    parts.append(f"<p><b>Condition:</b> {escape(draft.condition)}</p>")
    if draft.closing:
        parts.append(f"<p>{escape(draft.closing)}</p>")
    return "".join(parts)
