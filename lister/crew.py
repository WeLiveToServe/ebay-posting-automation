"""Multi-agent listing runs: role schemas, the rules code enforces, briefs, and run state.

A run turns one book's photos into a listing with four role agents, started by an
orchestrator (whatever model the user has open, from any provider):

    edition -> condition -> pricing + description (parallel) -> assembly (code)

Each role has a fixed system prompt (`lister/roles/<role>.md`), an output schema (below), and a
recommended model. `python -m lister run brief SKU ROLE` writes the complete brief a role agent
works from: its system prompt, any orchestrator corrections, the given facts from earlier
stages, the photos, and the output schema. The agent writes JSON; `run save` validates it here,
so the rules hold whichever model produced it. See AGENTS.md for the orchestrator runbook.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .models import ROLE_ORDER, Answer, Aspect, Attempt, CrewRun, Item, utcnow

ROLES_DIR = Path(__file__).parent / "roles"
OLDER_YEARS = 40  # a book this many years old or more is "older"

# --- Age and condition rules ----------------------------------------------------

NEWER_GRADES = ("Like New", "Very Good", "Good", "Acceptable")  # eBay terms; never New
OLDER_GRADES = ("Fine", "Near Fine", "Very Good", "Good", "Fair", "Poor")  # collector terms
EBAY_CONDITION_IDS = {  # grade -> eBay book condition ID
    "Like New": "2750", "Fine": "4000", "Near Fine": "4000", "Very Good": "4000",
    "Good": "5000", "Fair": "6000", "Poor": "6000", "Acceptable": "6000",
}


def age(year: int, today: date | None = None) -> int:
    return (today or date.today()).year - year


def is_older(year: int, today: date | None = None) -> bool:
    return age(year, today) >= OLDER_YEARS


def grade_scale(older: bool) -> tuple[str, ...]:
    return OLDER_GRADES if older else NEWER_GRADES


def grade_problems(grade: str, older: bool) -> list[str]:
    """Why a grade is not allowed for a book of this age (empty when it is fine)."""
    if grade.lower() in ("new", "brand new"):
        return ["grade may never be New"]
    if older and grade.lower() == "like new":
        return [f"a book {OLDER_YEARS}+ years old may never be graded Like New"]
    scale = grade_scale(older)
    if grade not in scale:
        return [f"grade {grade!r} is not on the {'older (collector)' if older else 'newer (eBay)'} "
                f"scale: {', '.join(scale)}"]
    return []


def ebay_condition_id(grade: str) -> str:
    return EBAY_CONDITION_IDS[grade]


# --- Role output schemas -----------------------------------------------------------

PhotoRole = Literal["front_cover", "back_cover", "spine", "title_page", "copyright_page", "plate",
                    "angle", "defect", "other"]


class PhotoLabel(BaseModel):
    idx: int = Field(description="The photo's `idx` from the brief's photo list.")
    role: PhotoRole
    volume: int | None = Field(None, description="1-based volume number for multi-volume sets; null for one volume.")
    note: str = Field("", description="What the photo shows, in a few words.")


class IssuePoint(BaseModel):
    point: str = Field(description="An issue point dealers or bibliographies use to identify this edition/printing.")
    present: bool | None = Field(description="True if this copy shows it, false if not, null if the photos can't tell.")
    note: str = ""


class EditionSource(BaseModel):
    url: str
    what: str = Field(description="What the source is, e.g. 'AbeBooks dealer listing, 1919 A&C Black'.")
    images_compared: list[str] = Field(default_factory=list,
                                       description="Which images were compared with ours: cover, spine, title page, copyright page.")
    matches: bool = Field(description="True when the source's edition matches this copy.")
    mismatches: str = Field("", description="Differences between the source and this copy, if any.")


class EditionResult(BaseModel):
    category: Literal["books", "antiquarian"]
    title: str = Field(description="Title as printed on the title page, without the author.")
    subtitle: str | None = None
    author: str = Field(description="Author as usually catalogued, e.g. 'P. G. Wodehouse'.")
    editor: str | None = None
    illustrator: str | None = None
    illustrated: bool = Field(description="True if the book has illustrations or plates.")
    cover_illustrated: bool = Field(description="True if the front cover carries a pictorial design; false if plain.")
    publisher: str
    place: str | None = Field(None, description="Place of publication.")
    year: int = Field(description="Year this copy was printed (best estimate when undated).")
    year_certain: bool = Field(description="False when `year` is an estimate (e.g. an undated reprint).")
    first_published: int | None = Field(None, description="Year the work was first published, if different.")
    edition: str = Field(description="Edition and printing, e.g. 'First Edition, first printing' or 'Popular Edition reprint'.")
    edition_short: str | None = Field(None, description="At most 20 characters for the listing title, e.g. '1st Edition', "
                                                       "'Arden 2nd Series'. Null for an unremarkable reprint.")
    series: str | None = None
    issue_points: list[IssuePoint] = Field(default_factory=list)
    volumes: int = 1
    binding: str = Field(description="e.g. Hardcover cloth, Pictorial cloth, Paperback, Leather.")
    dust_jacket: bool | None = Field(None, description="True if a dust jacket is present, false if absent.")
    format: str | None = Field(None, description="Size or format, e.g. 'Octavo', '8vo', '19 cm'.")
    pages: int | None = None
    isbn: str | None = Field(None, description="ISBN digits only, if printed in the book.")
    language: str = "English"
    aspects: list[Aspect] = Field(default_factory=list,
                                  description="eBay item specifics for the category, only values you can support.")
    sources: list[EditionSource] = Field(default_factory=list)
    edition_confirmed: bool = Field(description="Older books: true only when 2+ sources whose images you compared "
                                                "match this edition. Newer books: true when one source confirms it.")
    photos: list[PhotoLabel]
    uncertainties: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list, description="Questions only the seller can answer.")
    contrary_evidence: list[str] = Field(default_factory=list,
                                         description="Evidence that contradicts the seller's notes (notes still win).")


# Areas inspected, in the order the condition paragraph describes them (outside in).
AREAS = ("jacket", "boards", "spine", "corners_edges", "hinges", "text_block", "plates", "marks", "repairs")
ConditionArea = Literal[AREAS]  # type: ignore[valid-type]


class AreaNote(BaseModel):
    area: ConditionArea
    notable: bool = Field(description="False when there is nothing worth telling a buyer; the area is then left "
                                      "out of the paragraph.")
    sentence: str = Field("", description="Notable areas: one plain buyer-facing sentence naming every defect in "
                                          "this area. Empty when not notable.")
    photos: list[int] = Field(default_factory=list, description="idx of the photos that show it (notable areas).")


class ConditionResult(BaseModel):
    grade: str = Field(description="One grade from the scale given in the brief.")
    modifier: Literal["+", "-"] | None = Field(None, description="Optional: slightly better (+) or worse (-) than the grade.")
    jacket_grade: str | None = Field(None, description="The dust jacket's own grade, when the book has one.")
    ex_library: bool = Field(description="True for any ex-library copy (stamps, pockets, spine labels, withdrawn marks).")
    book_club: bool = False
    positives: list[str] = Field(description="1-2 sentences on what is good about the copy, said first.")
    areas: list[AreaNote] = Field(description="One entry for every area listed in the brief, notable or not.")
    verdict: str = Field(description="One closing sentence, e.g. 'A sound, honest copy of a book now over 100 years old.'")
    questions: list[str] = Field(default_factory=list)
    contrary_evidence: list[str] = Field(default_factory=list,
                                         description="Evidence that the given edition facts are wrong. Report; don't override.")


def grade_label(c: ConditionResult) -> str:
    label = c.grade + (c.modifier or "")
    if c.ex_library:
        label = f"Ex-library, {label}"
    if c.book_club:
        label += ", book club edition"
    if c.jacket_grade:
        label += f", in a {c.jacket_grade} dust jacket"
    return label


def condition_text(c: ConditionResult) -> str:
    """The buyer-facing paragraph: grade, positives, notable areas outside in, verdict."""
    order = {a: n for n, a in enumerate(AREAS)}
    notes = sorted((a for a in c.areas if a.notable and a.sentence.strip()), key=lambda a: order[a.area])
    parts = [f"{grade_label(c)}."] + [p.strip() for p in c.positives] + [a.sentence.strip() for a in notes]
    parts.append(c.verdict.strip())
    return " ".join(p if p.endswith((".", "!", "?")) else p + "." for p in parts if p)


SourceKey = Literal[
    "ebay_sold", "ebay_active", "terapeak", "amazon", "barnes_noble", "abebooks_com", "abebooks_uk",
    "thriftbooks", "bookfinder", "biblio", "vialibri", "liveauctioneers", "invaluable", "pba_galleries",
    "forums", "google_deep", "other",
]
REQUIRED_SOURCES = {
    False: ("amazon", "barnes_noble", "abebooks_com", "thriftbooks", "bookfinder"),
    True: ("abebooks_com", "abebooks_uk", "biblio", "vialibri", "liveauctioneers", "invaluable",
           "pba_galleries", "forums", "google_deep"),
}
MIN_EBAY_COMPS = 5


class PriceComp(BaseModel):
    source: SourceKey
    url: str
    status: Literal["sold", "asking"]
    date: str | None = Field(description="Sale date, or the date the listing was posted, if known (YYYY-MM-DD or YYYY-MM).")
    price: float = Field(description="USD; convert other currencies.")
    shipping: float | None = None
    edition: str = Field(description="The comparable's edition/printing/year as its seller states it.")
    condition: str
    match: Literal["same", "nearby", "other"] = Field(
        description="same = this edition/printing; nearby = another year, issue or illustrated release; other = weak.")
    note: str = ""


class SourceCheck(BaseModel):
    source: SourceKey
    url: str = Field(description="The search URL used.")
    found: int = Field(description="How many relevant copies the source showed (0 is a valid answer).")
    note: str = ""


class PricingResult(BaseModel):
    suggested: float
    low: float
    high: float
    confidence: Literal["high", "medium", "low"]
    reason: str = Field(description="One line: how the suggested price follows from the comparables.")
    provisional: bool = Field(description="True while a question about the price (e.g. nearby editions) is open.")
    comps: list[PriceComp]
    sources_checked: list[SourceCheck]
    ebay_history: str = Field(description="What eBay sold history was reachable (90-day sold, Terapeak, none) and "
                                          "whether older sales were visible without prices.")
    questions: list[str] = Field(default_factory=list)
    contrary_evidence: list[str] = Field(default_factory=list)


class DescriptionResult(BaseModel):
    fame: Literal["well_known", "lesser_known"] = Field(description="How well known this title is.")
    blurb: str = Field(description="2-4 sentences: the story or work, then this copy's edition and its most "
                                   "attractive feature. No condition defects, no price, no fact list.")
    questions: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class Role:
    name: str
    schema: type[BaseModel]
    needs: tuple[str, ...]  # roles whose results this one receives as given facts
    claude: str  # recommended Claude model / effort
    codex: str  # recommended Codex setting
    effort: str


ROLES: dict[str, Role] = {
    "edition": Role("edition", EditionResult, (), "opus", "most capable model, high", "high"),
    "condition": Role("condition", ConditionResult, ("edition",), "sonnet (opus for older books)",
                      "default model, medium", "medium"),
    "pricing": Role("pricing", PricingResult, ("edition", "condition"), "opus", "most capable model, high", "high"),
    "description": Role("description", DescriptionResult, ("edition", "condition"), "sonnet",
                        "default model, medium", "medium"),
}
ORDER = ROLE_ORDER


def result(item: Item, role: str):
    """The latest validated result of a role, as its schema model (None if not run)."""
    state = item.crew.roles[role] if item.crew else None
    return ROLES[role].schema.model_validate(state.latest) if state and state.latest else None


def older(item: Item) -> bool | None:
    edition = result(item, "edition")
    return collector(item, edition) if edition else None


def collector(item: Item, edition: EditionResult | None = None) -> bool:
    """Collector track: the user's choice when set (`--track`), otherwise the age rule."""
    if item.track:
        return item.track == "collector"
    return is_older((edition or result(item, "edition")).year)


