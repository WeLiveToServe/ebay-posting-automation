"""Runs the steps for an item and keeps its status/listing fields consistent."""

from __future__ import annotations

import logging
import threading
import traceback
from pathlib import Path

from . import describe, export, identify, pricing, publish
from .config import Settings
from .models import Item, ListingFields, Status
from .photos import R2Publisher
from .store import Store

log = logging.getLogger("lister")

STEPS = ("photos", "identify", "price", "describe")


def build_listing(item: Item) -> ListingFields:
    ident = item.identification
    listing = ListingFields()
    if ident:
        listing.category = ident.category
        listing.title = ident.listing_title[:80]
        listing.condition_id = ident.condition_id
        listing.isbn = ident.isbn or ""
        listing.aspects = {a.name: a.value for a in ident.aspects}
    if item.price:
        listing.price = round(item.price.list_price, 2)
    if item.description:
        listing.description_html = describe.render_html(item.description)
    return listing


def refresh_listing(item: Item, steps: tuple[str, ...]) -> None:
    """Update the editable listing from new drafts; re-running only some steps keeps the
    human's edits to the fields those steps don't produce."""
    fresh = build_listing(item)
    if set(steps) >= {"identify", "price", "describe"} or not item.listing.title:
        item.listing = fresh
        return
    if "identify" in steps:
        item.listing = fresh.model_copy(update={"price": item.listing.price, "description_html": item.listing.description_html})
    if "price" in steps:
        item.listing.price = fresh.price
    if "describe" in steps:
        item.listing.description_html = fresh.description_html


def run_step(settings: Settings, item: Item, step: str) -> None:
    if step == "photos":
        if settings.r2.configured:
            R2Publisher(settings).publish(item)
        else:
            log.warning("R2 not configured; %s photos stay local", item.sku)
    elif step == "identify":
        item.identification = identify.identify(settings, item)
    elif step == "price":
        pricing.price(settings, item)
    elif step == "describe":
        item.description = describe.describe(settings, item)
    else:
        raise ValueError(f"Unknown step {step!r}")


def process(settings: Settings, store: Store, item: Item, steps: tuple[str, ...] = STEPS) -> Item:
    """Run `steps` in order, saving after each so a failure keeps earlier work."""
    item.error = ""
    try:
        for step in steps:
            log.info("%s: %s", item.sku, step)
            run_step(settings, item, step)
            store.save(item)
        refresh_listing(item, steps)
        item.status = Status.REVIEW
    except Exception as exc:
        log.exception("%s failed", item.sku)
        item.error = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc(limit=3)}"
        item.status = Status.ERROR
    return store.save(item)


def _ready_approved(settings: Settings, store: Store, skus: list[str] | None = None) -> tuple[list[Item], dict[str, list[str]]]:
    """Approved items with photos on R2 that pass validation, plus the problems of the rest."""
    ready, problems = [], {}
    for item in store.list(Status.APPROVED):
        if skus and item.sku not in skus:
            continue
        if settings.r2.configured and any(not p.url for p in item.photos):
            try:
                R2Publisher(settings).publish(item)
                store.save(item)
            except Exception as exc:
                problems[item.sku] = [f"photo upload failed: {exc}"]
                continue
        errors = export.validate(item)
        if errors:
            problems[item.sku] = errors
        else:
            ready.append(item)
    return ready, problems


def export_approved(settings: Settings, store: Store) -> tuple[Path | None, dict[str, list[str]]]:
    """Write approved items that pass validation to a new CSV and mark them exported."""
    ready, problems = _ready_approved(settings, store)
    if not ready:
        return None, problems
    path = export.write_csv(settings, ready)
    for item in ready:
        item.status = Status.EXPORTED
        item.exported_file = path.name
        store.save(item)
    return path, problems


def publish_approved(settings: Settings, store: Store, skus: list[str] | None = None) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Publish approved items to eBay through the API; returns ({sku: listing_id}, problems)."""
    ready, problems = _ready_approved(settings, store, skus)
    listed: dict[str, str] = {}
    if not ready:
        return listed, problems
    client = publish.EbaySell(settings)
    for item in ready:
        try:
            item.ebay_listing_id = client.publish(item)
        except Exception as exc:
            log.exception("%s publish failed", item.sku)
            problems[item.sku] = [str(exc)]
            continue
        item.status = Status.LISTED
        store.save(item)
        listed[item.sku] = item.ebay_listing_id
    return listed, problems


class Worker(threading.Thread):
    """Single background worker; processes queued items one at a time."""

    def __init__(self, settings: Settings, store: Store, poll_seconds: float = 2.0):
        super().__init__(daemon=True, name="lister-worker")
        self.settings, self.store, self.poll = settings, store, poll_seconds
        self.stop_event = threading.Event()

    def run(self) -> None:
        self.store.requeue_stale()
        while not self.stop_event.is_set():
            item = self.store.claim_next()
            if item is None:
                self.stop_event.wait(self.poll)
                continue
            process(self.settings, self.store, item)
