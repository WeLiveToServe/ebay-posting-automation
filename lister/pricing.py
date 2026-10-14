"""Step 2: gather comparables and suggest a price.

Sources are pluggable (config [pricing].sources):
- ebay_active: current eBay listings via the Browse API. These are asking prices.
- web_research: Claude web search for sold/realized prices and dealer asking prices.

eBay no longer offers sold-listing data to new developers (Finding API retired,
Marketplace Insights restricted), so a dedicated sold-comps provider can be added
here later as another source returning `Comp(source="ebay_sold", ...)`.
"""

from __future__ import annotations

import statistics
import time

import httpx

from . import llm
from .config import Settings
from .models import Comp, Item, PriceSuggestion

EBAY_API = "https://api.ebay.com"
BOOKS_ROOT_CATEGORY = "267"  # Books & Magazines


class EbayBrowse:
    def __init__(self, settings: Settings):
        cfg = settings.pricing
        if not (cfg.ebay_client_id and cfg.ebay_client_secret):
            raise RuntimeError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET are not set")
        self.marketplace = cfg.ebay_marketplace
        self.limit = cfg.max_active_comps
        self._auth = (cfg.ebay_client_id, cfg.ebay_client_secret)
        self._token = ""
        self._expires = 0.0

    def _bearer(self) -> str:
        if time.time() < self._expires - 60:
            return self._token
        resp = httpx.post(
            f"{EBAY_API}/identity/v1/oauth2/token",
            auth=self._auth,
            data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        self._token, self._expires = body["access_token"], time.time() + body["expires_in"]
        return self._token

    def search(self, query: str = "", gtin: str = "") -> list[Comp]:
        params = {"limit": str(self.limit), "category_ids": BOOKS_ROOT_CATEGORY}
        if gtin:
            params["gtin"] = gtin
        if query:
            params["q"] = query
        resp = httpx.get(
            f"{EBAY_API}/buy/browse/v1/item_summary/search",
            params=params,
            headers={"Authorization": f"Bearer {self._bearer()}", "X-EBAY-C-MARKETPLACE-ID": self.marketplace},
            timeout=30,
        )
        resp.raise_for_status()
        comps = []
        for s in resp.json().get("itemSummaries", []):
            price = s.get("price") or s.get("currentBidPrice") or {}
            if price.get("currency", "USD") != "USD" or "value" not in price:
                continue
            ship = (s.get("shippingOptions") or [{}])[0].get("shippingCost", {}).get("value")
            comps.append(
                Comp(
                    source="ebay_active",
                    title=s.get("title", ""),
                    price=float(price["value"]),
                    shipping=float(ship) if ship is not None else None,
                    condition=s.get("condition"),
                    url=s.get("itemWebUrl"),
                    notes=", ".join(s.get("buyingOptions", [])) or None,
                )
            )
        return comps


def active_comps(settings: Settings, item: Item) -> list[Comp]:
    ident = item.identification
    browse = EbayBrowse(settings)
    found: dict[str, Comp] = {}
    if ident.isbn:
        for comp in browse.search(gtin=ident.isbn):
            found[comp.url or comp.title] = comp
    for comp in browse.search(query=ident.comps_query):
        found.setdefault(comp.url or comp.title, comp)
    return list(found.values())


RESEARCH_SYSTEM = """You research second-hand book prices for a seller. Prefer evidence of
what copies actually SOLD for (eBay sold listings, auction records such as Rare Book Hub,
Heritage, or PBA) over asking prices (AbeBooks, Biblio, BookFinder, Amazon). Match edition,
printing, binding, and presence of dust jacket as closely as possible, and say how each
comparable differs from the seller's copy. Report every price you found with its URL and
date, clearly labelled SOLD or ASKING. If you find little, say so; never invent figures."""


def web_research(settings: Settings, item: Item) -> str:
    ident = item.identification
    facts = {
        "title": ident.formal_title,
        "author": ident.creator,
        "isbn": ident.isbn,
        "publisher": ident.publisher,
        "year": ident.publication_year,
        "edition": ident.edition_statement,
        "binding": ident.binding,
        "condition_id": ident.condition_id,
        "notable": ident.notable_features,
    }
    prompt = f"Find recent sold and asking prices for this copy:\n{facts}"
    return llm.research(settings.models.price, RESEARCH_SYSTEM, prompt, effort="medium")


SUGGEST_SYSTEM = """You set fixed prices for a small eBay bookseller who wants items to sell
within roughly 60 days without leaving money on the table.

Weigh sold prices above asking prices; asking prices are ceilings, not market value.
Adjust for condition and edition differences between comparables and this copy, and
include shipping when comparing (buyers compare total cost). For common books with many
cheap copies, price near the low end of comparable listings. If evidence is thin or
conflicting, give a wider range, set confidence to low, and explain in `warnings`
what a human should check (for example, an unverified first printing)."""


def suggest(settings: Settings, item: Item, active: list[Comp], research: str) -> PriceSuggestion:
    active_lines = "\n".join(
        f"[{i}] ${c.price:.2f}"
        + (f" + ${c.shipping:.2f} ship" if c.shipping is not None else "")
        + f" | {c.condition or '?'} | {c.notes or ''} | {c.title}"
        for i, c in enumerate(active)
    )
    prompt = (
        f"Item:\n{item.identification.model_dump_json(indent=2)}\n\n"
        f"eBay active listings (asking prices):\n{active_lines or '(none found)'}\n\n"
        f"Web research notes:\n{research or '(not run)'}\n"
    )
    if item.cost is not None:
        prompt += f"\nSeller paid ${item.cost:.2f} for this copy.\n"
    return llm.parse(settings.models.price, SUGGEST_SYSTEM, prompt, PriceSuggestion, effort="high")


def price(settings: Settings, item: Item) -> None:
    """Run the configured sources and the suggestion; updates the item in place."""
    if item.identification is None:
        raise ValueError(f"{item.sku} must be identified before pricing")
    active: list[Comp] = []
    research = ""
    problems: list[str] = []
    for source in settings.pricing.sources:
        try:
            if source == "ebay_active":
                active = active_comps(settings, item)
            elif source == "web_research":
                research = web_research(settings, item)
            else:
                problems.append(f"Unknown pricing source {source!r}")
        except Exception as exc:  # keep pricing with whatever sources worked
            problems.append(f"{source} failed: {exc}")

    suggestion = suggest(settings, item, active, research)
    suggestion.warnings = problems + suggestion.warnings

    for rated in suggestion.rated_active:
        if 0 <= rated.index < len(active):
            active[rated.index].relevance = rated.relevance
            active[rated.index].notes = "; ".join(filter(None, [active[rated.index].notes, rated.note]))
    researched = [
        Comp(
            source="ebay_sold" if rc.kind == "sold" and rc.url and "ebay." in rc.url else "web",
            title=rc.title,
            price=rc.price,
            url=rc.url,
            sold_date=rc.date if rc.kind == "sold" else None,
            notes=f"{rc.kind.upper()}: {rc.note}",
            relevance=rc.relevance,
        )
        for rc in suggestion.research_comps
    ]
    item.comps = researched + active
    item.research_summary = research
    item.price = suggestion


def comp_stats(comps: list[Comp]) -> dict[str, dict[str, float | int]]:
    """Median total price (item + shipping) of strong/partial matches, sold and asking kept apart."""
    groups: dict[str, list[float]] = {"sold": [], "asking": []}
    for c in comps:
        if c.relevance not in ("strong", "partial"):
            continue
        sold = c.source == "ebay_sold" or (c.notes or "").startswith("SOLD")
        groups["sold" if sold else "asking"].append(c.price + (c.shipping or 0))
    return {
        name: {"count": len(v), "median": round(statistics.median(v), 2), "min": min(v), "max": max(v)}
        for name, v in groups.items()
        if v
    }
