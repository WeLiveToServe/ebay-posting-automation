# eBay posting modernization plan

Reviewed September 16, 2026. Scope: the local checkout at commit `b3c52c9`, its Python scripts, YAML configurations, documentation, tracked-file inventory, and current public GitHub/eBay/OpenAI documentation. This is a proposed implementation plan, not a deployment or an end-to-end certification. Existing workbooks were inventoried, not audited cell by cell. No seller account, AWS configuration, or live listings were accessed.

## Recommendation and intended outcome

Build a small, reliable book-selling application around the useful workflow this prototype discovered. Keep Python, preserve spreadsheet export, and progressively replace manual file handoffs with persistent inventory records and a review screen. Add direct eBay publication only after the preparation workflow is dependable.

The stated objective is photo-based identification and eBay listing preparation, including pricing, condition, descriptions, and eventually automation. The larger inferred objective is to make selling a physical collection economically worthwhile by reducing effort per book without sacrificing accurate descriptions or collector value. That implies intake, identification, pricing evidence, review, publication, shelf location, and sale reconciliation. A more elaborate agent framework is not the primary missing feature.

Confirmed initial scale: a personal workflow processing tens of books per week (owner's confirmation, September 16, 2026). Plan for one seller and prioritize convenient intake, accurate review, and low maintenance. US marketplace and books-first scope remain inferred from the repository. Start locally with SQLite and one worker; hosted operation is optional for phone access and unattended processing. PostgreSQL, multiple workers, and multi-user features are outside the initial scope.

## What exists and what to preserve

The actual implementation is six Python scripts: two model runners, two spreadsheet exporters, and two image upload scripts, one of which also renames images. It can prepare listing material; there is no implemented eBay publishing client, OAuth flow, listing reconciliation, application UI, or persistent inventory database.

Preserve the per-book image grouping, the lean extraction concept, seller-specific defaults as configuration, prior listing templates as migration references, and the spreadsheet fallback. The README's shared-agent architecture is not reflected in the executable code: the scripts call OpenAI directly, and the listed wrapper paths do not match this checkout. `.gitmodules` declares a utility repository, but the tracked tree has no corresponding submodule gitlink.

## Findings to fix first

| Priority | Evidence | Consequence and action |
|---|---|---|
| Critical | `json_to_ebay_excel.py:153`, `newest_json()` | An explicit file is replaced by the default directory because only directories survive its first assignment. An isolated execution reproduced `requested.json` selecting `other-book.json`. Honor the explicit path and fail if invalid. |
| High | Both YAML condition prompts | They map Very Good to 3000; eBay documents Very Good as 4000. Retrieve permitted conditions by category and marketplace and keep mappings outside prompts. [eBay condition values](https://www.developer.ebay.com/api-docs/sell/static/metadata/condition-id-values.html). |
| High | `batch_book_agent_runner.py:78`; `book-id-agent.py:358` | Batch does not pass its YAML schema; the single runner explicitly bypasses it on the configured Responses path. Invalid output can be saved as `raw_text`. Use structured parsing, application validation, and explicit failed/refused/incomplete states. |
| High | Both pricing prompts; model calls | They request comparable-based prices but supply no comparables and configure no retrieval tools. Separate identification from pricing; unsupported prices must be labeled estimates or left for review. |
| High | Both upload scripts; `append_json_queue_to_excel.py:170` | One uploader writes filename/URL lines; another writes pipe-separated URLs. The appender accepts the latter; the single exporter expects URLs inside JSON, although the lean model deliberately omits them. Replace text manifests with one typed photo list shared by every exporter. |
| High | `append_json_queue_to_excel.py:301-312` | Validation errors can terminate a batch; saving the workbook and moving inputs are separate operations. A move failure or interruption can cause duplicate rows on rerun. Store inventory/export identities and retry state transactionally. |
| Medium | `json_to_ebay_excel.py:287` | Default output overwrites the master workbook. Export selected IDs to a new dated artifact, with explicit replacement only. |
| Medium | `requirements.txt` | Direct dependencies `openpyxl` and `PyYAML` are absent; OpenAI is only pulled indirectly through an unpinned Git dependency on an unused agent SDK. Remove unrelated audio/clipboard dependencies and lock the actual requirements. |
| Medium | Renamer and S3 code | Original photos are renamed in place; uploads can partially succeed; filenames are not URL-encoded. Preserve originals, make derived copies, use stable object keys, and verify each required photo before export/publication. |
| Medium | `upload_images_to_s3.py:93` | `public-read` ACLs fail on bucket-owner-enforced buckets. Verify the actual bucket setup and use policy-based delivery. [AWS ACL documentation](https://docs.aws.amazon.com/AmazonS3/latest/userguide/acls.html). |
| Medium | Repository structure | No tests, CI, reproducible package setup, or `.gitignore`; executable binary and Excel lockfile tracked. Archive historical artifacts without losing evidence and remove generated/runtime files from the maintained source tree. |

These findings combine static inspection with one isolated function reproduction. No paid model requests or real uploads were run.

## Proposed user workflow

1. Create a book record and shelf/bin label; upload or capture cover, spine, title page, copyright page, and defects. Support explicit grouping and photo order before attempting automatic grouping.
2. Extract bibliographic facts with evidence pointing to the relevant photos. Keep formal book title separate from the optimized listing title. Record edition, publisher, year, ISBN when present, binding, and observed defects; allow unknown values.
3. Enrich identity from bibliographic sources only when a match is sufficiently specific. An ISBN match is not proof of condition, signature authenticity, or a valuable first printing. Evaluate a metadata provider during the pilot rather than making one a launch dependency.
4. Suggest a price range with dated comparable links, edition/condition differences, and source type. Distinguish asking prices from sold prices. Include acquisition cost, configured selling fees, packaging, and shipping assumptions in a transparent proceeds estimate.
5. Review photos, factual uncertainties, price, description, and policies in one screen. Flag signed, scarce, first-edition, incomplete, and ambiguous copies for closer review. Physical completeness cannot be certified from a few photos.
6. Approve the exact listing revision, then export or publish it. Editing material fields invalidates approval. Repeated clicks and job retries must not create additional listings.
7. Track listing ID, shelf location, sale status, and realized proceeds. Start with status reconciliation; shipping-label purchasing and buyer-message automation are later scope.

## Architecture and deployment

Recommended first implementation: a Python package, FastAPI with a modest responsive server-rendered UI, Pydantic domain schemas, SQLite, a persistent job table and one worker, plus object storage. Keep the application and worker in the same deployable project. Use database migrations from the beginning.

Suggested records: `Book`, `Photo`, `Extraction`, `PriceEvidence`, `ListingRevision`, `Approval`, `PublishAttempt`, and `Job`. Assign a stable SKU to each physical copy. Photo hashes detect accidental repeat uploads; they must not merge two genuine copies of the same edition. Record model/prompt version, cost, evidence, and user edits.

State transitions: captured -> extracted -> needs review -> approved -> publishing -> published -> sold/ended. Failed jobs retain their inputs and error; unknown publication outcomes enter reconciliation rather than immediately retrying a create operation. Persist SKU, offer/listing IDs, request fingerprints, and attempt history. Use transactional job claims with leases and bounded retries for transient failures.

Use the OpenAI SDK directly with `responses.parse` and a Pydantic schema, or the corresponding JSON Schema `text.format`. Validate factual/business constraints separately: schema conformance does not establish factual correctness. Handle refusals and incomplete responses explicitly. Choose a model by evaluating representative books and cost per accepted listing rather than assuming the largest model is necessary. [Official structured outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

| Deployment stage | Proposed arrangement | Reason to choose it |
|---|---|---|
| Local pilot | Windows Python application, localhost UI, SQLite, local originals and existing image storage | Lowest operational burden while validating the workflow. Jobs only run while the host is available. |
| Routine phone/desktop use | Same app in a container on one always-on host, HTTPS, authenticated access, persistent disk, one worker, automatic backups | Makes photo intake accessible and removes dependence on an open terminal. SQLite remains reasonable for the assumed single seller. |
| Increased concurrency | Managed PostgreSQL, separate worker process/service, object storage, durable queue as needed | Adopt when measurements show contention or multiple operators need simultaneous access. |

Do not make a short-lived web request responsible for a whole photo batch. Keep credentials server-side, encrypt stored seller refresh tokens, redact secrets from logs, and restrict access to the review UI. Preserve private originals and expose only listing derivatives through stable delivery URLs or a verified eBay photo-upload route. Do not use expiring URLs as permanent listing images. Test backup restoration, not just backup creation.

Select a hosting vendor after measuring pilot memory, storage, and processing duration; no current vendor price or infrastructure spend is assumed here. Track hosting, storage, and model usage separately. Add health checks, job failure visibility, and a repeatable deployment/rollback procedure. Existing `ngrok.exe` is not a deployment strategy.

## eBay integration decisions

Start with a validated spreadsheet export using a fresh category-specific seller template and import-result reconciliation. Then implement OAuth, seller policy/location retrieval, taxonomy/item specifics, category condition validation, offer creation, publication, revision, and status reconciliation.

Current eBay documentation says Inventory API supports both fixed-price and auction listings, but Inventory-created listings cannot be edited through Seller Hub or another listing platform; revisions must use Inventory API. Therefore the decision is about where the owner wants to maintain listings, not a presumed lack of auction support. Default to spreadsheet publication during the pilot. Choose Inventory API only once the application supports the needed revisions; if continued Seller Hub editing is essential, evaluate a Trading API route in a short compatibility exercise before committing. [Inventory API requirements and restrictions](https://developer.ebay.com/api-docs/sell/inventory/static/overview.html).

Do not make automatic sold-comparable retrieval a launch prerequisite. Marketplace Insights access is restricted, and the official overview redirected to sign-in during research. Verify account-specific access; otherwise use authorized available sources and reviewer-supplied sold evidence. Browse-derived active listing prices must be labeled asking prices. [eBay restricted-access guidance](https://partnernetwork.ebay.com/page/developer-questionnaire).

Keep development and production credentials/data separate. Test in sandbox, then conduct an explicitly approved small live pilot. A sandbox success alone does not prove seller policies, category requirements, images, and production permissions all work.

## GitHub candidates and evidence

Snapshot from GitHub REST API on September 16, 2026. Stars measure adoption, not correctness. Last push can include nonfunctional changes. Activity below is a sample of recently updated issues/PRs, not an exhaustive community audit. Searches included star-sorted eBay listing automation and Python REST clients, followed by README, license, and thread checks.

| Repository | Stars / last push | Activity and assessment |
|---|---|---|
| [hahm-ebay-lister](https://github.com/ashnicholes-droid/hahm-ebay-lister) | 37 / Sep 7, 2026 | 58 forks; photo intake, drafting, and publishing closely match the desired experience. Issue #50 had three user comments and activity Sep 11; recent PRs address batch review and debugging. Strongest workflow reference in this sample, but modest adoption and no local execution audit. |
| [hendt/ebay-api](https://github.com/hendt/ebay-api) | 212 / Aug 11, 2026 | TypeScript API client. Recent security/test PRs and user issues #191/#188 closed Aug 20, each with a comment. Worth considering if choosing TypeScript; insufficient reason alone to rewrite this Python project. MIT. |
| [matecsaj/ebay_rest](https://github.com/matecsaj/ebay_rest) | 71 / Mar 3, 2026 | Python REST wrapper, MIT. Issue #108 reports V1 frozen due to upstream spec/tooling; #109 describes V2 development. Recent sampled threads mostly have no comments. Evaluate exact endpoint coverage before adopting; a small direct HTTP adapter may be easier to maintain. |
| [jbwashington/ebay-mcp](https://github.com/jbwashington/ebay-mcp) | 14 / Mar 8, 2026 | Relevant assistant-operated workflow, MIT. No public issue/PR threads returned in the sample. Useful design reference; limited evidence of community validation. |
| [jjshay/ebay-listing-automation](https://github.com/jjshay/ebay-listing-automation) | 8 / Sep 13, 2026 | Recent Python project, MIT. Sample activity largely dependency-bot PRs and an author PR restoring sample inventory. Recency does not establish user adoption. |
| [timotheus/ebaysdk-python](https://github.com/timotheus/ebaysdk-python) | 856 / May 14, 2023 | Highest eBay-specific star count among these candidates, but stale push history. Do not adopt as the default modern foundation based on popularity alone. |
| [ListingOS-AI](https://github.com/jongan69/ListingOS-AI) | 1 / Aug 31, 2026 | Recently created, but already archived. Exclude as a maintained foundation. |
| [FastAPI](https://github.com/fastapi/fastapi) | 102,366 / Sep 14, 2026 | Active Sep 16 discussion/review across several sampled PRs. Recommended web layer; not an eBay application. |
| [Pydantic](https://github.com/pydantic/pydantic) | 28,780 / Sep 16, 2026 | Sampled issue/PR threads had two to four comments and same-day activity. Recommended validation layer. |
| [Prefect](https://github.com/PrefectHQ/prefect) | 23,848 / Sep 16, 2026 | Active issues/PRs, including human and bot contributions. Relevant to the original README's orchestration idea; defer until multiple scheduled workflows justify another operational component. |

The closest photo-listing candidate describes itself as open source, but its actual [license](https://github.com/ashnicholes-droid/hahm-ebay-lister/blob/main/LICENSE) is FSL-1.1-MIT with a future MIT grant, not immediate MIT. Review those terms before copying code or choosing it as a commercial product base. Borrowing workflow ideas does not require adopting its codebase.

Decision: do not wholesale fork a candidate yet. Keep the book-specific domain model and use established infrastructure libraries. A short isolated comparison of hahm's workflow against our pilot is worthwhile, without connecting production credentials or executing unreviewed deployment scripts.

## Implementation sequence and completion gates

1. **Establish a reliable baseline.** Repair explicit JSON selection, condition mapping, manifests, validation, overwrite behavior, and duplicate handling. Package the actual dependencies; add a lockfile, accurate README, example configuration, and regression checks. Gate: a clean environment prepares the intended books with their correct photos; repeating the batch does not duplicate output.
2. **Create one persistent pipeline.** Introduce schemas, SKU identities, database migrations, job state, immutable originals, and one shared export adapter. Import old JSON/workbook records conservatively, preserving source references and marking uncertain matches for review. Gate: interrupted jobs resume and a failed book does not lose the rest of the batch.
3. **Build intake and review.** Add photo ordering, extraction evidence, editable drafts, price evidence, shelf location, and revision-bound approval. Gate: a representative 30-book set can be prepared without moving files between queues or manually joining URLs.
4. **Connect eBay deliberately.** Validate seller capabilities and choose the publishing route; implement policies, category requirements, sandbox publication and revisions, duplicate prevention, and uncertain-outcome reconciliation. Gate: publication followed by a simulated network interruption produces one listing, and the application can retrieve and revise it through the chosen route.
5. **Deploy and measure.** Package the app/worker, configure authenticated HTTPS and backups, perform a restoration drill, and complete a small approved production pilot. Gate: another session can resume the work; all published items have correct photos, identity, price, status, and location.
6. **Improve selling outcomes.** Add sales/proceeds reports, aging inventory review, and suggested repricing only after the core workflow proves useful. Add auctions, other categories, and other marketplaces according to demonstrated demand.

Initial measurable targets, to validate rather than promise: zero duplicate publications or cross-book photo associations; all material collector claims supported or explicitly flagged; all publication errors visible and recoverable; at least 50% less hands-on preparation time than the current workflow. Record field correction rates, price overrides, rejected exports, time per accepted listing, and model cost. Evaluate ordinary ISBN books alongside older non-ISBN books, ambiguous editions, marked copies, missing jackets, and multiple copies of one title.

The first implementation deliverable should be a dependable photo-to-reviewed-export vertical slice. That gives immediate value while preserving the option to choose the most suitable eBay publication method afterward.
