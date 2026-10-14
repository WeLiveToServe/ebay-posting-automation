"""eBay category profiles: which item specifics ("aspects") and conditions each category takes.

Taken from the fx_category_template_EBAY_US workbook eBay generated for this
seller (legacy/ebay-listings-previous/ebay-midsummer-upload-4.xlsx, Aspects and
Categories sheets). To support a new kind of item, add a profile here; the
identifier, exporter and review screen all read from this table.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    id: str
    name: str
    required: tuple[str, ...]
    preferred: tuple[str, ...]
    optional: tuple[str, ...]
    conditions: dict[str, str]
    guidance: str

    @property
    def aspects(self) -> tuple[str, ...]:
        return self.required + self.preferred + self.optional


BOOK_CONDITIONS = {
    "1000": "Brand New",
    "2750": "Like New",
    "4000": "Very Good",
    "5000": "Good",
    "6000": "Acceptable",
}

CATEGORIES: dict[str, Category] = {
    "books": Category(
        key="books",
        id="261186",
        name="/Books & Magazines/Books",
        required=("Author", "Book Title", "Language"),
        preferred=(
            "Topic", "Publisher", "Format", "Genre", "Book Series", "Publication Year",
            "Original Language", "Features", "Type", "Country/Region of Manufacture",
            "Edition", "Narrative Type", "Signed", "Intended Audience",
        ),
        optional=(
            "Signed By", "Vintage", "Ex Libris", "Inscribed", "Personalized", "Era",
            "Illustrator", "Literary Movement", "Number of Pages",
        ),
        conditions=BOOK_CONDITIONS,
        guidance="Ordinary books, including used modern editions and most post-1950 printings.",
    ),
    "antiquarian": Category(
        key="antiquarian",
        id="29223",
        name="/Books & Magazines/Antiquarian & Collectible",
        required=(),
        preferred=(
            "Topic", "Author", "Publisher", "Binding", "Subject", "Special Attributes", "Language",
        ),
        optional=(
            "Character Family", "Original/Facsimile", "Illustrator", "Region", "Signed",
            "Personalized", "Place of Publication", "Year Printed", "Country/Region of Manufacture",
        ),
        conditions=BOOK_CONDITIONS,
        guidance=(
            "Antiquarian or collectible copies: pre-1900 imprints, true first editions/printings of "
            "notable titles, signed or association copies, fine bindings, scarce limited editions."
        ),
    ),
}


def get(key_or_id: str) -> Category:
    for category in CATEGORIES.values():
        if key_or_id in (category.key, category.id):
            return category
    raise KeyError(f"Unknown category {key_or_id!r}; add it to lister/categories.py")
