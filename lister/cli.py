"""Command line: `python -m lister <command>`."""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
from pathlib import Path

from . import account, agent, export, photos, pipeline, publish
from .config import load_settings
from .models import Status
from .store import Store


def cmd_serve(args, settings, store) -> None:
    import uvicorn

    from .web.app import create_app

    host = args.host or settings.server.host
    port = args.port or settings.server.port
    if host not in ("127.0.0.1", "localhost") and not settings.server.access_token:
        print("Note: LISTER_ACCESS_TOKEN is unset, so only this computer can use the UI.")
    try:
        lan_ip = socket.gethostbyname(socket.gethostname())
        suffix = f"/?token={settings.server.access_token}" if settings.server.access_token else ""
        print(f"Phone (same Wi-Fi): http://{lan_ip}:{port}{suffix}")
    except OSError:
        pass
    uvicorn.run(create_app(settings), host=host, port=port)


def cmd_ingest(args, settings, store) -> None:
    """Import a folder of per-item subfolders (the old batch-image-sets layout)."""
    root = Path(args.folder)
    folders = [p for p in sorted(root.iterdir()) if p.is_dir()] or [root]
    for folder in folders:
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in photos.IMAGE_SUFFIXES)
        if not files:
            continue
        item = store.create(settings.listing.sku_prefix)
        for f in files:
            photos.add_photo(settings, item, f.name, f.read_bytes())
        notes = folder / "notes.txt"
        item.notes = notes.read_text(encoding="utf-8").strip() if notes.exists() else ""
        item.status = Status.QUEUED
        store.save(item)
        print(f"{item.sku}: {len(files)} photos from {folder.name}")
    print("Queued. Run `python -m lister process` or start the server to process them.")


def cmd_process(args, settings, store) -> None:
    items = [store.get(s) for s in args.sku] if args.sku else store.list(Status.QUEUED, Status.ERROR)
    steps = tuple(args.steps.split(",")) if args.steps else pipeline.STEPS
    for item in filter(None, items):
        item = pipeline.process(settings, store, item, steps)
        price = f"${item.listing.price:.2f}" if item.listing.price else "-"
        print(f"{item.sku}: {item.status.value} | {price} | {item.listing.title or item.error.splitlines()[0]}")


def cmd_export(args, settings, store) -> None:
    path, problems = pipeline.export_approved(settings, store)
    for sku, errors in problems.items():
        print(f"SKIPPED {sku}: {'; '.join(errors)}")
    print(f"Wrote {path}" if path else "Nothing to export (approve items in the web UI first).")


def cmd_publish(args, settings, store) -> None:
    listed, problems = pipeline.publish_approved(settings, store, args.sku or None)
    for sku, errors in problems.items():
        print(f"SKIPPED {sku}: {'; '.join(errors)}")
    for sku, listing_id in listed.items():
        print(f"LISTED {sku}: {publish.listing_url(listing_id)}")
    if not listed and not problems:
        print("Nothing to publish (approve items in the web UI first).")


def cmd_ebay_login(args, settings, store) -> None:
    """One-time seller consent; saves EBAY_REFRESH_TOKEN to .env."""
    if not settings.seller.ebay_runame:
        raise SystemExit("Set EBAY_RUNAME in .env (developer.ebay.com > User Tokens > your RuName) first.")
    print("1. Open this link, sign in with your seller account and click Agree:\n")
    print(publish.consent_url(settings))
    print("\n2. You land on a page that may show an error. Copy its full address and paste it here")
    print("   within 5 minutes:")
    token = publish.exchange_code(settings, input("> "))
    publish.save_env({"EBAY_REFRESH_TOKEN": token})
    print("Saved EBAY_REFRESH_TOKEN to .env (valid about 18 months).")


def _item(store, sku):
    item = store.get(sku)
    if item is None:
        raise SystemExit(f"No item {sku}")
    return item


def cmd_agent(args, settings, store) -> None:
    """JSON commands for a Claude Code / Codex session driving the pipeline (see AGENTS.md)."""
    if args.action == "next":
        item = store.claim_next()
        print(agent.dumps(agent.show(item) if item else {"sku": None, "message": "queue is empty"}))
    elif args.action == "show":
        print(agent.dumps(agent.show(_item(store, args.sku))))
    elif args.action == "guide":
        print(agent.guide(args.step, store.get(args.sku) if args.sku else None))
    elif args.action == "schema":
        print(agent.dumps(agent.schema(args.step)))
    elif args.action == "comps":
        item = _item(store, args.sku)
        comps = agent.fetch_comps(settings, item)
        store.save(item)
        print(agent.dumps(comps))
    elif args.action == "save":
        item = _item(store, args.sku)
        raw = Path(args.file).read_text(encoding="utf-8") if args.file not in (None, "-") else sys.stdin.read()
        problems = agent.save(item, args.step, json.loads(raw))
        store.save(item)
        print(agent.dumps({"sku": item.sku, "status": item.status.value, "problems": problems}))