def condition_areas(edition: EditionResult) -> tuple[str, ...]:
    return AREAS if edition.dust_jacket else tuple(a for a in AREAS if a != "jacket")


def sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


def track_warning(item: Item, edition: EditionResult) -> str:
    """A sanity note when the user's track disagrees with the age rule (never blocking)."""
    if item.track and (item.track == "collector") != is_older(edition.year):
        return (f"track is {item.track} but the book is {age(edition.year)} years old; "
                "check the track is intended")
    return ""


# --- Briefs -----------------------------------------------------------------------


def role_prompt(role: str) -> str:
    return (ROLES_DIR / f"{role}.md").read_text(encoding="utf-8")


def brief(item: Item, role: str, out_path: Path, ebay_username: str = "") -> str:
    """The complete instructions for one role agent on one book."""
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}; use one of {', '.join(ORDER)}")
    run = item.crew or CrewRun()
    missing = [r for r in ROLES[role].needs if not run.roles[r].latest]
    if missing:
        raise ValueError(f"{item.sku}: save {', '.join(missing)} before briefing {role}")
    state = run.roles[role]
    parts = [f"# Brief: {role} agent for {item.sku}", "", "## System prompt (fixed for this role)", "",
             role_prompt(role).strip()]
    if state.corrections:
        parts += ["", "## Orchestrator corrections (part of your instructions; they override the above)", ""]
        parts += [f"{n}. {c}" for n, c in enumerate(state.corrections, 1)]
        if state.latest:
            parts += ["", "Your previous output, to revise rather than start over:", "```json",
                      json.dumps(state.latest, indent=2, ensure_ascii=False), "```"]
    parts += ["", "## This book", "", f"- SKU: {item.sku}", f"- Today: {date.today().isoformat()}"]
    if item.notes.strip():
        parts += ["", "Seller's notes (first-hand facts; they override anything inferred from photos):", "",
                  item.notes.strip()]
    if role == "edition":
        parts += ["", "Photos (open every one):", ""]
        parts += [f"- idx {p.idx}: {p.derived}" for p in item.photos]
    else:
        edition = result(item, "edition")
        old = collector(item, edition)
        source = f"set by the seller ({item.track})" if item.track else f"from age, {OLDER_YEARS}+ years = older"
        parts += ["", f"Age: {age(edition.year)} years. Track: {'OLDER (collector)' if old else 'NEWER (modern)'} "
                      f"book, {source}."]
        if role == "condition":
            parts += [f"Grade scale for this book: {', '.join(grade_scale(old))}.",
                      f"Areas to report, one `areas` entry each: {', '.join(condition_areas(edition))}."]
        if role == "pricing":
            parts += [f"Required sources for this book: {', '.join(REQUIRED_SOURCES[old])}."]
            if ebay_username:
                parts += [f"The seller's own eBay user is `{ebay_username}`: their listings are never comparables."]
        labels = {p.idx: p for p in edition.photos}
        parts += ["", "Photos (labelled by the edition agent):", ""]
        for p in item.photos:
            label = labels.get(p.idx)
            tag = f"{label.role}{f' vol {label.volume}' if label and label.volume else ''}" if label else "unlabelled"
            parts.append(f"- idx {p.idx} [{tag}]: {p.derived}")
        for need in ROLES[role].needs:
            parts += ["", f"Given facts from the {need} agent (accept them; report contrary evidence, don't override):",
                      "```json", json.dumps(run.roles[need].latest, indent=2, ensure_ascii=False), "```"]
    parts += ["", "## Output", "",
              f"Write ONE JSON object matching this schema to `{out_path}` (UTF-8), then reply with a one-line "
              f"summary. Do not run any `lister` command that writes to eBay.", "", "```json",
              json.dumps(ROLES[role].schema.model_json_schema(), indent=1), "```"]
    return "\n".join(parts) + "\n"


