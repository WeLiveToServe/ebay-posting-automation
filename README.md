# eBay posting automation (v2)

Photograph an item on your phone and get back a reviewed eBay listing in a bulk-upload CSV.

```
phone photos ─► identify (Claude vision) ─► price (eBay comps + web research) ─► describe ─► review/approve ─► eBay CSV
                       photos published to Cloudflare R2 for eBay to fetch ──────────────────────────────┘
```

Books are the first category (Books 261186, Antiquarian & Collectible 29223). Other kinds
of items are added as category profiles in `lister/categories.py`.

## Setup (Windows, Python 3.11+)

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
copy config.example.toml config.toml   # seller defaults, R2 bucket, models
copy .env.example .env                 # API keys
```

Fill in `.env` with these keys:

| Key | Where it comes from | Used for |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com | identify, price analysis, web research, description |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Cloudflare dashboard → R2 → Manage API tokens (Object Read & Write) | publishing listing photos |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | developer.ebay.com → production keyset | Browse API active-listing comps |
| `LISTER_ACCESS_TOKEN` | any long random string | lets your phone use the web UI |

**R2 bucket:** create the bucket and connect a custom domain to it (R2 → bucket →
Settings → Custom Domains). Then set `[r2].public_base_url` to that domain. The `r2.dev`
URLs work for testing, but Cloudflare rate-limits them and says not to use them in
production.

## Daily use

```powershell
.venv\Scripts\python -m lister serve
```

1. On your phone, open the printed `http://<pc-ip>:8765/?token=...` address. Your phone must be on the same Wi-Fi.
   To use it away from home, run a [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) to the same port.
2. Tap **+ New item**, then take photos: cover, spine, back, title page, copyright page, and any flaws.
   Add notes for anything the photos can't show. Tap **Done · next item**.
3. The background worker uploads the photos to R2. It then identifies the item, finds comparable listings, suggests a price, and writes the description.
4. Open each item under **Review**. Check the warnings, compare against the linked comps, edit anything that's wrong, then tap **Save & approve**.
5. Tap **Export N approved to eBay CSV** and upload the file at Seller Hub → Reports → Uploads.

Command-line equivalents:

| Command | What it does |
|---|---|
| `python -m lister ingest <folder>` | Queues items from a folder. Each subfolder is one item, and an optional `notes.txt` in it becomes the item's notes. This replaces the old `batch-image-sets` flow. |
| `python -m lister process [SKU ...] [--steps identify,price]` | Processes queued or failed items without starting the server. |
| `python -m lister list [review approved ...]` | Lists items, optionally filtered by status. |
| `python -m lister export` | Writes the approved items to a CSV. |

Runtime data is kept in `data/` and is gitignored:
- `originals/`: your photos, never modified.
- `derived/`: resized JPEGs with GPS and other metadata removed.
- `lister.db`: the item records.
- `exports/`: the dated CSV files.

## How pricing works

For each item, the price step:
- collects **current eBay asking prices** from the Browse API, searching by ISBN and by a search query;
- runs a **Claude web-search pass** that looks for recent *sold* prices and dealer asking prices;
- passes both to Claude, which weights sold prices above asking prices, adjusts for edition and condition, and returns a suggested price, a range, and a rating for each comp.

The review screen links to every comp.

eBay no longer gives new developers access to sold-listing data: the Finding API's
`findCompletedItems` has been retired and Marketplace Insights is restricted. To add a
dedicated sold-comps provider, write a source in `lister/pricing.py` that returns
`Comp(source="ebay_sold", ...)` and list it in `[pricing].sources`.

## Layout

| Path | Contents |
|---|---|
| `lister/categories.py` | eBay categories, item specifics, condition IDs |
| `lister/identify.py` | step 1: vision cataloguing (structured output) |
| `lister/pricing.py` | step 2: comps sources and price suggestion |
| `lister/describe.py` | step 3: description writer and HTML renderer |
| `lister/export.py` | Seller Hub CSV columns and validation |
| `lister/pipeline.py` | step runner, background worker, export |
| `lister/photos.py` | photo normalization and R2 publishing |
| `lister/web/` | FastAPI and Jinja UI (capture, review, export) |
| `legacy/` | the v1 scripts and workbooks, kept for reference |

## Tests

```powershell
.venv\Scripts\python -m pytest
```

The tests stub out the Claude and eBay calls, so they cost nothing to run.
