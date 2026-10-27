"""Assembly: the code stage of a multi-agent run. The same every time, no model involved.

Turns the four role results into a listing (title, photo order, condition, description HTML,
item specifics, price), then checks it against the hard rules and eBay's required fields.
Each problem names the role responsible, so the orchestrator knows whom to correct.
"""

from __future__ import annotations

import re
from html import escape

from . import categories, crew
from .models import Assembled, Item
from .crew import ConditionResult, DescriptionResult, EditionResult, PhotoLabel, PricingResult

MAX_TITLE = 80
PLACEHOLDER_AUTHORS = {"various", "anonymous", "anon.", "unknown"}

# --- Title -------------------------------------------------------------------------

_PUBLISHER_NOISE = re.compile(r",?\s*\b(Ltd\.?|Limited|Inc\.?|Publishers?|Publishing|Company|Co\.?|& Co\.?|and Company)$", re.I)


def compact_publisher(publisher: str) -> str:
    """'A. & C. Black Ltd.' -> 'A. & C. Black'; repeated until nothing more comes off."""
    previous = None
    while previous != publisher:
        previous, publisher = publisher, _PUBLISHER_NOISE.sub("", publisher).strip(" ,")
    return publisher


def _fit(parts: list[str | None]) -> str:
    return re.sub(r"\s+", " ", " ".join(p.strip() for p in parts if p and p.strip())).strip()


def build_title(edition: EditionResult, older: bool) -> str:
    """Facts only, at most 80 characters.

    Older books: year + edition + publisher, then author, then title.
    Newer books: author, then title.
    Multi-volume sets end with "Set of N".
    When too long, drop in this order: subtitle, publisher noise, publisher, edition, then cut
    the title at a word boundary. Author, title and the set count are never dropped.
    """
    title = edition.title.strip()
    full_title = f"{title}: {edition.subtitle.strip()}" if edition.subtitle else title
    author = edition.author.strip()
    if author.lower() in PLACEHOLDER_AUTHORS:  # an anthology: the title carries it
        author = ""
    if not older:
        candidates = [[author, full_title], [author, title]]
    else:
        publisher = edition.publisher.strip()
        short_pub = compact_publisher(publisher)
        lead = [str(edition.year), edition.edition_short]
        candidates = [
            lead + [publisher, author, full_title],
            lead + [publisher, author, title],
            lead + [short_pub, author, title],
            [str(edition.year), short_pub, author, title],
            [str(edition.year), edition.edition_short, author, title],
            [str(edition.year), author, title],
        ]
    if edition.volumes > 1:  # a set says so; that fact outranks the publisher and edition
        suffix = f"Set of {edition.volumes}"
        candidates = [c + [suffix] for c in candidates] + [[author, title, suffix]]
    for parts in candidates:
        text = _fit(parts)
        if len(text) <= MAX_TITLE:
            return text
    text = _fit(candidates[-1])
    return text[:MAX_TITLE].rsplit(" ", 1)[0]


# --- Photo order ---------------------------------------------------------------------

_COVERS = ("front_cover", "spine", "back_cover")


def photo_order(labels: list[PhotoLabel], cover_illustrated: bool) -> list[int]:
    """Photo idx values in listing order.

    1. Front covers if the cover is illustrated, otherwise spines.
    2. The remaining cover and spine shots (front, spine, back).
    3. Title and copyright pages, grouped by volume.
    4. Plates, then defects, then anything else.
    5. Angle shots of page edges and binding last.
    Within a group, photos keep volume order and then their original order.
    """
    lead = "front_cover" if cover_illustrated else "spine"
    position = {p.idx: n for n, p in enumerate(labels)}

    def key(p: PhotoLabel) -> tuple:
        vol = p.volume or 0
        if p.role == lead:
            group, sub = 0, 0
        elif p.role in _COVERS:
            group, sub = 1, _COVERS.index(p.role)
        elif p.role in ("title_page", "copyright_page"):
            return (2, vol, ("title_page", "copyright_page").index(p.role), position[p.idx])
        elif p.role in ("plate", "defect", "other"):
            group, sub = 3, ("plate", "defect", "other").index(p.role)
        else:  # angle
            group, sub = 4, 0
        return (group, sub, vol, position[p.idx])

    return [p.idx for p in sorted(labels, key=key)]


