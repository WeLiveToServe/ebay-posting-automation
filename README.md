# eBay Posting Automation

## Overview
This repository automates the creation and posting of eBay listings using OpenAI’s multimodal models.  
Images of used books are analyzed to extract metadata (title, author, edition, publisher, year, condition, etc.), which is then structured into eBay-ready listing JSON.  

The system produces:
- Structured description fields  
- Dynamic pricing and condition metadata  
- Automatic post formatting for “Buy It Now” or “Auction” modes  

The project is modular and built around **agent YAML definitions** and **tool scripts** for extensibility.

---

## Integration with `llm_agent_utilities`

### Purpose
`llm_agent_utilities` contains the common logic for creating, running, and managing LLM agents.  
Rather than duplicating agent orchestration code, this repo **imports those utilities directly**.  
This ensures:
- All agents (across multiple projects) use the same initialization and execution logic.  
- No manual version control across repos.  
- Immediate access to updates and shared improvements.  

### Structure

Local structure:
```
C:\Users\Keith\dev\projects\
├── ebay-posting-automation\
│   ├── main.py
│   ├── book-identifier-agent.py
│   ├── agent-yamls\
│   │   └── book_identifier.yaml
│   ├── tools\
│   │   └── image_processing.py
│   └── llm_agent_utilities\  ← imported here (shared repo)
└── LLM-agent-utilities\
```

> The `llm_agent_utilities` directory may either exist as a submodule inside the eBay repo or as a sibling repo referenced via `sys.path`.

---

## Import Configuration

To access the shared agent functions:
```python
import sys, os
sys.path.append(os.path.abspath("../LLM-agent-utilities"))
from llm_agent_utilities import load_agent
```

### Explanation
- `sys.path.append()` temporarily adds the utilities repo to Python’s import path.  
- This allows direct imports like:
  ```python
  from llm_agent_utilities import load_agent
  ```
- Python then treats the `llm_agent_utilities` package as if it were installed globally.  
- No need to publish or install it via `pip`; you always use the latest local version.

---

## Example Usage

Run an agent that identifies books from images:

```powershell
python book-identifier-agent.py
```

That script loads the YAML agent configuration:
```yaml
agent-yamls/book_identifier.yaml
```
and executes:
```python
from main import run_agent
run_agent("agent-yamls/book_identifier.yaml", {"image_folder": "images"})
```

The agent calls OpenAI’s model through `llm_agent_utilities` functions and returns structured JSON:
```json
{
  "title": "The Great Gatsby",
  "author": "F. Scott Fitzgerald",
  "edition": "First",
  "year": "1925",
  "publisher": "Scribner"
}
```

---

## Future Expansion

This repo is designed to grow by simply adding new:
- **Agent YAMLs** in `/agent-yamls/`
- **Wrapper scripts** like `/book-identifier-agent.py`
- **Tools** in `/tools/` for custom pre/post-processing

Once stable, the workflow can be automated with **Prefect** to:
- Trigger agents on schedule  
- Monitor eBay listing status daily  
- Generate summaries or alerts automatically

---

## Requirements

See `requirements.txt` for dependencies.

Install them:
```powershell
pip install -r requirements.txt
```

Ensure your OpenAI key is set:
```powershell
setx OPENAI_API_KEY "your_api_key_here"
```
Then open a new PowerShell session before running the scripts.
