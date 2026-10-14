"""Domain records. The LLM-facing models double as structured-output schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from .categories import BOOK_CONDITIONS, CATEGORIES

CategoryKey = Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]
ConditionId = Literal[tuple(BOOK_CONDITIONS)]  # type: ignore[valid-type]
Confidence = Literal["high", "medium", "low"]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Status(StrEnum):
    CAPTURING = "capturing"   # photos still being added
    QUEUED = "queued"         # waiting for the worker
    PROCESSING = "processing"
    REVIEW = "review"         # drafts ready for a human
    APPROVED = "approved"     # will be included in the next export
    EXPORTED = "exported"
    ERROR = "error"


# --- LLM outputs -----------------------------------------------------------


class Aspect(BaseModel):
    name: str = Field(description="eBay item-specific name, exactly as listed for the category.")
    value: str


class Identification(BaseModel):
    category: CategoryKey
    formal_title: str = Field(description="Title as printed on the title page, without the author.")
    creator: str = Field(description="Primary author (or maker/brand for non-book items).")
    listing_title: str = Field(
        description="Buyer-facing eBay title, at most 80 characters, keyword-rich, no ALL CAPS or emoji."
    )
    isbn: str | None = Field(description="ISBN-10/13 digits only, if visible in a photo.")
    publisher: str | None
    publication_year: str | None
    edition_statement: str | None = Field(description="Edition/printing exactly as stated, e.g. 'First Edition, 3rd printing'.")
    is_first_edition: bool | None = Field(description="True only when the photos show evidence (statement or number line).")
    binding: str | None = Field(description="e.g. Hardcover, Paperback, Leather, Cloth.")
    aspects: list[Aspect]
    condition_id: ConditionId
    condition_notes: list[str] = Field(description="Specific observed flaws or confirmations, one per entry.")
    notable_features: list[str] = Field(description="Collector-relevant points: signatures, illustrations, provenance.")
    uncertainties: list[str] = Field(description="Facts that the photos could not establish and a human should check.")
    confidence: Confidence
    comps_query: str = Field(description="Short eBay search query for finding comparable copies.")


class RatedComp(BaseModel):
    index: int = Field(description="Index into the supplied comparables list.")
    relevance: Literal["strong", "partial", "weak"]
    note: str


class ResearchComp(BaseModel):
    kind: Literal["sold", "asking"]
    title: str
    price: float = Field(description="USD; convert if the source used another currency.")
    date: str | None = Field(description="Sale or listing date if known.")
    url: str | None
    relevance: Literal["strong", "partial", "weak"]
    note: str


class PriceSuggestion(BaseModel):
    list_price: float = Field(description="Recommended fixed price in USD.")
    low: float
    high: float
    confidence: Confidence
    rationale: str = Field(description="Two to four sentences on how the price was reached.")
    rated_active: list[RatedComp] = Field(description="Ratings for the supplied eBay active listings, by index.")
    research_comps: list[ResearchComp] = Field(description="Every priced comparable found in the web research.")
    warnings: list[str]


class DescriptionDraft(BaseModel):
    intro: str = Field(description="One short paragraph introducing the item and why it is appealing.")
    details: list[str] = Field(description="Bibliographic/physical facts as short bullet points.")
    condition: str = Field(description="Honest condition paragraph matching the condition grade and notes.")
    closing: str | None = Field(description="Optional one-line shipping/packing note; null to omit.")


# --- Stored records --------------------------------------------------------


class Comp(BaseModel):
    source: Literal["ebay_active", "ebay_sold", "web"]
    title: str
    price: float
    currency: str = "USD"
    shipping: float | None = None
    condition: str | None = None
    url: str | None = None
    sold_date: str | None = None
    notes: str | None = None
    relevance: str | None = None


class Photo(BaseModel):
    idx: int
    original: str
    derived: str
    url: str | None = None


class ListingFields(BaseModel):
    """What gets exported. Built from the drafts, then editable in review."""

    category: str = "books"
    title: str = ""
    price: float | None = None
    condition_id: str = ""
    isbn: str = ""
    aspects: dict[str, str] = Field(default_factory=dict)
    description_html: str = ""


class Item(BaseModel):
    sku: str
    status: Status = Status.CAPTURING
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    notes: str = ""
    shelf_location: str = ""
    cost: float | None = None
    photos: list[Photo] = Field(default_factory=list)
    identification: Identification | None = None
    comps: list[Comp] = Field(default_factory=list)
    research_summary: str = ""
    price: PriceSuggestion | None = None
    description: DescriptionDraft | None = None
    listing: ListingFields = Field(default_factory=ListingFields)
    error: str = ""
    exported_file: str = ""
