"""Phone-friendly web UI: capture photos, watch processing, review/edit, approve, export."""

from __future__ import annotations

import hmac
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import categories, export, photos, pipeline, preview, pricing
from ..config import Settings, load_settings
from ..models import Item, Status
from ..store import Store

HERE = Path(__file__).parent
COOKIE = "lister_token"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def create_app(settings: Settings | None = None, start_worker: bool = True) -> FastAPI:
    settings = settings or load_settings()
    store = Store(settings.db_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # In agent mode a Claude Code / Codex session processes the queue instead.
        worker = pipeline.Worker(settings, store) if start_worker and settings.pipeline.llm == "api" else None
        if worker:
            worker.start()
        yield
        if worker:
            worker.stop_event.set()

    app = FastAPI(title="Lister", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    # Photos referenced by the preview (data/previews/listings.html uses ../derived/...).
    app.mount("/derived", StaticFiles(directory=settings.derived_dir, check_dir=False), name="derived")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(categories=categories.CATEGORIES, Status=Status)

    @app.middleware("http")
    async def auth(request: Request, call_next):
        token = settings.server.access_token
        if token:
            supplied = request.query_params.get("token") or request.cookies.get(COOKIE, "")
            if not hmac.compare_digest(supplied, token):
                return Response("Unauthorized. Open this page once with ?token=<LISTER_ACCESS_TOKEN>.", 401)
            response = await call_next(request)
            if request.query_params.get("token"):
                response.set_cookie(COOKIE, token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
            return response
        if request.client and request.client.host not in LOCAL_HOSTS:
            return Response("Set LISTER_ACCESS_TOKEN in .env to allow access from other devices.", 403)
        return await call_next(request)

    def load(sku: str) -> Item:
        item = store.get(sku)
        if item is None:
            raise HTTPException(404, f"No item {sku}")
        return item

    def page(request: Request, name: str, **context) -> HTMLResponse:
        return templates.TemplateResponse(request, name, context)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, msg: str = ""):
        items = store.list()
        groups = {s: [i for i in items if i.status == s] for s in Status}
        exports = sorted(settings.exports_dir.glob("*.csv"), reverse=True)[:5] if settings.exports_dir.exists() else []
        return page(request, "index.html", groups=groups, msg=msg, exports=[p.name for p in exports],
                    r2_ready=settings.r2.configured, can_publish=bool(settings.seller.ebay_refresh_token),
                    agent_mode=settings.pipeline.llm == "agent")

    @app.post("/items")
    def new_item():
        item = store.create(settings.listing.sku_prefix)
        return RedirectResponse(f"/items/{item.sku}/capture", 303)

    @app.get("/items/{sku}/capture", response_class=HTMLResponse)
    def capture(request: Request, sku: str):
        return page(request, "capture.html", item=load(sku))

    @app.post("/items/{sku}/photos")
    async def upload_photo(sku: str, file: UploadFile = File(...)):
        item = load(sku)
        try:
            photo = photos.add_photo(settings, item, file.filename or "photo.jpg", await file.read())
        except Exception as exc:
            raise HTTPException(400, f"Could not read image: {exc}")
        store.save(item)
        return JSONResponse({"idx": photo.idx, "src": f"/photos/{sku}/{photo.idx}"})

    @app.get("/photos/{sku}/{idx}")
    def photo_file(sku: str, idx: int):
        for p in load(sku).photos:
            if p.idx == idx:
                return FileResponse(p.derived, media_type="image/jpeg")
        raise HTTPException(404)

    @app.post("/items/{sku}/photos/{idx}/delete")
    def delete_photo(sku: str, idx: int, back: str = "capture"):
        item = load(sku)
        photos.remove_photo(item, idx)
        store.save(item)
        return RedirectResponse(f"/items/{sku}/capture" if back == "capture" else f"/items/{sku}", 303)

    @app.post("/items/{sku}/photos/{idx}/move")
    def move_photo(sku: str, idx: int, delta: int):
        item = load(sku)
        photos.move_photo(item, idx, delta)
        store.save(item)
        return RedirectResponse(f"/items/{sku}", 303)

    @app.post("/items/{sku}/queue")
    def queue(sku: str, notes: str = Form(""), cost: str = Form(""), shelf_location: str = Form(""),
              next: str = Form("")):
        item = load(sku)
        if not item.photos:
            return RedirectResponse(f"/items/{sku}/capture", 303)
        item.notes, item.shelf_location = notes.strip(), shelf_location.strip()
        item.cost = _money(cost)
        item.status = Status.QUEUED
        store.save(item)
        if next:
            return new_item()
        return home(f"{sku} queued")

    @app.get("/items/{sku}", response_class=HTMLResponse)
    def review(request: Request, sku: str):
        item = load(sku)
        cat = categories.get(item.listing.category) if item.listing.category in categories.CATEGORIES else None
        return page(request, "item.html", item=item, cat=cat, stats=pricing.comp_stats(item.comps),
                    problems=export.validate(item) if item.status in (Status.REVIEW, Status.APPROVED) else [])

    @app.post("/items/{sku}")
    async def save(request: Request, sku: str):
        item = load(sku)
        form = await request.form()
        listing = item.listing
        listing.category = str(form.get("category", listing.category))
        listing.title = str(form.get("title", listing.title)).strip()
        listing.price = _money(str(form.get("price", ""))) or listing.price
        listing.condition_id = str(form.get("condition_id", listing.condition_id))
        listing.isbn = str(form.get("isbn", listing.isbn)).strip()
        listing.description_html = str(form.get("description_html", listing.description_html))
        listing.aspects = {
            key.removeprefix("aspect__"): str(value).strip()
            for key, value in form.items()
            if key.startswith("aspect__") and str(value).strip()
        }
        item.notes = str(form.get("notes", item.notes))
        item.shelf_location = str(form.get("shelf_location", item.shelf_location))
        action = form.get("action", "save")
        if action == "approve":
            item.status = Status.APPROVED
        elif action == "unapprove":
            item.status = Status.REVIEW
        store.save(item)
        return home(f"{sku} approved") if action == "approve" else RedirectResponse(f"/items/{sku}", 303)

    @app.post("/items/{sku}/rerun")
    def rerun(sku: str, step: str = Form(...)):
        item = load(sku)
        steps = pipeline.STEPS if step == "all" else (step,)
        if any(s not in pipeline.STEPS for s in steps):
            raise HTTPException(400, f"Unknown step {step}")
        if step == "all" or settings.pipeline.llm == "agent":  # agent sessions pick queued items up
            item.status = Status.QUEUED
            store.save(item)
        else:
            item.status = Status.PROCESSING
            store.save(item)
            threading.Thread(target=pipeline.process, args=(settings, store, item, steps), daemon=True).start()
        return RedirectResponse(f"/items/{sku}", 303)

    @app.post("/items/{sku}/delete")
    def delete(sku: str):
        store.delete(sku)
        return home(f"{sku} deleted (original photos kept in data/originals)")

    @app.post("/export")
    def run_export():
        path, problems = pipeline.export_approved(settings, store)
        parts = [f"Exported {path.name}" if path else "Nothing exported"]
        parts += [f"{sku}: {'; '.join(errs)}" for sku, errs in problems.items()]
        return home(" | ".join(parts))

    @app.post("/publish")
    def run_publish():
        try:
            listed, problems = pipeline.publish_approved(settings, store)
        except RuntimeError as exc:  # missing eBay credentials
            return home(str(exc))
        parts = [f"Listed {len(listed)} on eBay" if listed else "Nothing listed"]
        parts += [f"{sku}: {'; '.join(errs)}" for sku, errs in problems.items()]
        return home(" | ".join(parts))

    @app.get("/preview", response_class=HTMLResponse)
    def preview_page():
        """The multi-agent preview, rebuilt on each request (live listings come from the cached snapshot)."""
        return HTMLResponse(preview.build(settings, store).read_text(encoding="utf-8"))

    @app.get("/exports/{name}")
    def download(name: str):
        path = (settings.exports_dir / name).resolve()
        if path.parent != settings.exports_dir.resolve() or not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="text/csv", filename=name)

    return app


def home(msg: str) -> RedirectResponse:
    return RedirectResponse("/?msg=" + quote(msg), 303)


def _money(value: str) -> float | None:
    try:
        return round(float(value.replace("$", "").strip()), 2) if value.strip() else None
    except ValueError:
        return None
