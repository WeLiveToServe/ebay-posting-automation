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

## Two ways to run the AI steps

Identify, price and describe can run in either of two modes. Choose with `[pipeline] llm` in
`config.toml`.

- **`agent`: a Claude Code or Codex session does the work on your subscription.** It does not
  use Anthropic API credit. Open a session in this folder and say, for example, "process the
  queue", "reprice my live listings" or "rewrite listing 177413428830".
  - The session follows [AGENTS.md](AGENTS.md); `CLAUDE.md` points Claude Code there.
  - It reads the photos itself and researches prices with its own web tools, giving more
    weight to rare-book dealers.
  - It returns JSON through `python -m lister agent ...`. The same checks and post-processing
    run as in API mode.
  - It never publishes or reprices without your go-ahead.
- **`api`: the background worker calls the Anthropic API.** Items are processed as soon as
  you queue them. This mode needs `ANTHROPIC_API_KEY` with credit.

Live listings, including ones made in Seller Hub, are managed with `python -m lister ebay ...`:

| Command | What it does |
|---|---|
| `listings` | Lists your active listings |
| `reprice` | Changes the price; Best Offer thresholds follow |
| `end` | Ends the listing |
| `adopt` | Brings a listing under app control so it can be rewritten and re-published |

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

### Publishing straight to eBay (instead of the CSV)

**Publish N approved to eBay** creates live listings through the Sell Inventory API. It uses your
business policies by the names in `[seller]`, an inventory location built from `location` and
`postal_code`, and the item's SKU as the eBay custom label. Publishing an item again revises its
live listing; it does not create a duplicate. The eBay item number is shown on the item page.

To set it up once:
1. Go to developer.ebay.com → Application Keys → **User Tokens** for the Production keyset.
   Add an eBay redirect URL with OAuth enabled.
2. Put its RuName in `.env` as `EBAY_RUNAME`.
3. Run `python -m lister ebay-login`, approve access in the browser, and paste back the URL you
   land on. This saves `EBAY_REFRESH_TOKEN`, which is valid for about 18 months. Revoke it any time
   in your eBay account settings.

Seller Hub can only make limited edits to listings created through the API, so revise them by
re-publishing from the app.

Command-line equivalents:

| Command | What it does |
|---|---|
| `python -m lister ingest <folder>` | Queues items from a folder. Each subfolder is one item, and an optional `notes.txt` in it becomes the item's notes. This replaces the old `batch-image-sets` flow. |
| `python -m lister process [SKU ...] [--steps identify,price]` | Processes queued or failed items without starting the server. |
| `python -m lister list [review approved ...]` | Lists items, optionally filtered by status. |
| `python -m lister export` | Writes the approved items to a CSV. |
| `python -m lister publish [SKU ...]` | Publishes approved items live on eBay through the API. |
| `python -m lister ebay-login` | Authorizes API publishing for your seller account, one time. |

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