def cmd_approve(args, settings, store) -> None:
    item = _item(store, args.sku)
    problems = export.validate(item)
    if problems:
        raise SystemExit(f"{item.sku} is not ready: {'; '.join(problems)}")
    item.status = Status.APPROVED
    store.save(item)
    print(f"{item.sku} approved")


def cmd_ebay(args, settings, store) -> None:
    """Manage live eBay listings, including ones the app did not create."""
    acct = account.EbayAccount(settings)
    if args.action == "listings":
        rows = acct.active_listings()
        if args.json:
            print(agent.dumps([r.to_dict() for r in rows]))
            return
        for i, r in enumerate(rows, start=1):
            print(f"{i:>3}. {r.item_id}  ${r.price:>8.2f}  w{r.watchers:<3} {r.started}  {r.title[:70]}")
    elif args.action == "show":
        print(agent.dumps(acct.get_item(args.item_id)))
    elif args.action == "reprice":
        acct.reprice(args.item_id, args.price)
        print(f"{args.item_id}: price set to ${args.price:.2f}")
    elif args.action == "end":
        acct.end(args.item_id)
        print(f"{args.item_id}: ended")
    elif args.action == "adopt":
        item = acct.adopt(store, args.item_id)
        print(f"{args.item_id}: now managed as {item.sku} ({len(item.photos)} photos imported)")


def cmd_list(args, settings, store) -> None:
    for item in store.list(*(Status(s) for s in args.status)):
        price = f"${item.listing.price:.2f}" if item.listing.price else ""
        print(f"{item.sku:18} {item.status.value:10} {price:>8}  {item.listing.title}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lister", description="Photos to eBay listings.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="Run the web UI and background worker")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("ingest", help="Queue items from a folder of per-item photo folders")
    p.add_argument("folder")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("process", help="Process queued/errored items now (no server needed)")
    p.add_argument("sku", nargs="*")
    p.add_argument("--steps", help=f"Comma-separated subset of {','.join(pipeline.STEPS)}")
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("export", help="Write approved items to an eBay upload CSV")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("publish", help="Publish approved items to eBay through the API")
    p.add_argument("sku", nargs="*")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("ebay-login", help="Authorize API publishing for your eBay seller account")
    p.set_defaults(func=cmd_ebay_login)

    p = sub.add_parser("approve", help="Mark a reviewed item ready to publish/export")
    p.add_argument("sku")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("agent", help="JSON commands for a Claude Code / Codex session (see AGENTS.md)")
    act = p.add_subparsers(dest="action", required=True)
    act.add_parser("next", help="Claim the oldest queued item and show it")
    a = act.add_parser("show", help="Show an item: photos, drafts, listing, problems")
    a.add_argument("sku")
    a = act.add_parser("guide", help="Instructions for a step (same as the API prompts)")
    a.add_argument("step", choices=list(agent.SCHEMAS))
    a.add_argument("sku", nargs="?")
    a = act.add_parser("schema", help="JSON schema a step's output must match")
    a.add_argument("step", choices=list(agent.SCHEMAS))
    a = act.add_parser("comps", help="Fetch current eBay asking-price comparables for an item")
    a.add_argument("sku")
    a = act.add_parser("save", help="Validate and store a step's JSON (file path, or stdin)")
    a.add_argument("sku")
    a.add_argument("step", choices=list(agent.SCHEMAS))
    a.add_argument("file", nargs="?")
    p.set_defaults(func=cmd_agent)

    p = sub.add_parser("ebay", help="Manage live eBay listings (list, reprice, end, adopt)")
    act = p.add_subparsers(dest="action", required=True)
    a = act.add_parser("listings", help="Active listings with price, watchers and start date")
    a.add_argument("--json", action="store_true")
    a = act.add_parser("show", help="Full details of a live listing")
    a.add_argument("item_id")
    a = act.add_parser("reprice", help="Change a listing's price (Best Offer thresholds follow)")
    a.add_argument("item_id")
    a.add_argument("price", type=float)
    a = act.add_parser("end", help="End a listing")
    a.add_argument("item_id")
    a = act.add_parser("adopt", help="Bring a Seller Hub/CSV listing under app control")
    a.add_argument("item_id")
    p.set_defaults(func=cmd_ebay)

    p = sub.add_parser("list", help="List items")
    p.add_argument("status", nargs="*", choices=[s.value for s in Status])
    p.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # titles and JSON carry non-ASCII text
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = load_settings()
    args.func(args, settings, Store(settings.db_path))


if __name__ == "__main__":
    main()
