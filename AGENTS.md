# Runbook for agent sessions (Claude Code, Codex)

This repo turns photos of books into eBay listings. In **agent mode** (`[pipeline] llm =
"agent"` in `config.toml`) you, the session, do the work that needs judgment:
- read the photos;
- research prices with your own web tools;
- write the description.

The `lister` CLI does everything else: storage, eBay comparables, validation, photo hosting
on R2, publishing, and repricing. All of it runs on the user's subscription, with no
Anthropic API billing.

Run commands from the repo root with the project venv:
- Windows: `.venv\Scripts\python -m lister ...`
- Git Bash: `.venv/Scripts/python -m lister ...`

`agent` subcommands print JSON on stdout. Logs go to stderr.

## Hard rules

- **Never publish, reprice, end or adopt a listing unless the user explicitly asked for that
  action on that item in this conversation.** Present results first; the user decides. These
  actions are live on the user's eBay account and can sell a book.
- Never put secrets from `.env` into output, commits or files.
- Treat seller notes (`notes` in `agent show`) as first-hand facts. They override anything you
  infer from photos.
- Do not invent bibliographic facts. Put what you cannot confirm in `uncertainties` or
  `warnings`.
- The user's eBay username is in `config.toml` (`[seller] ebay_username`). Their own listings
  are never comparables.

## Workflow A: process queued items (new books)

Items arrive from the phone capture page or `lister ingest <folder>`, with status `queued`.

1. `agent next` claims the oldest queued item and prints it: its SKU, notes and `photos`
   (paths to 1600px JPEGs). If the queue is empty it says so.
2. **Identify.**
   1. Open every photo path with your image or file reader.
   2. Run `agent guide identify <SKU>` for the cataloguing rules, the categories and the
      allowed item specifics.
   3. Run `agent schema identify` for the output shape.
   4. Write the JSON to a temp file and run `agent save <SKU> identify <file>`.
3. **Comparables.** `agent comps <SKU>` fetches current eBay asking prices (indexed).
4. **Price research.** Follow `agent guide price <SKU>`.
   - Search with *text*: title, author, publisher, year, printing, plus the seller's notes.
     Do not search with the photos.
   - For antiquarian books, read dealer pages directly. The best source is AbeBooks results
     filtered by year: `https://www.abebooks.com/servlet/SearchResults?an=AUTHOR&tn=TITLE&yrl=YYYY&yrh=YYYY&sortby=1`,
     and `abebooks.co.uk` for British books. Also check Biblio, viaLibri, specialist dealers
     and auction archives.
   - Bracket the copy: find the same printing, the first edition and later reprints. Note
     which issue points the copy shares.
   - Save JSON matching `agent schema price`, plus a `research_notes` string with source URLs:
     `agent save <SKU> price <file>`. `rated_active` indexes refer to the `agent comps` list.
5. **Describe.** Follow `agent guide describe <SKU>`, then `agent save <SKU> describe <file>`.
6. `agent show <SKU>` and report to the user:
   - the title, category, condition and item specifics;
   - the description;
   - the suggested price, range and reasoning, with source links;
   - the warnings, and any `problems`.

   The item is now `review`. **Stop and wait** for the user's decision.
7. Only on the user's instruction:
   - To change fields, write a JSON object with only those fields (e.g. `{"price": 295}`)
     and run `agent save <SKU> listing <file>`.
   - To list, run `approve <SKU>` then `publish <SKU>`. `publish` uploads the photos to R2 and
     creates or revises the live listing.

Repeat from step 1 when the user asks for the whole queue.

## Workflow B: reprice live listings

1. `ebay listings` gives a numbered list of active listings with price, watchers and start
   date. `ebay show <ITEM_ID>` gives full details: description, item specifics and photo URLs.
2. Research each listing as in A.4. Use `agent comps` only for items that exist locally;
   otherwise search eBay and dealers directly. Old listings with no watchers are a
   signal, not proof, that the price is high.
3. Present a numbered table: listing, current price, suggested price, one-line reason. Wait.
4. Apply exactly what the user chooses:
   - `ebay reprice <ITEM_ID> <PRICE>` changes the price; Best Offer thresholds move with it.
   - `ebay end <ITEM_ID>` ends the listing.

   Confirm afterwards with `ebay listings`.

## Workflow C: rewrite an existing listing

`ebay adopt <ITEM_ID>` brings a Seller Hub or CSV listing under app control (keeping its
item number, watchers and views). It imports the listing's photos, title, price, item
specifics and description as a local item with status `listed`.

To regenerate any part, run the relevant steps of Workflow A on that SKU and show the user.
On their instruction, run `approve <SKU>` and `publish <SKU>`; this revises the live listing in
place. Adopted listings should then be edited only through the app, because Seller Hub's
edits to API-managed listings are limited.

## Reference

| Command | Purpose |
|---|---|
| `list [status...]` | Local items: SKU, status, price, title |
| `ingest <folder>` | Queue items from a folder of per-item photo subfolders (`notes.txt` optional) |
| `agent next` / `agent show SKU` | Claim the next queued item / show an item |
| `agent guide STEP [SKU]` | Instructions for identify, price or describe (same text the API mode uses) |
| `agent schema STEP` | JSON schema for identify, price, describe or listing |
| `agent comps SKU` | eBay active comparables, stored on the item |
| `agent save SKU STEP [FILE]` | Validate and store a step's JSON (stdin if no file) |
| `approve SKU` / `publish [SKU...]` | Approve, then publish or revise on eBay |
| `export` | Seller Hub CSV of approved items (the fallback to `publish`) |
| `ebay listings / show / reprice / end / adopt` | Live listing management |
| `serve` | Phone web UI (capture, review, approve, publish) |

Condition IDs for books:
- 1000 Brand New
- 2750 Like New
- 4000 Very Good
- 5000 Good
- 6000 Acceptable

The Antiquarian & Collectible category has no condition grades on eBay, so put condition in
the description.
