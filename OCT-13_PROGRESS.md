## 13 Oct Progress Snapshot

### New & Updated Scripts
- `book-id-agent.py` — extracts bibliographic data from local JPGs and now prints an eBay-ready CSV snippet alongside the JSON it already saved.
- `json_to_ebay_excel.py` — converts the latest agent JSON into a fresh `ebay-auto-listings.xlsx` workbook with all required columns preset (category, price, photo URL, condition, location, business-policy names, and blank AE–AI columns).
- `append_json_queue_to_excel.py` — sweeps `queue-JSONs-to-excel/`, turns each JSON into a listing row, appends it to `ebay-auto-listings.xlsx`, then parks processed files in `queue-JSONs-to-excel/processed/`.
- `batch_book_agent_runner.py` — walks `batch-image-sets/<book>/`, ships each folder’s JPGs through the agent, and drops a matching JSON into `batch-JSON-results/<book>.json`.
- `upload_images_to_s3.py` — pushes JPGs to S3, marks them public, and writes a per-folder `uploaded_urls.txt` that maps filename → permanent URL.

### Directory Expectations
- `images-bookfinder/` – single-run photos for `book-id-agent.py`.
- `batch-image-sets/<book>/` – per-book folders of JPGs for batch processing and S3 uploads.
- `outputs-JSON/` – raw agent responses from ad-hoc runs (latest JSON feeds `json_to_ebay_excel.py`).
- `queue-JSONs-to-excel/` – drop structured JSONs here to append them into the master workbook.
- `batch-JSON-results/` – auto-generated JSONs from the batch runner.

### Typical Workflow
1. Capture book photos into `batch-image-sets/<book>/`.
2. Run `upload_images_to_s3.py --bucket <your-bucket>` to push photos and record public URLs.
3. Execute `batch_book_agent_runner.py` to generate JSON for each folder.
4. Move any JSONs that should become listings into `queue-JSONs-to-excel/` and run `append_json_queue_to_excel.py`.
5. When you need a clean, one-off listing file, run `json_to_ebay_excel.py` (no args) to rebuild `ebay-auto-listings.xlsx`.

### Notes & Next Steps
- S3 bucket must allow public read (bucket policy already set); uploader writes `filename URL` pairs per line.
- `json_to_ebay_excel.py` currently overwrites its output each run—append via the queue script if you need one workbook with many listings.
- Batch scripts trust the JSON returned by the model; add validation once the schema stabilizes.
- Image URL automation now exists; the next enhancement could be wiring those URLs directly into the listing pipeline once naming/prefix conventions are locked in.
