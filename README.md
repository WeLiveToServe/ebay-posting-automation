# eBay Posting Automation

This repository automates **eBay listing creation** from minimal input such as book or product images.  
It generates structured data fields — including **title, description, price, and auction/"Buy It Now" options** — and then **posts automatically to eBay** via their API.

## Overview

The system connects with OpenAI's API and the **Agents SDK**, leveraging agent creation utilities from the companion repository **`LLM-agent-utilities`**.  
This allows the pipeline to dynamically create and configure agents using YAML and JSON definitions. The `LLM-agent-utilities` repo provides the reusable logic for:

- Generating structured OpenAI agents  
- Converting image and text inputs into schema-based JSON data  
- Ensuring all required eBay fields (title, price, condition, etc.) are validated  
- Deploying agent outputs to downstream APIs (eBay in this case)

## Core Workflow

1. **Image Input:** User provides an image of a book or other item.  
2. **Agent Invocation:** The `ebay-posting-agent` calls functions from `LLM-agent-utilities` to analyze the image.  
3. **Data Structuring:** Extracted details (title, author, condition, price suggestion) are converted to structured JSON.  
4. **eBay Posting:** The system connects to eBay's API to publish the listing automatically.  

## Recommended Directory Structure

```
ebay-posting-automation/
├── main.py                    # Entrypoint for running the automation
├── agents/
│   └── ebay_posting_agent.yaml # Agent definition calling utilities from LLM-agent-utilities
├── tools/
│   ├── image_processing.py     # Image parsing and OCR logic
│   ├── listing_formatter.py    # Converts raw output to structured eBay fields
│   └── ebay_api_client.py      # Handles API calls for posting and updating listings
├── workflows/
│   └── prefect_flow.py         # (Optional) Prefect-based automation for scheduling & monitoring
├── llm_agent_utilities/        # Submodule link to the shared LLM-agent-utilities repo
├── requirements.txt
└── README.md
```

## Dependencies

- Python ≥ 3.10  
- `openai` (Agents SDK)  
- `pydantic`  
- `requests`  
- `pillow`  
- `llm-agent-utilities` (linked as submodule or local package)

## Future Enhancements

- Add **Prefect** flow for daily status checks on eBay postings.  
- Extend agent logic to include **pricing prediction** based on market data.  
- Add **error logging and retry mechanisms** for failed API calls.

---

**Author:** Keith Harmon  
**Workspace:** WeLiveToServe  
