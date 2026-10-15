"""Command line: `python -m lister <command>`."""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
from pathlib import Path

from pydantic import ValidationError

from . import account, agent, assemble, crew, export, photos, pipeline, preview, publish
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


def _run_dir(settings, sku: str) -> Path:
    path = settings.data_dir / "runs" / sku
    path.mkdir(parents=True, exist_ok=True)
    return path


def cmd_run(args, settings, store) -> None:
    """Multi-agent listing runs, driven by an orchestrator session (see AGENTS.md)."""
    if args.action == "roles":
        print(agent.dumps({name: {"claude_model": r.claude, "effort": r.effort, "codex": r.codex,
                                  "receives": list(r.needs), "prompt": f"lister/roles/{name}.md"}
                           for name, r in crew.ROLES.items()}))
        return
    if args.action == "preview":
        print(preview.build(settings, store))
        return
    if args.action == "snapshot":  # read-only eBay calls
        rows = preview.snapshot_live(settings)
        print(f"{len(rows)} live listings saved to {preview.live_path(settings)}")
        print(preview.build(settings, store))
        return
    if args.action == "start":
        item = _item(store, args.sku) if args.sku else store.claim_next()
        if item is None:
            print(agent.dumps({"sku": None, "message": "queue is empty"}))
            return
        if item.crew is None or args.restart:
            item.crew = crew.CrewRun()
        store.save(item)
        folder = _run_dir(settings, item.sku)
        print(agent.dumps({
            "sku": item.sku, "status": item.status.value, "notes": item.notes,
            "photos": [{"idx": p.idx, "path": p.derived} for p in item.photos],
            "stages": [
                {"stage": 1, "roles": ["edition"]}, {"stage": 2, "roles": ["condition"]},
                {"stage": 3, "roles": ["pricing", "description"], "parallel": True},
                {"stage": 4, "code": f"run assemble {item.sku}"},
            ],
            "files": {r: {"brief": str(folder / f"{r}.brief.md"), "output": str(folder / f"{r}.json")}
                      for r in crew.ORDER},
            "done": {r: bool(s.latest) for r, s in item.crew.roles.items()},
        }))
        return

    item = _item(store, args.sku)
    folder = _run_dir(settings, item.sku)
    if args.action == "brief":
        path = folder / f"{args.role}.brief.md"
        try:
            path.write_text(crew.brief(item, args.role, folder / f"{args.role}.json",
                                       settings.seller.ebay_username), encoding="utf-8")
        except ValueError as exc:
            raise SystemExit(str(exc))
        print(path)
    elif args.action == "save":
        path = Path(args.file) if args.file else folder / f"{args.role}.json"
        try:
            problems = crew.save(item, args.role, crew.load_json(path))
        except ValidationError as exc:
            print(agent.dumps({"saved": False, "schema_errors": crew.schema_errors(exc)}))
            raise SystemExit(1)
        except ValueError as exc:
            raise SystemExit(str(exc))
        store.save(item)
        stale = [r for r, s in item.crew.roles.items() if s.stale]
        print(agent.dumps({"saved": True, "role": args.role, "attempt": len(item.crew.roles[args.role].attempts),
                           "problems": problems, "stale": stale}))
    elif args.action == "correct":
        crew.correct(item, args.role, args.text)
        store.save(item)
        print(f"Correction {len(item.crew.roles[args.role].corrections)} recorded for {args.role}. "
              f"Re-brief with `run brief {item.sku} {args.role}` (or message the live agent).")
    elif args.action == "assemble":
        try:
            listing = assemble.assemble(item)
        except ValueError as exc:
            raise SystemExit(str(exc))
        store.save(item)
        out = preview.build(settings, store)
        print(agent.dumps({**listing.model_dump(exclude={"description_html"}), "preview": str(out)}))
    elif args.action == "signoff":
        listing = item.crew.assembled if item.crew else None
        if listing is None:
            raise SystemExit(f"Run `run assemble {item.sku}` first")
        if listing.problems:
            raise SystemExit("Not signed off; fix these first:\n- " + "\n- ".join(listing.problems))
        item.crew.signed_off_at = crew.utcnow()
        if item.status in (Status.LISTED, Status.EXPORTED):
            note = ("Item is live; its listing fields were NOT changed. Applying this draft to the live "
                    "listing needs the user's explicit instruction.")
        else:
            assemble.apply(item)
            item.status = Status.REVIEW
            note = "Listing fields updated; status review. Publishing needs the user's explicit instruction."
        store.save(item)
        print(f"{item.sku} signed off. {note}\nPreview: {preview.build(settings, store)}")
    elif args.action == "status":
        run = item.crew
        if run is None:
            raise SystemExit(f"{item.sku} has no run; `run start {item.sku}`")
        print(agent.dumps({
            "sku": item.sku,
            "roles": {r: {"attempts": len(s.attempts), "corrections": s.corrections, "stale": s.stale}
                      for r, s in run.roles.items()},
            "assembled": bool(run.assembled),
            "problems": run.assembled.problems if run.assembled else [],
            "questions": run.assembled.questions if run.assembled else [],
            "signed_off_at": run.signed_off_at,
        }))


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

    p = sub.add_parser("run", help="Multi-agent listing runs for an orchestrator session (see AGENTS.md)")
    act = p.add_subparsers(dest="action", required=True)
    act.add_parser("roles", help="Roles with recommended Claude / Codex models and effort")
    a = act.add_parser("start", help="Start (or resume) a run on SKU, or on the next queued item")
    a.add_argument("sku", nargs="?")
    a.add_argument("--restart", action="store_true", help="Discard earlier role results")
    a = act.add_parser("brief", help="Write a role agent's complete brief; prints its path")
    a.add_argument("sku")
    a.add_argument("role", choices=list(crew.ORDER))
    a = act.add_parser("save", help="Validate and store a role's JSON output")
    a.add_argument("sku")
    a.add_argument("role", choices=list(crew.ORDER))
    a.add_argument("file", nargs="?", help="Defaults to data/runs/SKU/ROLE.json")
    a = act.add_parser("correct", help="Record an orchestrator correction for a role")
    a.add_argument("sku")
    a.add_argument("role", choices=list(crew.ORDER))
    a.add_argument("text")
    a = act.add_parser("assemble", help="Build and check the listing; rebuilds the preview")
    a.add_argument("sku")
    a = act.add_parser("signoff", help="Orchestrator approval of a complete, problem-free listing")
    a.add_argument("sku")
    a = act.add_parser("status", help="Attempts, corrections, staleness, problems and questions")
    a.add_argument("sku")
    act.add_parser("preview", help="Rebuild data/previews/listings.html")
    act.add_parser("snapshot", help="Refresh the read-only snapshot of live eBay listings, then the preview")
    p.set_defaults(func=cmd_run)

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
