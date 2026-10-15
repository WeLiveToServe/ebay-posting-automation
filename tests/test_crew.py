"""Multi-agent runs: code-enforced rules, assembly, briefs, corrections and the preview."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from lister import assemble, cli, crew, photos, preview
from lister.config import Settings
from lister.crew import EditionResult, PhotoLabel
from lister.models import Status
from lister.store import Store
from lister.web.app import create_app

THIS_YEAR = date.today().year


def edition_data(year=1919, n_photos=4, **over) -> dict:
    data = {
        "category": "antiquarian", "title": "Psmith in the City", "subtitle": None, "author": "P. G. Wodehouse",
        "illustrator": "T. M. R. Whitwell", "illustrated": True, "cover_illustrated": True,
        "publisher": "A. & C. Black Ltd.", "place": "London", "year": year, "year_certain": True,
        "first_published": 1910, "edition": "Second edition, reissue", "edition_short": "2nd Edition",
        "issue_points": [{"point": "12 plates by Whitwell", "present": True}],
        "volumes": 1, "binding": "Pictorial cloth", "language": "English",
        "aspects": [{"name": "Author", "value": "P. G. Wodehouse"}, {"name": "Bogus", "value": "x"}],
        "sources": [
            {"url": "https://www.abebooks.com/1", "what": "dealer copy", "images_compared": ["cover", "title page"], "matches": True},
            {"url": "https://www.biblio.com/2", "what": "dealer copy", "images_compared": ["spine"], "matches": True},
        ],
        "edition_confirmed": True,
        "photos": [{"idx": i, "role": r} for i, r in
                   zip(range(1, n_photos + 1), ["angle", "front_cover", "title_page", "spine", "plate", "back_cover"])],
    }
    return {**data, **over}


CONDITION = {"grade": "Near Fine", "summary": "A bright, tight copy; the corners show light wear.", "defects": []}


def pricing_data(older=True, **over) -> dict:
    sources = crew.REQUIRED_SOURCES[older]
    comps = [{"source": "ebay_sold", "url": f"https://www.ebay.com/itm/{n}", "status": "sold", "date": "2026-09-01",
              "price": 200 + n, "edition": "1919 A&C Black", "condition": "VG", "match": "same"} for n in range(5)]
    data = {
        "suggested": 250, "low": 200, "high": 300, "confidence": "medium", "reason": "Five sold copies, $200-204.",
        "provisional": False, "comps": comps,
        "sources_checked": [{"source": s, "url": f"https://example.com/{s}", "found": 1} for s in sources],
        "ebay_history": "90-day sold only", "questions": [],
    }
    return {**data, **over}


DESCRIPTION = {"fame": "lesser_known", "text": "An underappreciated gem from early Wodehouse.",
               "collector_line": "Perfect for a Wodehouse collector."}


def jpeg(color="red") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (400, 300), color).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path)


@pytest.fixture
def store(settings) -> Store:
    return Store(settings.db_path)


def make_item(settings, store, n_photos=4):
    item = store.create("BK")
    for n in range(n_photos):
        photos.add_photo(settings, item, f"{n}.jpg", jpeg(["red", "blue", "green", "gray", "white", "black"][n]))
    item.notes = "All 12 plates present."
    return store.save(item)


def full_run(item, edition=None, pricing=None, condition=None):
    for role, data in (("edition", edition or edition_data()), ("condition", condition or CONDITION),
                       ("pricing", pricing or pricing_data()), ("description", DESCRIPTION)):
        assert crew.save(item, role, data) == [], role
    return assemble.assemble(item)


# --- Age and grade rules -----------------------------------------------------------


def test_age_cutoff_is_40_years():
    assert crew.is_older(THIS_YEAR - 40) and not crew.is_older(THIS_YEAR - 39)


def test_grade_rules():
    assert crew.grade_problems("Like New", older=False) == []
    assert crew.grade_problems("Like New", older=True)  # never Like New when older
    assert crew.grade_problems("New", older=False) and crew.grade_problems("Brand New", older=True)
    assert crew.grade_problems("Near Fine", older=False)  # collector terms are for older books
    assert crew.grade_problems("Acceptable", older=True)  # eBay terms are for newer books
    assert crew.grade_problems("Fine", older=True) == []


def test_condition_mapping_for_older_books():
    ids = {g: crew.ebay_condition_id(g) for g in crew.OLDER_GRADES}
    assert ids == {"Fine": "4000", "Near Fine": "4000", "Very Good": "4000", "Good": "5000",
                   "Fair": "6000", "Poor": "6000"}
    assert "1000" not in {crew.ebay_condition_id(g) for g in crew.NEWER_GRADES + crew.OLDER_GRADES}


def test_condition_save_rejects_bad_grade_and_long_note(settings, store):
    item = make_item(settings, store)
    crew.save(item, "edition", edition_data())
    problems = crew.save(item, "condition", {"grade": "Like New", "summary": "One. Two. Three."})
    assert any("Like New" in p for p in problems) and any("3 sentences" in p for p in problems)


# --- Title -------------------------------------------------------------------------


def test_title_older_and_newer():
    older = EditionResult.model_validate(edition_data())
    assert assemble.build_title(older, True) == "1919 2nd Edition A. & C. Black Ltd. P. G. Wodehouse Psmith in the City"
    newer = EditionResult.model_validate(edition_data(year=2015, edition_short=None))
    assert assemble.build_title(newer, False) == "P. G. Wodehouse Psmith in the City"


def test_title_drops_parts_in_order_and_never_exceeds_80():
    long = EditionResult.model_validate(edition_data(
        subtitle="Being a Chronicle of Banking Life", publisher="Adam and Charles Black Publishing Company Ltd."))
    title = assemble.build_title(long, True)
    assert len(title) <= 80 and "Chronicle" not in title and title.endswith("Psmith in the City")
    assert title.startswith("1919") and "Wodehouse" in title
    huge = EditionResult.model_validate(edition_data(title="A " + "Very " * 30 + "Long Title"))
    assert len(assemble.build_title(huge, True)) <= 80


def test_title_marks_sets():
    newer = EditionResult.model_validate(edition_data(year=2021, volumes=6, title="Thrawn Series", author="Timothy Zahn"))
    assert assemble.build_title(newer, False) == "Timothy Zahn Thrawn Series Set of 6"


def test_title_skips_placeholder_author():
    anthology = EditionResult.model_validate(edition_data(author="Various", edition_short=None, title="Persian Literature"))
    assert assemble.build_title(anthology, True) == "1919 A. & C. Black Ltd. Persian Literature"


def test_compact_publisher():
    assert assemble.compact_publisher("A. & C. Black Ltd.") == "A. & C. Black"
    assert assemble.compact_publisher("Grosset & Dunlap Publishers") == "Grosset & Dunlap"


# --- Photo order -------------------------------------------------------------------


def labels(*pairs):
    return [PhotoLabel(idx=i, role=r, volume=v) for i, (r, v) in enumerate(pairs, start=1)]


def test_photo_order_illustrated_cover_leads():
    ls = labels(("angle", None), ("spine", None), ("title_page", None), ("front_cover", None),
                ("plate", None), ("back_cover", None), ("defect", None), ("copyright_page", None))
    assert assemble.photo_order(ls, cover_illustrated=True) == [4, 2, 6, 3, 8, 5, 7, 1]


def test_photo_order_plain_cover_puts_spine_first():
    ls = labels(("front_cover", None), ("spine", None), ("angle", None))
    assert assemble.photo_order(ls, cover_illustrated=False) == [2, 1, 3]


def test_photo_order_groups_title_pages_by_volume():
    ls = labels(("copyright_page", 2), ("title_page", 2), ("copyright_page", 1), ("title_page", 1),
                ("spine", 2), ("spine", 1), ("angle", 1))
    assert assemble.photo_order(ls, cover_illustrated=False) == [6, 5, 4, 3, 2, 1, 7]


# --- Schemas and role checks -------------------------------------------------------


def test_schema_validation_rejects_malformed_output(settings, store):
    item = make_item(settings, store)
    with pytest.raises(ValidationError):
        crew.save(item, "edition", {"title": "missing everything else"})
    with pytest.raises(ValidationError):
        crew.save(item, "edition", edition_data(photos=[{"idx": 1, "role": "dust_jacket_flap"}]))


def test_edition_checks_labels_and_sources(settings, store):
    item = make_item(settings, store)
    problems = crew.save(item, "edition", edition_data(n_photos=3, sources=[]))
    assert any("not labelled: [4]" in p for p in problems)
    assert any("2 online sources" in p for p in problems)
    one_match = edition_data()
    one_match["sources"][1]["matches"] = False
    assert any("only 1 source" in p for p in crew.save(item, "edition", one_match))
    assert crew.save(item, "edition", {**one_match, "edition_confirmed": False}) == []
    newer = crew.save(item, "edition", edition_data(year=THIS_YEAR - 5, sources=[]))
    assert newer == []  # newer books don't need two sources


def test_pricing_checks(settings, store):
    item = make_item(settings, store)
    crew.save(item, "edition", edition_data())
    crew.save(item, "condition", CONDITION)
    data = pricing_data(sources_checked=[], suggested=500)
    data["comps"] = data["comps"][:2] + [{**data["comps"][0], "match": "nearby", "url": "n/a"}]
    problems = crew.save(item, "pricing", data)
    assert any("outside the low-high range" in p for p in problems)
    assert any("eBay comparables" in p for p in problems)
    assert any("vialibri" in p and "pba_galleries" in p for p in problems)
    assert any("nearby-edition" in p for p in problems)
    assert any("URL" in p for p in problems)
    newer = pricing_data(older=False)
    item2 = make_item(settings, store)
    crew.save(item2, "edition", edition_data(year=THIS_YEAR - 3, sources=[]))
    crew.save(item2, "condition", {"grade": "Very Good", "summary": "Clean copy."})
    assert crew.save(item2, "pricing", newer) == []


def test_description_needs_collector_line_when_older(settings, store):
    item = make_item(settings, store)
    crew.save(item, "edition", edition_data())
    problems = crew.save(item, "description", {**DESCRIPTION, "collector_line": None, "text": "Worth $300."})
    assert any("collector line" in p for p in problems) and any("price" in p for p in problems)


# --- Briefs, corrections, staleness -------------------------------------------------


def test_brief_carries_prompt_given_facts_and_corrections(settings, store, tmp_path):
    item = make_item(settings, store)
    with pytest.raises(ValueError):
        crew.brief(item, "pricing", tmp_path / "p.json")  # needs edition and condition first
    first = crew.brief(item, "edition", tmp_path / "e.json")
    assert crew.role_prompt("edition").strip() in first and "All 12 plates present." in first
    assert all(p.derived in first for p in item.photos)
    crew.save(item, "edition", edition_data())
    crew.save(item, "condition", CONDITION)
    text = crew.brief(item, "pricing", tmp_path / "p.json", ebay_username="me123")
    assert "OLDER" in text and "abebooks_uk" in text and "me123" in text and '"grade": "Near Fine"' in text
    crew.correct(item, "condition", "Photo 1 shows a torn spine; grade it Good.")
    again = crew.brief(item, "condition", tmp_path / "c.json")
    assert "Orchestrator corrections" in again and "torn spine" in again and "previous output" in again
    assert again.index(crew.role_prompt("condition").strip()) < again.index("torn spine")


def test_resaving_a_role_makes_dependants_stale(settings, store):
    item = make_item(settings, store)
    full_run(item)
    crew.save(item, "edition", edition_data())
    assert [r for r, s in item.crew.roles.items() if s.stale] == ["condition", "pricing", "description"]
    assert item.crew.assembled is None


# --- Assembly ------------------------------------------------------------------------


def test_assemble_builds_a_clean_listing(settings, store):
    item = make_item(settings, store)
    listing = full_run(item)
    assert listing.problems == []
    assert listing.condition_id == "4000" and listing.grade == "Near Fine"
    assert listing.photo_order == [2, 4, 3, 1]  # cover, spine, title page, angle last
    assert "Bogus" not in listing.aspects and listing.aspects["Author"] == "P. G. Wodehouse"
    assert "Condition: Near Fine." in listing.description_html
    assert listing.description_html.endswith("<p>Perfect for a Wodehouse collector.</p>")


def test_assemble_reports_stale_and_questions(settings, store):
    item = make_item(settings, store)
    nearby = pricing_data(provisional=True, questions=["Weight the 1910 firsts?"])
    nearby["comps"].append({**nearby["comps"][0], "match": "nearby", "price": 900})
    full_run(item, pricing=nearby)
    crew.save(item, "condition", CONDITION)
    listing = assemble.assemble(item)
    assert any("stale" in p for p in listing.problems)
    assert "pricing: Weight the 1910 firsts?" in listing.questions
    assert any("provisional" in q for q in listing.questions)


def test_signoff_applies_new_items_but_never_live_ones(settings, store, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    item = make_item(settings, store)
    full_run(item)
    store.save(item)
    cli.main(["run", "signoff", item.sku])
    fresh = store.get(item.sku)
    assert fresh.status == Status.REVIEW and fresh.listing.title.startswith("1919")
    assert [p.idx for p in fresh.photos] == [2, 4, 3, 1]

    live = make_item(settings, store)
    live.status, live.ebay_listing_id = Status.LISTED, "177412641967"
    live.listing.title = "Live title"
    full_run(live)
    store.save(live)
    cli.main(["run", "signoff", live.sku])
    after = store.get(live.sku)
    assert after.listing.title == "Live title" and after.status == Status.LISTED and after.crew.signed_off_at
    assert "NOT changed" in capsys.readouterr().out


def test_cli_run_flow(settings, store, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    item = make_item(settings, store)
    item.status = Status.QUEUED
    store.save(item)
    cli.main(["run", "start"])
    started = json.loads(capsys.readouterr().out)
    assert started["sku"] == item.sku and len(started["photos"]) == 4
    folder = settings.data_dir / "runs" / item.sku
    for role, data in (("edition", edition_data()), ("condition", CONDITION), ("pricing", pricing_data()),
                       ("description", DESCRIPTION)):
        cli.main(["run", "brief", item.sku, role])
        assert (folder / f"{role}.brief.md").exists()
        (folder / f"{role}.json").write_text(json.dumps(data), encoding="utf-8")
        cli.main(["run", "save", item.sku, role])
        assert json.loads(capsys.readouterr().out.split("\n", 1)[1])["problems"] == []
    (folder / "condition.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        cli.main(["run", "save", item.sku, "condition"])
    assert "schema_errors" in capsys.readouterr().out
    cli.main(["run", "assemble", item.sku])
    assert json.loads(capsys.readouterr().out)["problems"] == []
    assert (settings.data_dir / "previews" / "listings.html").exists()


# --- Preview -------------------------------------------------------------------------

LIVE = [
    {"item_id": "177500233705", "title": "Titus Andronicus Arden Shakespeare", "price": 22.0, "sku": "",
     "category_id": "261186", "condition_id": "4000", "description_html": "<p>Live <b>text</b></p>",
     "aspects": {"Author": "William Shakespeare"}, "photo_urls": ["https://i.ebayimg.com/a.jpg"],
     "watchers": 1, "started": "2025-09-14"},
    {"item_id": "177512441058", "title": "Tascam TH-03", "price": 22.0, "sku": "", "category_id": "x",
     "condition_id": "", "description_html": "", "aspects": {}, "photo_urls": [], "watchers": 0, "started": ""},
]


def test_preview_has_every_listing_and_the_menu(settings, store):
    first, second = make_item(settings, store), make_item(settings, store, n_photos=3)
    full_run(first)
    full_run(second, edition=edition_data(year=THIS_YEAR - 2, n_photos=3, sources=[], title="Newer Book"),
             pricing=pricing_data(older=False), condition={"grade": "Very Good", "summary": "Clean."})
    store.save(first), store.save(second)
    preview.live_path(settings).parent.mkdir(parents=True, exist_ok=True)
    preview.live_path(settings).write_text(json.dumps(LIVE), encoding="utf-8")

    html = preview.build(settings, store).read_text(encoding="utf-8")
    assert 'id="menu"' in html and 'id="menu-btn"' in html
    for key in (first.sku, second.sku, "live-177500233705", "live-177512441058"):
        assert f'href="#{key}"' in html and f'id="{key}"' in html
    assert "New runs (2)" in html and "Live on eBay (2)" in html
    assert "../derived/" in html  # local photos, relative to the preview file
    assert "srcdoc=\"&lt;p&gt;Live &lt;b&gt;text" in html  # live descriptions are sandboxed
    article = html[html.index(f'id="{first.sku}"'):]
    order = [article.index(h) for h in ("<h1>", "<h4>Edition", "<h4>Condition", "<h4>Description",
                                        "<h4>Price", "<details class=\"photos\"")]
    assert order == sorted(order)


def test_preview_route_and_photos_served(settings, store):
    item = make_item(settings, store)
    full_run(item)
    store.save(item)
    client = TestClient(create_app(replace(settings), start_worker=False))
    page = client.get("/preview")
    assert page.status_code == 200 and item.sku in page.text
    src = page.text.split('<img src="', 1)[1].split('"', 1)[0]
    assert src.startswith("../derived/")
    assert client.get("/" + src.removeprefix("../")).status_code == 200
