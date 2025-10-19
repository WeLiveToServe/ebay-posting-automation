Tonight we locked in the HTML review workflow by editing listings as `.html` in `batch-JSON-results` and letting the queueing script flip them back to `.txt`, keeping Live Preview effortless without breaking the Excel pipeline. We also confirmed review tooling with `review_html_generator.py` so the latest descriptions can be spot-checked quickly before batching.

We chased down the repeated `no url text file found` stop in the Gemini runner and traced it to leftover folders without `uploaded_urls.txt`, so the batch now needs a clean `batch-image-sets/` (or manifest restores) before reprocessing. Drori’s retry and the processed-image stubs highlighted the gap, and tightening error handling remains on deck.
#
# pull in jpgs from drive, execute In gdrive path: (7 min for )
rclone sync gdrive:ebay-upload-pics .

# move folders from gdrive... to batch-image...
Copy-Item C:\source\folder1, C:\source\folder2 -Destination C:\target - -Recurse

# rename and convert to s3 urls, return upated-urls.txt
# took 3 min nb
python rename_and_upload_images.py --bucket keith-ebay-images --prefix books  # use --dry-run to test first

# post to gpt-4o with yaml
python gemini-autooutput-runner.py --config gem-yaml-reboot.yaml --output batch-JSON-results [--review]
# develops issues if the renamer stalls. extra directories cannot be left in batch images sets
# this flow actually needs rework and better error handling. Maybe 


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
  - Leverage `review_html_generator.py` to create `reviews/<folder>.html` (auto-open with `--open`)
  - Consider integrating the review step into the main automation script (batch preview + status)

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
