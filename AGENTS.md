# Runbook for agent sessions (Claude Code, Codex, any other)

This repo turns photos of books into eBay listings. In **agent mode** (`[pipeline] llm =
"agent"` in `config.toml`) a model session does the work that needs judgment, on the user's
subscription with no API billing. The `lister` CLI does everything else: storage, validation,
assembly, the preview, photo hosting and eBay calls.

Run commands from the repo root with the project venv:
- Windows: `.venv\Scripts\python -m lister ...`
- Git Bash: `.venv/Scripts/python -m lister ...`

`run` and `agent` subcommands print JSON or a file path on stdout; logs go to stderr.

## Hard rules

- **Never publish, reprice, end or adopt a listing unless the user explicitly asked for that
  action on that item in this conversation.** Present results first; the user decides. These
  actions are live on the user's eBay account and can sell a book.
- Never put secrets from `.env` into output, commits or files.
- Treat seller notes as first-hand facts. They override anything inferred from photos.
- Do not invent bibliographic facts or prices. Unconfirmed facts become uncertainties or
  questions.
- The user's own eBay listings (`[seller] ebay_username`) are never comparables.

## Workflow A: multi-agent listing run (new books)

You are the **orchestrator**. You don't write the listing yourself. You start one role agent per
stage for each book, give it a brief, check the results, send corrections, and sign off. The
user wants to stay out of the loop until the end: **don't stop mid-run to ask questions.**
Questions collected along the way go into the run summary and the preview.

### Roles

Each role has a fixed system prompt in `lister/roles/<role>.md`, an output schema (in
`lister/crew.py`, and printed inside every brief), and a recommended model. `run roles` prints
this table.

| Stage | Role | Receives as given facts | Claude Code | Codex |
|---|---|---|---|---|
| 1 | edition | photos, seller notes | Opus, effort high | most capable model, high |
| 2 | condition | edition | Sonnet, medium (Opus for older books if grading looks off) | default model, medium |
| 3 | pricing | edition, condition | **Opus, high (always)** | most capable model, high |
| 3 | description | edition, condition | Sonnet, medium | default model, medium |
| 4 | assembly | everything | code: `run assemble` | same |

Pricing and description run **in parallel**. Every stage takes the earlier results as given and
does not reassess them; an agent that finds contrary evidence reports it and doesn't override.

