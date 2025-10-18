# eBay Posting Automation

Automated pipeline for turning antiquarian book photos into eBay-ready listings. The system renames and uploads images, runs an OpenAI agent to produce price/HTML/condition data, and builds minimal Excel upload sheets that can be dropped directly into eBay’s file exchange.

---

## Workflow Overview

1. **Rename & upload photos**  
   ```
   python rename_and_upload_images.py --bucket <bucket> --prefix <prefix> [--root <path>] [--dry-run]
   ```  
   - Reads `batch-image-sets/<folder>/`  
   - Renames images to `<folder>-NN.jpg`, uploads to S3, writes `uploaded_urls.txt`

2. **Generate agent outputs**  
   ```
   python gemini-autooutput-runner.py --config gem-yaml-reboot.yaml --output batch-JSON-results [--review]
   ```  
   - Uses the YAML config to call GPT-4o  
   - Writes `<folder>.txt` to `batch-JSON-results/` in the format `price ||| html ||| condition`  
   - Optional `--review` opens the HTML description locally

3. **Produce Excel uploads**
   - Single listing (spot check):  
     ```
     python generate_ebay_upload_excel.py --folder <folder> [--output <dir>]
     ```  
   - Multi-listing / append:  
     ```
     python batch_generate_ebay_workbook.py [--folders <f1> <f2> ...] [--output <dir>] [--append]
     ```  
     Builds `ebay-upl-MM-DD-HH-MM.xlsx` with required columns and constants, optionally appending to the latest workbook.

Outputs rely on:
- `batch-JSON-results/<folder>.txt` (agent response)
- `batch-image-sets/<folder>/uploaded_urls.txt` (pipe-delimited S3 URLs)

---

## Key Scripts

| Script | Purpose | Important Args |
| ------ | ------- | -------------- |
| `rename_and_upload_images.py` | Rename JPGs, upload to S3, record URL manifest | `--bucket`, `--prefix`, `--root`, `--dry-run` |
| `gemini-autooutput-runner.py` | Run GPT-4o agent across image folders | `--config`, `--output`, `--review` |
| `generate_ebay_upload_excel.py` | Build a single-listing workbook from one folder | `--folder`, `--output` |
| `batch_generate_ebay_workbook.py` | Aggregate many folders into one workbook, or append to existing | `--folders`, `--output`, `--append` |

Each script reads/writes UTF-8 text and standard `.xlsx` using `openpyxl`.

---

## Configuration

- Agent behaviour is defined in `gem-yaml-reboot.yaml`. The system prompt enforces:
  - `price ||| html ||| condition_id` output
  - Raw HTML (no escaping) with a consistent structure
  - Condition ID restricted to eBay-approved values
- Constants for the Excel sheets (category, shipping profiles, etc.) live inside the Excel generator scripts.

---

## Environment Setup

1. Install dependencies  
   ```
   pip install -r requirements.txt
   ```
2. Set OpenAI credentials  
   ```
   setx OPENAI_API_KEY "your_api_key_here"
   ```
   Start a new PowerShell session afterwards.
3. Ensure AWS credentials are configured for S3 uploads (`boto3` reads the default profile).

---

## Future Enhancements

- Harden the agent prompt for pricing ranges and edition nuance
- Add automated HTML review hooks
- Build an orchestration script to move incoming photos, run the full pipeline, and produce Excel outputs headlessly
- Expand validation/tests around Excel generation and URL manifests

---

## Project Structure (abridged)

```
batch-image-sets/           # Source image folders (rename/upload input)
batch-JSON-results/         # Agent outputs (.txt per folder)
archives-all/               # Historical Excel artifacts
generate_ebay_upload_excel.py
batch_generate_ebay_workbook.py
rename_and_upload_images.py
gemini-autooutput-runner.py
gem-yaml-reboot.yaml
OCT-18-TO-DO.md
README.md
```

This repository is kept intentionally modular—scripts can be chained manually or wired into future schedulers/automations as needs evolve.***
