"""Manage the seller's live eBay listings, including ones the app did not create.

Listings made in Seller Hub or by CSV are only reachable through the Trading API (XML), so
pricing and ending them goes through here. `adopt` converts one to an app-managed listing:
it gives the listing our SKU, migrates it to the Inventory API, and imports its photos, so
from then on it is revised by re-publishing like any app-created item.
"""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import httpx

from . import photos
from .categories import CATEGORIES
from .config import Settings
from .models import Item, Status
from .publish import EbayError, EbaySell
from .store import Store

TRADING_URL = "https://api.ebay.com/ws/api.dll"
COMPAT_LEVEL = "1193"


@dataclass
class Listing:
    item_id: str
    title: str
    price: float
    sku: str
    watchers: int
    views: int
    started: str
    url: str

    def to_dict(self) -> dict:
        return asdict(self)


def _tag(xml: str, tag: str) -> str:
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", xml, re.S)
    return html.unescape(match.group(1)) if match else ""


class EbayAccount:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.sell = EbaySell(settings)

    def trading(self, call: str, body: str) -> str:
        """One Trading API call; raises EbayError on failure, returns the response XML."""
        request = (f'<?xml version="1.0" encoding="utf-8"?><{call}Request xmlns="urn:ebay:apis:eBLBaseComponents">'
                   f"{body}</{call}Request>")
        resp = httpx.post(TRADING_URL, content=request.encode(), timeout=60, headers={
            "X-EBAY-API-IAF-TOKEN": self.sell._bearer(),
            "X-EBAY-API-CALL-NAME": call,
            "X-EBAY-API-SITEID": "0",
            "X-EBAY-API-COMPATIBILITY-LEVEL": COMPAT_LEVEL,
            "Content-Type": "text/xml",
        })
        xml = resp.text
        errors = [_tag(e, "LongMessage") for e in re.findall(r"<Errors>(.*?)</Errors>", xml, re.S)
                  if "<SeverityCode>Error" in e]
        if errors or _tag(xml, "Ack") == "Failure":
            raise EbayError(f"eBay {call}: {'; '.join(errors) or xml[:300]}")
        return xml

    def active_listings(self) -> list[Listing]:
        listings, page = [], 1
        while True:
            xml = self.trading("GetMyeBaySelling", (
                "<ActiveList><Include>true</Include><Pagination><EntriesPerPage>200</EntriesPerPage>"
                f"<PageNumber>{page}</PageNumber></Pagination></ActiveList><DetailLevel>ReturnAll</DetailLevel>"))
            now = datetime.now(timezone.utc).isoformat()
            for block in re.findall(r"<Item>(.*?)</Item>", xml, re.S):
                if _tag(block, "EndTime") and _tag(block, "EndTime") < now:
                    continue  # with ReturnAll, eBay also returns just-ended (sold/ended) items
                item_id = _tag(block, "ItemID")
                listings.append(Listing(
                    item_id=item_id,
                    title=_tag(block, "Title"),
                    price=float(_tag(block, "CurrentPrice") or 0),
                    sku=_tag(block, "SKU"),
                    watchers=int(_tag(block, "WatchCount") or 0),
                    views=int(_tag(block, "HitCount") or 0),
                    started=_tag(block, "StartTime")[:10],
                    url=f"https://www.ebay.com/itm/{item_id}",
                ))
            if page >= int(_tag(xml, "TotalNumberOfPages") or 1):
                return listings
            page += 1

    def get_item(self, item_id: str) -> dict:
        """Title, price, description, item specifics, condition and photo URLs of a live listing."""
        xml = self.trading("GetItem", f"<ItemID>{item_id}</ItemID><DetailLevel>ReturnAll</DetailLevel>"
                                      "<IncludeItemSpecifics>true</IncludeItemSpecifics>")
        return {
            "item_id": item_id,
            "title": _tag(xml, "Title"),
            "sku": _tag(xml, "SKU"),
            "price": float(_tag(xml, "CurrentPrice") or 0),
            "category_id": _tag(_tag(xml, "PrimaryCategory"), "CategoryID"),
            "condition_id": _tag(xml, "ConditionID"),
            "description_html": _tag(xml, "Description"),
            "aspects": {html.unescape(n): html.unescape(v) for n, v in
                        re.findall(r"<NameValueList><Name>(.*?)</Name><Value>(.*?)</Value>", xml)},
            "photo_urls": [html.unescape(u) for u in re.findall(r"<PictureURL>(.*?)</PictureURL>", xml)],
            "status": _tag(xml, "ListingStatus"),
        }

    def reprice(self, item_id: str, price: float) -> None:
        """Change the price, moving Best Offer thresholds with it (eBay rejects stale ones)."""
        cfg = self.settings.listing
        offer = ""
        if cfg.best_offer and (cfg.best_offer_auto_accept or cfg.best_offer_minimum):
            offer = "<ListingDetails>"
            if cfg.best_offer_auto_accept:
                offer += f"<BestOfferAutoAcceptPrice>{price * cfg.best_offer_auto_accept:.2f}</BestOfferAutoAcceptPrice>"
            if cfg.best_offer_minimum:
                offer += f"<MinimumBestOfferPrice>{price * cfg.best_offer_minimum:.2f}</MinimumBestOfferPrice>"
            offer += "</ListingDetails>"
        self.trading("ReviseFixedPriceItem",
                     f"<Item><ItemID>{item_id}</ItemID><StartPrice>{price:.2f}</StartPrice>{offer}</Item>")

    def end(self, item_id: str, reason: str = "NotAvailable") -> None:
        self.trading("EndFixedPriceItem", f"<ItemID>{item_id}</ItemID><EndingReason>{reason}</EndingReason>")

    def adopt(self, store: Store, item_id: str) -> Item:
        """Bring a live listing under app control and create (or refresh) its local item."""
        live = self.get_item(item_id)
        item = next((i for i in store.list() if i.ebay_listing_id == item_id), None)
        if item is None and live["sku"]:
            item = store.get(live["sku"]) or store.save(Item(sku=live["sku"]))
        if item is None:
            item = store.create(self.settings.listing.sku_prefix)
        if live["sku"] != item.sku:  # migration requires the listing to carry a SKU
            self.trading("ReviseFixedPriceItem", f"<Item><ItemID>{item_id}</ItemID><SKU>{item.sku}</SKU></Item>")

        offers = self.sell.request("GET", "/sell/inventory/v1/offer",
                                   params={"sku": item.sku, "marketplace_id": self.sell.marketplace})
        if offers.status_code != 200:  # not yet under Inventory API control
            resp = self.sell.request("POST", "/sell/inventory/v1/bulk_migrate_listing",
                                     json={"requests": [{"listingId": item_id}]})
            result = (resp.json().get("responses") or [{}])[0]
            if result.get("statusCode") != 200:
                raise EbayError(f"Could not migrate {item_id}: {result.get('errors') or resp.text[:300]}")

        if not item.photos:
            for n, url in enumerate(live["photo_urls"], start=1):
                data = httpx.get(re.sub(r"s-l\d+", "s-l1600", url), timeout=60, follow_redirects=True).content
                photos.add_photo(self.settings, item, f"{n:02d}.jpg", data)
        cat_key = next((c.key for c in CATEGORIES.values() if c.id == live["category_id"]), item.listing.category)
        item.listing = item.listing.model_copy(update={
            "category": cat_key,
            "title": live["title"],
            "price": live["price"],
            "condition_id": live["condition_id"] or item.listing.condition_id,
            "aspects": live["aspects"] or item.listing.aspects,
            "description_html": live["description_html"],
        })
        item.ebay_listing_id = item_id
        item.status = Status.LISTED
        return store.save(item)