**Age rule:** a book is **older** at 40 or more years (from the edition's `year`). This
decides the grading scale, the title format, the description style and the required price
sources. Briefs state it for each book.

### Starting and keeping role agents

Every role agent gets the **same instructions every run**: the brief file starts with the role's
system prompt verbatim. Hand the agent the brief's path and nothing else of substance:

> Read `data/runs/BK-…/edition.brief.md` and follow it exactly.

Keep each agent **alive until you sign off the whole listing**, so that one asked to fix a
mistake still knows its earlier work.

- **Claude Code:** use the project agents in `.claude/agents/`: `book-edition`, `book-condition`,
  `book-pricing` and `book-description`. Their model and effort are set there (pricing is always
  Opus). Start each with the Agent tool, `subagent_type` set to the agent name, in the
  background, and run pricing and description in the same message so they run in parallel.
  Send corrections to the same agent with SendMessage; it keeps its context. Agent files are
  read when a session starts. If `book-*` isn't offered, start `general-purpose` instead, with
  the role's `model` and `effort` from the table and the body of `.claude/agents/book-<role>.md`
  as its prompt.
- **Codex:** use the project agents in `.codex/agents/` (`book_edition`, `book_condition`,
  `book_pricing`, `book_description`). Ask Codex to spawn the named agent with the brief path,
  and send corrections to that thread (`send_input`). The files set `model_reasoning_effort`.
  Set `model` in them to the strongest model your plan offers for edition and pricing; check
  `/model` for the names.
- **No subagent support** (or the agent was closed): start a fresh session or instance per role
  with the brief as its instructions. For a correction, a new instance works, because `run brief`
  rewrites the brief with the original prompt, the corrections and the previous output.
- **No separate model at all:** do each role yourself, in order, one at a time. Read only that
  role's brief, and treat the earlier results as given. This is the weakest option; say so in
  the summary.

### Browser tools for pricing

eBay's ordinary search shows sold listings for about 90 days. Terapeak (Seller Hub → Research)
shows years. The pricing agent uses a browser or computer-use tool when the session has one:
- Claude Code: start with `claude --chrome` (Claude in Chrome, using the user's logged-in
  Chrome).
- Codex: a browser MCP server (e.g. Playwright) or the ChatGPT browser extension.

If the session has no browser tool, pricing still runs on 90-day sold and active listings, and
the agent raises a question. If the user wants Terapeak history and Claude Code has no browser
tool available, recommend running the orchestrator from a Codex session that has one.

### Steps for one book

1. `run start [SKU]`: claims the next queued item (or the SKU given) and prints its photos, the
   stage plan, and the brief and output paths under `data/runs/<SKU>/`.
2. **Edition:** `run brief SKU edition` writes the brief. Start the edition agent on it. When
   it replies, run `run save SKU edition`.
3. **Condition:** `run brief SKU condition`, start the agent, then `run save SKU condition`.
4. **Pricing and description, in parallel:** `run brief SKU pricing` and
   `run brief SKU description`, then start both agents together. Save each when it finishes.
5. `run assemble SKU`: code builds the title, photo order, condition field, description HTML
   and item specifics. It checks them against the rules and eBay's required fields and rebuilds
   the preview.
6. **Quality check:** go through `lister/roles/checklist.md` against the preview and the
   photos. Every `problems` entry, and every checklist failure, is a correction for the role it
   names.
7. **Corrections:** for each failing role:
   1. Record the correction with `run correct SKU ROLE "what is wrong, the evidence, what must
      change"`.
   2. Run `run brief SKU ROLE`. This rewrites the brief, with the correction added to its
      instructions and the previous output attached.
   3. Message the live agent: "Correction recorded; re-read your brief and revise."
   4. Save its new output with `run save`.

   Saving a role marks the later roles that depend on it as **stale**. Re-run those, then go
   back to step 5. Stop after 3 correction rounds for a role; leave what remains as a question.
8. `run signoff SKU`: refused while `problems` is non-empty.
   - For a new item, it copies the listing onto the item and sets the status to `review`.
   - For an item that is already live, it does **not** touch the listing; the draft stays in
     the run.

   Close the role agents.
9. Repeat from step 1 for the rest of the queue if the user asked for the queue. Then report:
   one line per book (title, suggested price and range, provisional or not), all open questions
   grouped by book, and the preview path (`data/previews/listings.html`, or `/preview` while
   `lister serve` runs).

   **Stop.** Publishing happens only on the user's instruction: `approve SKU` then `publish SKU`.

When the user answers questions, record the answers as corrections to the responsible role
(e.g. pricing: "Seller says give the 1914 firsts no weight"). Then run steps 7–8 again.

### What code enforces

You don't need to check these by eye; `run save` and `run assemble` report them as problems.

- **Grade:** never New; never Like New for an older book; the grade must be on the scale for
  the book's age.
  - Newer books use the eBay terms: Like New, Very Good, Good, Acceptable.
  - Older books use the collector terms: Fine, Near Fine, Very Good, Good, Fair, Poor.
- **eBay condition field:**
  - Like New → Like New; Fine, Near Fine and Very Good → Very Good; Good → Good; Fair, Poor and
    Acceptable → Acceptable.
  - The collector grade is always written in the description text, which matters for the
    Antiquarian category, where eBay shows no grades.
- **Title:** 80 characters at most, facts only.
  - Older books: year, edition, publisher, author, title.
  - Newer books: author, title.
  - When a title is too long, parts are dropped in this order: subtitle, publisher suffixes,
    publisher, edition.
- **Photo order:**
  1. Front covers if the cover is illustrated, otherwise spines.
  2. The other cover and spine shots.
  3. Title and copyright pages, by volume.
  4. Plates, then defects.
  5. Angle and edge shots last.
- **Edition:** every photo labelled; older books confirmed by 2 or more sources with images
  compared.
- **Pricing:**
  - 5 or more eBay comps, or a question explaining why there are fewer.
  - Every required source checked:
    - Newer books: Amazon, Barnes & Noble, AbeBooks, ThriftBooks, BookFinder.
    - Older books: AbeBooks .com and .co.uk, Biblio, viaLibri, LiveAuctioneers, Invaluable,
      PBA Galleries, forums, Google pages 2–3.
  - Every comp has a URL.
  - Nearby editions require a question and a provisional price.
- **Description and condition note:** older books need a collector line; the condition note is
  at most 2 sentences; the description never mentions a price.

### Questions: when to raise them

Raise a question only for something the user alone can settle:
- facts about the physical copy the photos don't show;
- how to weight nearby editions;
- sold prices hidden in Terapeak;
- a conflict between the seller's notes and the evidence.

Never raise one for something an agent could look up. Questions never pause the run.

## Workflow B: reprice live listings

1. `ebay listings` gives a numbered list of active listings with price, watchers and start
   date. `ebay show <ITEM_ID>` gives the full details.
2. Research each listing as the pricing role would. Use `lister/roles/pricing.md` as your guide,
   or start the `book-pricing` agent with a brief you write from `ebay show`. Old listings with
   no watchers are a signal, not proof, that the price is high.
3. Present a numbered table: listing, current price, suggested price, one-line reason. Wait.
4. Apply exactly what the user chooses:
   - `ebay reprice <ITEM_ID> <PRICE>` changes the price; Best Offer thresholds move with it.
   - `ebay end <ITEM_ID>` ends the listing.

   Confirm afterwards with `ebay listings`.

## Workflow C: rewrite an existing listing (only on request)

`ebay adopt <ITEM_ID>` brings a Seller Hub or CSV listing under app control, keeping its
item number, watchers and views. It imports the photos, title, price, item specifics and
description as a local item with status `listed`. Only adopt when the user asks.

To redo it with the team, run Workflow A on that SKU. The draft appears in the preview next to
the live listing. Sign-off keeps a live item's fields unchanged; applying a draft to a live
listing isn't automated in this iteration. If the user asks for specific changes, write those
fields with `agent save SKU listing FILE`, then run `approve SKU` and `publish SKU` to revise the
live listing in place.

## The preview

`run preview` rebuilds `data/previews/listings.html`; `assemble` and `signoff` also rebuild
it. `lister serve` serves it at `/preview`.

Each listing shows, in order:
1. the title;
2. any open questions and problems;
3. the edition;
4. the condition;
5. the description;
6. the price, with every comp linked;
7. the photos, in listing order, in a collapsible section.

The hamburger menu switches between all new runs and the **live eBay listings**. The live
listings are a read-only snapshot refreshed with `run snapshot`, which makes only read calls
(GetMyeBaySelling, GetItem).

## Reference

| Command | Purpose |
|---|---|
| `run roles` | Roles, prompts, recommended Claude/Codex models and effort |
| `run start [SKU] [--restart]` | Start or resume a run; prints photos, plan, brief/output paths |
| `run brief SKU ROLE` | Write a role's complete brief (prompt + corrections + given facts + schema) |
| `run save SKU ROLE [FILE]` | Validate and store a role's output (default `data/runs/SKU/ROLE.json`) |
| `run correct SKU ROLE TEXT` | Record a correction; the next brief carries it |
| `run assemble SKU` | Build and check the listing; rebuild the preview |
| `run signoff SKU` | Orchestrator approval (refused while problems remain) |
| `run status SKU` | Attempts, corrections, stale roles, problems, questions |
| `run preview` / `run snapshot` | Rebuild the preview / refresh the live-listing snapshot (read-only) |
| `list [status...]` | Local items: SKU, status, price, title |
| `ingest <folder>` | Queue items from a folder of per-item photo subfolders (`notes.txt` optional) |
| `agent next / show / guide / schema / comps / save` | The older single-session flow; `agent save SKU listing FILE` edits listing fields |
| `approve SKU` / `publish [SKU...]` | Approve, then publish or revise on eBay (**only on the user's instruction**) |
| `export` | Seller Hub CSV of approved items (the fallback to `publish`) |
| `ebay listings / show / reprice / end / adopt` | Live listing management (writes only on instruction) |
| `serve` | Phone web UI (capture, review, approve, publish, `/preview`) |

Condition IDs for books:
- 1000 Brand New (never used)
- 2750 Like New
- 4000 Very Good
- 5000 Good
- 6000 Acceptable
