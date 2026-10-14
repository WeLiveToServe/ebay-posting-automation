"""Command line: `python -m lister <command>`."""

from __future__ import annotations

import argparse
import logging
import socket
from pathlib import Path

from . import photos, pipeline
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

    p = sub.add_parser("list", help="List items")
    p.add_argument("status", nargs="*", choices=[s.value for s in Status])
    p.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = load_settings()
    args.func(args, settings, Store(settings.db_path))


if __name__ == "__main__":
    main()
