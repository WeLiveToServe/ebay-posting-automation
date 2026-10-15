"""End-to-end flow with the LLM and eBay calls stubbed out."""

from __future__ import annotations

import csv
import io
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lister import export, llm, pipeline, pricing, publish
from lister.config import R2, Settings
from lister.models import (
    Aspect, Comp, DescriptionDraft, Identification, Item, Photo, PriceSuggestion, RatedComp, ResearchComp, Status,
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


class FakeEbay:
    """Stands in for the eBay Sell APIs; records every call."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.offers: dict[str, str] = {}

    def post(self, url, **kwargs):  # token endpoint
        assert kwargs["data"]["grant_type"] == "refresh_token"
        return httpx.Response(200, json={"access_token": "user-token", "expires_in": 7200},
                              request=httpx.Request("POST", url))

    def request(self, method, url, **kwargs):
        path = httpx.URL(url).path
        self.calls.append((method, path, kwargs.get("json")))
        req = httpx.Request(method, url)
        if path.endswith("_policy"):
            kind = path.rsplit("/", 1)[1].removesuffix("_policy")
            names = {"fulfillment": "USPS Media Mail", "payment": "Immediate payment managed via eBay",
                     "return": "Returns allowed within 30 days"}
            return httpx.Response(200, json={f"{kind}Policies": [{"name": names[kind], f"{kind}PolicyId": f"{kind}-1"}]}, request=req)
        if path == "/sell/inventory/v1/location/home":
            return httpx.Response(404 if method == "GET" else 204, request=req)
        if path.startswith("/sell/inventory/v1/inventory_item/"):
            return httpx.Response(204, request=req)
        if path == "/sell/inventory/v1/offer" and method == "GET":
            sku = kwargs["params"]["sku"]
            if sku in self.offers:
                return httpx.Response(200, json={"offers": [{"offerId": self.offers[sku]}]}, request=req)
            return httpx.Response(404, json={"errors": [{"errorId": 25713}]}, request=req)
        if path == "/sell/inventory/v1/offer" and method == "POST":
            self.offers[kwargs["json"]["sku"]] = "offer-1"
            return httpx.Response(201, json={"offerId": "offer-1"}, request=req)
        if path == "/sell/inventory/v1/offer/offer-1":
            return httpx.Response(204, request=req)
        if path == "/sell/inventory/v1/offer/offer-1/publish":
            return httpx.Response(200, json={"listingId": "1234567890"}, request=req)
        raise AssertionError(f"unexpected {method} {path}")


def test_publish_via_api(settings, monkeypatch):
    fake = FakeEbay()
    monkeypatch.setattr(httpx, "post", fake.post)
    monkeypatch.setattr(httpx, "request", fake.request)
    settings = replace(
        settings,
        seller=replace(settings.seller, shipping_profile="USPS Media Mail", payment_profile="Immediate payment managed via eBay",
                       return_profile="Returns allowed within 30 days", ebay_refresh_token="refresh"),
        pricing=replace(settings.pricing, ebay_client_id="id", ebay_client_secret="secret"),
        listing=replace(settings.listing, best_offer_auto_accept=0.9, best_offer_minimum=0.75),
    )
    store = Store(settings.db_path)
    item = store.create("BK")
    item.photos = [Photo(idx=1, original="o.jpg", derived="d.jpg", url="https://photos.example.com/1.jpg")]
    item.listing = pipeline.build_listing(Item(sku=item.sku, identification=IDENT, price=PRICE, description=DESC))
    item.listing.price = 22
    item.status = Status.APPROVED
    store.save(item)

    listed, problems = pipeline.publish_approved(settings, store)
    assert problems == {} and listed == {item.sku: "1234567890"}
    saved = store.get(item.sku)
    assert saved.status == Status.LISTED and saved.ebay_listing_id == "1234567890"

    sent = {(m, p): body for m, p, body in fake.calls}
    inv = sent[("PUT", f"/sell/inventory/v1/inventory_item/{item.sku}")]
    assert inv["condition"] == "USED_GOOD"  # IDENT condition 5000
    assert inv["product"]["imageUrls"] == ["https://photos.example.com/1.jpg"]
    assert inv["product"]["aspects"]["Author"] == ["William Shakespeare"]
    assert "Not An Aspect" not in inv["product"]["aspects"]
    offer = sent[("POST", "/sell/inventory/v1/offer")]
    assert offer["pricingSummary"]["price"] == {"value": "22.00", "currency": "USD"}
    assert offer["listingPolicies"]["fulfillmentPolicyId"] == "fulfillment-1"
    assert offer["listingPolicies"]["bestOfferTerms"]["autoAcceptPrice"]["value"] == "19.80"
    assert offer["categoryId"] == "261186" and offer["merchantLocationKey"] == "home"
    assert sent[("POST", "/sell/inventory/v1/location/home")]["location"]["address"]["city"] == "Newfields"

    # Publishing again revises the existing offer instead of creating a duplicate.
    saved.status = Status.APPROVED
    store.save(saved)
    fake.calls.clear()
    pipeline.publish_approved(settings, store)
    assert ("PUT", "/sell/inventory/v1/offer/offer-1") in {(m, p) for m, p, _ in fake.calls}
    assert ("POST", "/sell/inventory/v1/offer") not in {(m, p) for m, p, _ in fake.calls}


def test_publish_reports_missing_policy(settings, monkeypatch):
    fake = FakeEbay()
    monkeypatch.setattr(httpx, "post", fake.post)
    monkeypatch.setattr(httpx, "request", fake.request)
    settings = replace(settings, seller=replace(settings.seller, shipping_profile="Nope", ebay_refresh_token="r"),
                       pricing=replace(settings.pricing, ebay_client_id="id", ebay_client_secret="s"))
    client = publish.EbaySell(settings)
    with pytest.raises(publish.EbayError, match="No fulfillment policy named 'Nope'.*USPS Media Mail"):
        client.policy_ids()


def test_agent_mode_flow(settings, monkeypatch):
    """A session saves identify/price/describe JSON; the result matches the API path."""
    from lister import agent

    monkeypatch.setattr(pricing, "active_comps", lambda s, i: [
        Comp(source="ebay_active", title="Arden MND", price=13.0, shipping=4.0, url="https://www.ebay.com/itm/2"),
    ])
    store = Store(settings.db_path)
    item = store.create("BK")
    item.photos = [Photo(idx=1, original="o.jpg", derived="d.jpg", url="https://photos.example.com/1.jpg")]
    item.notes = "All plates present."
    item.status = Status.QUEUED
    store.save(item)

    claimed = store.claim_next()
    assert agent.show(claimed)["photos"] == ["d.jpg"]
    assert "All plates present." in agent.guide("price", claimed)
    assert agent.schema("identify")["type"] == "object"

    agent.save(claimed, "identify", IDENT.model_dump())
    assert "Not An Aspect" not in [a.name for a in claimed.identification.aspects]
    comps = agent.fetch_comps(settings, claimed)
    assert comps[0]["index"] == 0 and comps[0]["price"] == 13.0
    agent.save(claimed, "price", {**PRICE.model_dump(), "research_notes": "One SOLD at $11."})
    problems = agent.save(claimed, "describe", DESC.model_dump())
    store.save(claimed)

    done = store.get(item.sku)
    assert done.status == Status.REVIEW and problems == []
    assert done.listing.price == 12.5 and done.listing.title.startswith("A Midsummer")
    assert done.research_summary == "One SOLD at $11."
    assert {c.source: c.relevance for c in done.comps} == {"ebay_sold": "strong", "ebay_active": "strong"}

    agent.save(done, "listing", {"price": 295})
    assert done.listing.price == 295 and done.listing.title.startswith("A Midsummer")
    with pytest.raises(Exception):
        agent.save(done, "price", {"list_price": "not a number"})


def test_account_reprice_moves_best_offer(settings, monkeypatch):
    from lister import account

    sent = []

    def fake_post(url, **kwargs):
        if "oauth2/token" in url:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 7200}, request=httpx.Request("POST", url))
        sent.append((kwargs["headers"]["X-EBAY-API-CALL-NAME"], kwargs["content"].decode()))
        return httpx.Response(200, text="<Ack>Warning</Ack>", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    settings = replace(settings, seller=replace(settings.seller, ebay_refresh_token="r"),
                       pricing=replace(settings.pricing, ebay_client_id="id", ebay_client_secret="s"),
                       listing=replace(settings.listing, best_offer_auto_accept=0.9, best_offer_minimum=0.75))
    account.EbayAccount(settings).reprice("123", 22)
    call, body = sent[0]
    assert call == "ReviseFixedPriceItem"
    assert "<StartPrice>22.00</StartPrice>" in body
    assert "<BestOfferAutoAcceptPrice>19.80</BestOfferAutoAcceptPrice>" in body
    assert "<MinimumBestOfferPrice>16.50</MinimumBestOfferPrice>" in body