# --- Description HTML ----------------------------------------------------------------


def edition_facts(edition: EditionResult) -> list[tuple[str, str]]:
    """Label/value pairs shown in the listing and the preview's Edition section."""
    place = f"{edition.place}: " if edition.place else ""
    rows = [
        ("Title", edition.title + (f": {edition.subtitle}" if edition.subtitle else "")),
        ("Author", edition.author),
        ("Editor", edition.editor),
        ("Illustrator", edition.illustrator),
        ("Published", f"{place}{edition.publisher}, {'' if edition.year_certain else 'c. '}{edition.year}"),
        ("First published", str(edition.first_published) if edition.first_published and edition.first_published != edition.year else None),
        ("Edition", edition.edition),
        ("Series", edition.series),
        ("Volumes", str(edition.volumes) if edition.volumes > 1 else None),
        ("Binding", edition.binding + ("" if edition.dust_jacket is None else
                                       ", with dust jacket" if edition.dust_jacket else ", no dust jacket")),
        ("Format", edition.format),
        ("Pages", str(edition.pages) if edition.pages else None),
        ("Illustrated", "Yes" if edition.illustrated else None),
        ("ISBN", edition.isbn),
    ]
    points = [p.point for p in edition.issue_points if p.present]
    if points:
        rows.append(("Issue points", "; ".join(points)))
    return [(k, v) for k, v in rows if v]


def listing_facts(edition: EditionResult) -> list[str]:
    """The short facts list in the description, in the Psmith in the City style (no bold labels)."""
    place = f", {edition.place}" if edition.place else ""
    date = f"Dated {edition.year}" if edition.year_certain else f"Undated, c. {edition.year}"
    rows = [
        f"Title: {edition.title}" + (f": {edition.subtitle}" if edition.subtitle else ""),
        f"Author: {edition.author}",
        f"Edited by {edition.editor}" if edition.editor else None,
        f"Publisher: {edition.publisher}{place}",
        date,
        f"Edition: {edition.edition}" if edition.edition_short else None,  # only when remarkable
        f"Illustrated by {edition.illustrator}" if edition.illustrator else None,
        f"{edition.volumes} volumes" if edition.volumes > 1 else None,
        f"Binding: {edition.binding}" + ("" if edition.dust_jacket is None else
                                         ", with dust jacket" if edition.dust_jacket else ""),
        f"ISBN: {edition.isbn}" if edition.isbn else None,
        f"Language: {edition.language}",
    ]
    return [r for r in rows if r]


def description_html(edition: EditionResult, condition: ConditionResult, description: DescriptionResult) -> str:
    """Blurb, facts list, condition paragraph: the layout of the Psmith in the City listing."""
    text = crew.condition_text(condition)
    grade, rest = text.split(". ", 1) if ". " in text else (text.rstrip("."), "")
    return "".join([
        f"<p>{escape(description.blurb.strip())}</p>",
        "<ul>" + "".join(f"<li>{escape(r)}</li>" for r in listing_facts(edition)) + "</ul>",
        f"<p><b>Condition:</b> {escape(grade)}. {escape(rest)}</p>",
    ])


# --- Aspects ------------------------------------------------------------------------


def aspects(edition: EditionResult) -> dict[str, str]:
    """Agent-supplied item specifics, filled in from the edition facts where empty."""
    cat = categories.get(edition.category)
    allowed = set(cat.aspects)
    out = {a.name: a.value.strip() for a in edition.aspects if a.name in allowed and a.value.strip()}
    defaults = {
        "Author": edition.author, "Book Title": edition.title, "Language": edition.language,
        "Publisher": edition.publisher, "Illustrator": edition.illustrator, "Binding": edition.binding,
        "Publication Year": str(edition.year), "Year Printed": str(edition.year),
        "Place of Publication": edition.place, "Edition": edition.edition, "Book Series": edition.series,
    }
    for name, value in defaults.items():
        if name in allowed and value and name not in out:
            out[name] = value
    return out


# --- Assembly and checks -------------------------------------------------------------


