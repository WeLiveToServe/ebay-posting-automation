# main.py
from llm-agent-utilities import load_agent
from tools.image_processing import process_image
from tools.listing_formatter import format_listing
from tools.ebay_api_client import post_listing

def main(image_path: str):
    """End-to-end workflow: image → structured data → eBay listing."""
    # 1. Process image (extract book data)
    book_data = process_image(image_path)

    # 2. Generate structured listing JSON via LLM agent
    agent = load_agent("agents/ebay_posting_agent.yaml")
    structured_listing = agent.run(book_data)

    # 3. Format listing and post to eBay
    payload = format_listing(structured_listing)
    result = post_listing(payload)

    print("Listing posted:", result)

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python main.py <image_path>")
        sys.exit(1)
    main(sys.argv[1])