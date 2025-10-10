def format_listing(structured_listing: dict) -> dict:
    """
    Converts structured agent output into an eBay API payload.
    """
    payload = {
        "title": structured_listing.get("title", "Untitled Item"),
        "description": structured_listing.get("description", "No description"),
        "price": structured_listing.get("price", 0.0),
        "listingType": structured_listing.get("listingType", "FixedPriceItem")
    }
    return payload
