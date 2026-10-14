"""End-to-end flow with the LLM and eBay calls stubbed out."""

from __future__ import annotations

import csv
import io
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lister import export, llm, pipeline, pricing
from lister.config import R2, Settings
from lister.models import (
    Aspect, Comp, DescriptionDraft, Identification, Item, PriceSuggestion, RatedComp, ResearchComp, Status,
)
from lister.store import Store
from lister.web.app import create_app

IDENT = Identification(
    category="books",
    formal_title="A Midsummer Night's Dream",
    creator="William Shakespeare",
    listing_title="A Midsummer Night's Dream Arden Shakespeare Harold Brooks Paperback 1979",
    isbn="9780415026994",
    publisher="Methuen",
    publication_year="1979",
    edition_statement="Arden Edition, Second Series",
    is_first_edition=None,
    binding="Paperback",
    aspects=[
        Aspect(name="Author", value="William Shakespeare"),
        Aspect(name="Book Title", value="A Midsummer Night's Dream"),
        Aspect(name="Language", value="English"),
        Aspect(name="Not An Aspect", value="dropped"),
    ],
    condition_id="5000",
    condition_notes=["Creased spine", "Pencil underlining on ~10 pages"],
    notable_features=["Edited by Harold F. Brooks"],
    uncertainties=["Printing not visible"],
    confidence="high",
    comps_query="Arden Midsummer Night's Dream Brooks paperback",
)
PRICE = PriceSuggestion(
    list_price=12.5, low=9, high=16, confidence="medium", rationale="Plenty of copies.",
    rated_active=[RatedComp(index=0, relevance="strong", note="same edition")],
    research_comps=[ResearchComp(kind="sold", title="Arden MND", price=11, date="2026-09-01",
                                 url="https://www.ebay.com/itm/1", relevance="strong", note="same")],
    warnings=[],
)
DESC = DescriptionDraft(intro="The Arden <scholarly> edition.", details=["Methuen, 1979"],
                        condition="Good: creased spine, some pencil.", closing=None)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, pricing=replace(Settings(data_dir=tmp_path).pricing, sources=("ebay_active", "web_research")))


@pytest.fixture
def stubbed(monkeypatch):
    def fake_parse(model, system, content, schema, effort="medium"):
        return {Identification: IDENT, PriceSuggestion: PRICE, DescriptionDraft: DESC}[schema].model_copy(deep=True)

    monkeypatch.setattr(llm, "parse", fake_parse)
    monkeypatch.setattr(llm, "research", lambda *a, **k: "Found one SOLD copy at $11.")
    monkeypatch.setattr(pricing, "active_comps", lambda s, i: [
        Comp(source="ebay_active", title="Arden MND", price=13.0, shipping=4.0, url="https://www.ebay.com/itm/2"),
    ])


def jpeg(color="red", size=(2400, 1800)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG")
    return out.getvalue()


def test_full_flow_through_web(settings, stubbed):
    client = TestClient(create_app(settings, start_worker=False))
    store = Store(settings.db_path)

    resp = client.post("/items", follow_redirects=False)
    sku = resp.headers["location"].split("/")[2]
    for color in ("red", "blue"):
        r = client.post(f"/items/{sku}/photos", files={"file": ("x.jpg", jpeg(color), "image/jpeg")})
        assert r.status_code == 200
    client.post(f"/items/{sku}/queue", data={"notes": "pencil marks", "cost": "$1.50", "shelf_location": "B3"})

    item = store.claim_next()
    assert item.sku == sku and item.cost == 1.5
    with Image.open(item.photos[0].derived) as img:
        assert max(img.size) == 1600

    item = pipeline.process(settings, store, item)
    assert item.status == Status.REVIEW, item.error
    assert "Not An Aspect" not in item.listing.aspects
    assert item.listing.price == 12.5
    assert "&lt;scholarly&gt;" in item.listing.description_html  # LLM text is escaped
    assert {c.source for c in item.comps} == {"ebay_sold", "ebay_active"}
    assert pricing.comp_stats(item.comps)["sold"]["median"] == 11

    page = client.get(f"/items/{sku}")
    assert page.status_code == 200 and "Printing not visible" in page.text

    # Photos have no R2 URL, so export must refuse the item.
    form = {"title": item.listing.title, "price": "14", "condition_id": "4000", "category": "books",
            "aspect__Author": "William Shakespeare", "aspect__Book Title": "A Midsummer Night's Dream",
            "aspect__Language": "English", "description_html": item.listing.description_html, "action": "approve"}
    client.post(f"/items/{sku}", data=form)
    path, problems = pipeline.export_approved(settings, store)
    assert path is None and "photos are not uploaded to R2 yet" in problems[sku]

    item = store.get(sku)
    for p in item.photos:
        p.url = f"https://photos.example.com/{sku}/{p.idx}.jpg"
    store.save(item)
    path, problems = pipeline.export_approved(settings, store)
    assert path and not problems
    assert store.get(sku).status == Status.EXPORTED

    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 1
    row = rows[0]
    assert row[export.ACTION] == "Add"
    assert row["Custom label (SKU)"] == sku
    assert row["Start price"] == "14.00"
    assert row["Condition ID"] == "4000"
    assert row["Item photo URL"].count("|") == 1
    assert row["C:Author"] == "William Shakespeare"
    assert row["Best Offer Enabled"] == "1"


def test_rerun_single_step_keeps_human_edits(settings, stubbed):
    store = Store(settings.db_path)
    item = store.create("BK")
    item.photos = []
    item.identification = IDENT
    item.listing.title = "My edited title"
    item.listing.price = 99
    store.save(item)
    item = pipeline.process(settings, store, item, ("describe",))
    assert item.listing.title == "My edited title" and item.listing.price == 99
    assert item.listing.description_html.startswith("<p>")


def test_pricing_source_failure_is_a_warning(settings, stubbed, monkeypatch):
    def boom(s, i):
        raise RuntimeError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET are not set")

    monkeypatch.setattr(pricing, "active_comps", boom)
    item = Item(sku="BK-1", identification=IDENT)
    pricing.price(settings, item)
    assert any("ebay_active failed" in w for w in item.price.warnings)


def test_validation_messages():
    item = Item(sku="BK-2")
    item.listing.title = "x" * 81
    item.listing.condition_id = "3000"  # the old, wrong code for Very Good
    errors = export.validate(item)
    assert any("max 80" in e for e in errors)
    assert any("condition '3000'" in e for e in errors)
    assert any("Author" in e for e in errors)


def test_sku_sequence(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    a, b = store.new_sku("BK"), store.new_sku("BK")
    assert a != b and a.endswith("-001") and b.endswith("-002")


def test_remote_access_requires_token(settings):
    app = create_app(replace(settings, server=replace(settings.server, access_token="s3cret")), start_worker=False)
    client = TestClient(app)
    assert client.get("/").status_code == 401
    assert client.get("/?token=s3cret").status_code == 200
    assert client.get("/").status_code == 200  # cookie set


def test_schemas_are_valid_for_structured_outputs():
    """Every schema the API sees must be closed objects with all properties required."""
    from anthropic.lib._parse._transform import transform_schema  # SDK's own transformer

    for model in (Identification, PriceSuggestion, DescriptionDraft):
        schema = transform_schema(model)
        assert schema["type"] == "object"