# --- Saving and corrections ---------------------------------------------------------


def _start(item: Item) -> CrewRun:
    if item.crew is None:
        item.crew = CrewRun()
    return item.crew


def save(item: Item, role: str, data: dict) -> list[str]:
    """Validate a role's output and store it as a new attempt; returns rule problems.

    Problems are returned rather than raised so the orchestrator can send them back to the agent
    as a correction; invalid JSON for the schema raises instead.
    """
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}; use one of {', '.join(ORDER)}")
    run = _start(item)
    parsed = ROLES[role].schema.model_validate(data)
    problems = check(item, role, parsed)
    state = run.roles[role]
    state.attempts.append(Attempt(output=parsed.model_dump(mode="json"), corrections=list(state.corrections)))
    state.stale = False
    for later in ORDER[ORDER.index(role) + 1:]:  # later stages built on the old result
        if role in ROLES[later].needs and run.roles[later].latest:
            run.roles[later].stale = True
    run.assembled, run.signed_off_at = None, ""
    return problems


def dependents(roles: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """These roles plus every later role built on them, in run order."""
    out = set(roles)
    for later in ORDER:
        if any(n in out for n in ROLES[later].needs):
            out.add(later)
    return tuple(r for r in ORDER if r in out)


def question_role(question: str) -> str:
    """The role a tagged question ("pricing: ...", "edition (contrary evidence): ...") belongs to."""
    tag = question.split(":", 1)[0].split(" (")[0].strip()
    return tag if tag in ROLES else "edition"


def answer(item: Item, question: str, text: str) -> str:
    """Record the seller's answer as a correction to the question's role; returns the role."""
    role = question_role(question)
    body = question.split(": ", 1)[1] if ": " in question else question
    correct(item, role, f'The seller answered your question "{body}": {text.strip()} '
                        "(a first-hand fact; act on it and drop the question).")
    item.crew.answers.append(Answer(role=role, question=question, answer=text.strip()))
    return role


def correct(item: Item, role: str, text: str) -> None:
    _start(item).roles[role].corrections.append(text.strip())
    item.crew.signed_off_at = ""


def check(item: Item, role: str, parsed: BaseModel) -> list[str]:
    """Rules code enforces on one role's output."""
    problems: list[str] = []
    if role == "edition":
        photo_ids = {p.idx for p in item.photos}
        labelled = {p.idx for p in parsed.photos}
        if photo_ids - labelled:
            problems.append(f"photos not labelled: {sorted(photo_ids - labelled)}")
        if labelled - photo_ids:
            problems.append(f"labels for photos that don't exist: {sorted(labelled - photo_ids)}")
        if collector(item, parsed):
            compared = [s for s in parsed.sources if s.images_compared]
            matching = [s for s in compared if s.matches]
            if len(compared) < 2:
                problems.append("older book: compare the edition against at least 2 online sources with images")
            if parsed.edition_confirmed and len(matching) < 2:
                problems.append(f"edition_confirmed is true but only {len(matching)} source(s) with compared images "
                                "match; find another or set edition_confirmed false")
        if parsed.edition_short and len(parsed.edition_short) > 20:
            problems.append("edition_short is over 20 characters")
        return problems
    old = collector(item)
    if role == "condition":
        edition = result(item, "edition")
        problems += grade_problems(parsed.grade, old)
        if parsed.jacket_grade:
            problems += [f"jacket {p}" for p in grade_problems(parsed.jacket_grade, old)]
        if parsed.modifier and not old:
            problems.append("+/- modifiers are for the collector scale only")
        if edition.dust_jacket and not parsed.jacket_grade:
            problems.append("the book has a dust jacket: give jacket_grade")
        seen = [a.area for a in parsed.areas]
        missing = [a for a in condition_areas(edition) if a not in seen]
        if missing:
            problems.append(f"areas not reported: {', '.join(missing)}")
        if dupes := sorted({a for a in seen if seen.count(a) > 1}):
            problems.append(f"areas reported more than once: {', '.join(dupes)}")
        photo_ids = {p.idx for p in item.photos}
        for a in parsed.areas:
            if a.notable and not a.sentence.strip():
                problems.append(f"{a.area} is notable but has no sentence")
            if a.notable and not a.photos:
                problems.append(f"{a.area}: cite the photo(s) that show it")
            if bad := [i for i in a.photos if i not in photo_ids]:
                problems.append(f"{a.area}: photos {bad} don't exist")
        if not 1 <= len(parsed.positives) <= 2:
            problems.append("give 1-2 positives")
        if sentences(parsed.verdict) != 1:
            problems.append("verdict must be exactly one sentence")
    elif role == "pricing":
        if not parsed.low <= parsed.suggested <= parsed.high:
            problems.append("suggested price is outside the low-high range")
        ebay = [c for c in parsed.comps if c.source in ("ebay_sold", "ebay_active", "terapeak")]
        if len(ebay) < MIN_EBAY_COMPS and not any("ebay" in q.lower() for q in parsed.questions):
            problems.append(f"only {len(ebay)} eBay comparables (need {MIN_EBAY_COMPS}, or a question explaining why)")
        checked = {s.source for s in parsed.sources_checked}
        missing = [s for s in REQUIRED_SOURCES[old] if s not in checked]
        if missing:
            problems.append(f"required sources not checked: {', '.join(missing)}")
        if any(c.match == "nearby" for c in parsed.comps) and not (parsed.provisional and parsed.questions):
            problems.append("nearby-edition comparables need a weighting question and provisional=true")
        if any(not c.url.startswith("http") for c in parsed.comps):
            problems.append("every comparable needs its URL")
    elif role == "description":
        n = sentences(parsed.blurb)
        if not 2 <= n <= 4:
            problems.append(f"blurb is {n} sentences (2-4)")
        if re.search(r"\$\s?\d", parsed.blurb):
            problems.append("blurb must not mention a price")
    return problems


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc


def schema_errors(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
