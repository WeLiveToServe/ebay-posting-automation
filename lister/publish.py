"""Publish approved items straight to eBay with the Sell Inventory API (the CSV is the fallback).

Flow per item: inventory item (keyed by our SKU) -> offer (price, category, policies) -> publish.
Every call is idempotent per SKU, so re-publishing an item revises its live listing.
Needs a seller refresh token: run `python -m lister ebay-login` once (valid ~18 months).
"""

from __future__ import annotations

import time
import urllib.parse
from pathlib import Path

import httpx

from . import categories
from .config import ROOT, Settings
from .models import Item
from .pricing import EBAY_API

AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
SCOPES = tuple(
    "https://api.ebay.com/oauth/" + s
    for s in ("api_scope", "api_scope/sell.inventory", "api_scope/sell.account", "api_scope/sell.fulfillment")
)
# Our condition IDs -> Inventory API condition enums (book categories).
CONDITIONS = {
    "1000": "NEW",
    "2750": "LIKE_NEW",
    "4000": "USED_VERY_GOOD",
    "5000": "USED_GOOD",
    "6000": "USED_ACCEPTABLE",
}
FORMATS = {"FixedPrice": "FIXED_PRICE"}


class EbayError(RuntimeError):
    pass


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    try:
        errors = resp.json().get("errors", [])
        detail = "; ".join(f"{e.get('errorId')}: {e.get('longMessage') or e.get('message')}" for e in errors)
    except ValueError:
        detail = resp.text[:300]
    raise EbayError(f"eBay {resp.request.method} {resp.request.url.path} -> {resp.status_code}: {detail}")


def _money(value: float) -> dict[str, str]:
    return {"value": f"{value:.2f}", "currency": "USD"}