def assemble(item: Item) -> Assembled:
    """Build the listing from the latest role results and store it on the run."""
    run = item.crew
    if run is None:
        raise ValueError(f"{item.sku} has no multi-agent run; start one with `run start`")
    missing = [r for r in crew.ORDER if not run.roles[r].latest]
    if missing:
        raise ValueError(f"{item.sku}: still missing {', '.join(missing)}")
    edition: EditionResult = crew.result(item, "edition")
    condition: ConditionResult = crew.result(item, "condition")
    pricing: PricingResult = crew.result(item, "pricing")
    description: DescriptionResult = crew.result(item, "description")
    old = crew.collector(item, edition)

    problems = [f"{role}: {p}" for role in crew.ORDER for p in crew.check(item, role, crew.result(item, role))]
    problems += [f"{role}: result is stale (an earlier stage changed); re-run it"
                 for role in crew.ORDER if run.roles[role].stale]
    grade_ok = not crew.grade_problems(condition.grade, old)
    listing = Assembled(
        category=edition.category,
        title=build_title(edition, old),
        price=round(pricing.suggested, 2),
        condition_id=crew.ebay_condition_id(condition.grade) if grade_ok else "",
        grade=condition.grade,
        isbn=edition.isbn or "",
        aspects=aspects(edition),
        description_html=description_html(edition, condition, description),
        photo_order=photo_order(edition.photos, edition.cover_illustrated),
    )
    problems += [f"assembly: {p}" for p in validate(item, listing)]
    listing.problems = problems
    listing.questions = questions(item, edition, condition, pricing, description)
    run.assembled = listing
    run.signed_off_at = ""
    return listing


def validate(item: Item, listing: Assembled) -> list[str]:
    """eBay required fields and the listing-level hard rules."""
    errors = []
    cat = categories.get(listing.category)
    if not listing.title:
        errors.append("title is empty")
    elif len(listing.title) > MAX_TITLE:
        errors.append(f"title is {len(listing.title)} characters (max {MAX_TITLE})")
    if listing.title.isupper():
        errors.append("title is ALL CAPS")
    if listing.price <= 0:
        errors.append("price is missing")
    if listing.condition_id not in cat.conditions:
        errors.append(f"condition {listing.condition_id!r} is not valid for {cat.name}")
    if listing.condition_id == "1000":
        errors.append("condition may never be Brand New")
    for name in cat.required:
        if not listing.aspects.get(name, "").strip():
            errors.append(f"required item specific '{name}' is empty")
    if sorted(listing.photo_order) != sorted(p.idx for p in item.photos):
        errors.append("photo order does not cover every photo exactly once")
    if not listing.description_html.strip():
        errors.append("description is empty")
    return errors


def questions(item: Item, edition: EditionResult, condition: ConditionResult, pricing: PricingResult,
              description: DescriptionResult) -> list[str]:
    """Everything that needs the seller's judgment, de-duplicated, tagged by role."""
    out: list[str] = []
    for role, res in (("edition", edition), ("condition", condition), ("pricing", pricing),
                      ("description", description)):
        out += [f"{role}: {q}" for q in res.questions]
        out += [f"{role} (contrary evidence): {c}" for c in getattr(res, "contrary_evidence", [])]
    if not edition.edition_confirmed:
        out.append("edition: the edition is not confirmed by 2 matching sources; check it before listing")
    if warning := crew.track_warning(item, edition):
        out.append(f"track: {warning}")
    if not edition.year_certain:
        out.append(f"edition: the year {edition.year} is an estimate")
    if pricing.provisional:
        out.append(f"pricing: ${pricing.suggested:.2f} is provisional until the questions above are answered")
    seen: set[str] = {a.question for a in item.crew.answers} if item.crew else set()
    return [q for q in out if not (q in seen or seen.add(q))]


def apply(item: Item) -> None:
    """Copy the signed-off listing onto the item's editable listing fields and photo order."""
    listing = item.crew.assembled
    item.listing = item.listing.model_copy(update={
        "category": listing.category, "title": listing.title, "price": listing.price,
        "condition_id": listing.condition_id, "isbn": listing.isbn, "aspects": dict(listing.aspects),
        "description_html": listing.description_html,
    })
    by_idx = {p.idx: p for p in item.photos}
    item.photos = [by_idx[i] for i in listing.photo_order if i in by_idx]
