# Oct 18 To-Do & Workflow

## Current Workflow (reference)

1. **Rename, upload, and log image URLs**  
   ```
   python rename_and_upload_images.py --bucket <bucket> --prefix <prefix> [--root <path>] [--dry-run]
   ```
   - Processes each folder in `batch-image-sets/`
   - Writes `uploaded_urls.txt` (pipe-delimited S3 URLs)

2. **Run GPT-4o agent across all folders**  
   ```
   python gemini-autooutput-runner.py --config gem-yaml-reboot.yaml --output batch-JSON-results [--review]
   ```
   - Generates `<folder>.txt` with `price ||| html ||| condition`

3a. **Single listing workbook (spot check)**  
   ```
   python generate_ebay_upload_excel.py --folder <folder> [--output <dir>]
   ```

3b. **Batch workbook / append flow**  
   ```
   python batch_generate_ebay_workbook.py [--folders <f1> <f2> ...] [--output <dir>] [--append]
   ```

## Priorities & Next Steps

- **YAML refinements**
  - Dial in pricing guidance (ranges, comps, caution with high/low outliers)
  - Emphasise edition / state sensitivity (points of issue, bindings, provenance)
  - Prompt for clearer condition narrative when photos are ambiguous

- **HTML review capability**
  - Restore/extend review flow so each generated HTML can be opened instantly
  - Possibly produce a lightweight preview utility tied to the batch outputs

- **End-to-end automation script**
  - New orchestration Python script should:
    1. Move inbound photos from `/gdrive` into `batch-image-sets/`
    2. Run `rename_and_upload_images.py`
    3. Invoke `gemini-autooutput-runner.py` with the current YAML
    4. Generate Excel via `batch_generate_ebay_workbook.py` (or single-folder flow)
    5. Produce logs/errors for any folder lacking manifests or agent output

- **General polish**
  - Verify title truncation and author extraction on a wider sample
  - Consider handling missing manifests/outputs gracefully (notification + skip)
  - Prep for future batch append enhancements (dedupe, timestamp logging)