class EbaySell:
    def __init__(self, settings: Settings):
        cfg = settings.pricing
        if not (cfg.ebay_client_id and cfg.ebay_client_secret):
            raise RuntimeError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET are not set")
        if not settings.seller.ebay_refresh_token:
            raise RuntimeError("EBAY_REFRESH_TOKEN is not set; run `python -m lister ebay-login`")
        self.settings = settings
        self.marketplace = cfg.ebay_marketplace
        self._auth = (cfg.ebay_client_id, cfg.ebay_client_secret)
        self._token = ""
        self._expires = 0.0
        self._policies: dict[str, str] | None = None

    def _bearer(self) -> str:
        if time.time() < self._expires - 60:
            return self._token
        resp = httpx.post(
            f"{EBAY_API}/identity/v1/oauth2/token",
            auth=self._auth,
            data={"grant_type": "refresh_token", "refresh_token": self.settings.seller.ebay_refresh_token,
                  "scope": " ".join(SCOPES)},
            timeout=30,
        )
        _raise_for(resp)
        body = resp.json()
        self._token, self._expires = body["access_token"], time.time() + body["expires_in"]
        return self._token

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._bearer()}",
            "Content-Language": "en-US",
            "Accept-Language": "en-US",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
        }
        for attempt in range(4):  # eBay's Inventory API returns sporadic 500s (error 25001)
            resp = httpx.request(method, f"{EBAY_API}{path}", headers=headers, timeout=60, **kwargs)
            if resp.status_code < 500 or attempt == 3:
                return resp
            time.sleep(5 * (attempt + 1))
        return resp

    def policy_ids(self) -> dict[str, str]:
        """Look up the configured business policies by name."""
        if self._policies is None:
            seller = self.settings.seller
            wanted = {"fulfillment": seller.shipping_profile, "payment": seller.payment_profile,
                      "return": seller.return_profile}
            ids = {}
            for kind, name in wanted.items():
                resp = self.request("GET", f"/sell/account/v1/{kind}_policy", params={"marketplace_id": self.marketplace})
                _raise_for(resp)
                policies = resp.json().get(f"{kind}Policies", [])
                match = [p[f"{kind}PolicyId"] for p in policies if p["name"].strip() == name.strip()]
                if not match:
                    names = ", ".join(repr(p["name"]) for p in policies)
                    raise EbayError(f"No {kind} policy named {name!r} in config.toml [seller]; eBay has: {names}")
                ids[f"{kind}PolicyId"] = match[0]
            self._policies = ids
        return self._policies

    def ensure_location(self) -> str:
        seller = self.settings.seller
        key = seller.merchant_location_key
        resp = self.request("GET", f"/sell/inventory/v1/location/{key}")
        if resp.status_code == 200:
            return key
        if resp.status_code != 404:
            _raise_for(resp)
        city, _, state = (part.strip() for part in seller.location.partition(","))
        address = {"city": city, "stateOrProvince": state, "country": "US"}
        if seller.postal_code:
            address["postalCode"] = seller.postal_code
        resp = self.request("POST", f"/sell/inventory/v1/location/{key}", json={
            "location": {"address": address},
            "locationTypes": ["WAREHOUSE"],
            "name": seller.location,
            "merchantLocationStatus": "ENABLED",
        })
        _raise_for(resp)
        return key

    def inventory_item(self, item: Item) -> dict:
        listing = item.listing
        cat = categories.get(listing.category)
        product: dict = {
            "title": listing.title.strip(),
            "imageUrls": [p.url for p in item.photos if p.url],
            "aspects": {k: [v.strip()] for k, v in listing.aspects.items() if k in cat.aspects and v.strip()},
        }
        if listing.isbn:
            product["isbn"] = [listing.isbn]
        return {
            "availability": {"shipToLocationAvailability": {"quantity": 1}},
            "condition": CONDITIONS[listing.condition_id],
            "product": product,
        }

    def offer(self, item: Item, location_key: str) -> dict:
        listing, cfg = item.listing, self.settings.listing
        if cfg.format not in FORMATS:
            raise EbayError(f"Listing format {cfg.format!r} is not supported for API publishing")
        policies: dict = dict(self.policy_ids())
        if cfg.best_offer:
            terms: dict = {"bestOfferEnabled": True}
            if cfg.best_offer_auto_accept:
                terms["autoAcceptPrice"] = _money(listing.price * cfg.best_offer_auto_accept)
            if cfg.best_offer_minimum:
                terms["autoDeclinePrice"] = _money(listing.price * cfg.best_offer_minimum)
            policies["bestOfferTerms"] = terms
        return {
            "sku": item.sku,
            "marketplaceId": self.marketplace,
            "format": FORMATS[cfg.format],
            "availableQuantity": 1,
            "categoryId": categories.get(listing.category).id,
            "listingDescription": listing.description_html,
            "listingDuration": cfg.duration,
            "listingPolicies": policies,
            "pricingSummary": {"price": _money(listing.price)},
            "merchantLocationKey": location_key,
        }

    def publish(self, item: Item) -> str:
        """Create or update the item's listing; returns the eBay listing (item) ID."""
        location = self.ensure_location()
        sku = urllib.parse.quote(item.sku, safe="")
        _raise_for(self.request("PUT", f"/sell/inventory/v1/inventory_item/{sku}", json=self.inventory_item(item)))

        body = self.offer(item, location)
        resp = self.request("GET", "/sell/inventory/v1/offer", params={"sku": item.sku, "marketplace_id": self.marketplace})
        if resp.status_code != 404:  # 404 just means no offer exists for this SKU yet
            _raise_for(resp)
        offers = resp.json().get("offers", []) if resp.status_code == 200 else []
        if offers:
            offer_id = offers[0]["offerId"]
            _raise_for(self.request("PUT", f"/sell/inventory/v1/offer/{offer_id}", json=body))
        else:
            resp = self.request("POST", "/sell/inventory/v1/offer", json=body)
            _raise_for(resp)
            offer_id = resp.json()["offerId"]

        resp = self.request("POST", f"/sell/inventory/v1/offer/{offer_id}/publish")
        _raise_for(resp)
        return resp.json()["listingId"]


def listing_url(listing_id: str) -> str:
    return f"https://www.ebay.com/itm/{listing_id}"


def consent_url(settings: Settings) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": settings.pricing.ebay_client_id,
        "response_type": "code",
        "redirect_uri": settings.seller.ebay_runame,
        "scope": " ".join(SCOPES),
    })


def exchange_code(settings: Settings, redirected_url: str) -> str:
    """Turn the URL eBay redirected to after consent into a refresh token."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(redirected_url.strip()).query)
    if "code" not in query:
        raise EbayError("That URL has no ?code= parameter; copy the full address after clicking Agree")
    resp = httpx.post(
        f"{EBAY_API}/identity/v1/oauth2/token",
        auth=(settings.pricing.ebay_client_id, settings.pricing.ebay_client_secret),
        data={"grant_type": "authorization_code", "code": query["code"][0], "redirect_uri": settings.seller.ebay_runame},
        timeout=30,
    )
    _raise_for(resp)
    return resp.json()["refresh_token"]


def save_env(values: dict[str, str], path: Path = ROOT / ".env") -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [line for line in lines if line.split("=", 1)[0] not in values]
    lines += [f"{k}={v}" for k, v in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
