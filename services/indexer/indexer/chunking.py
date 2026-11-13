"""Turn source rows into chunks: one per product, one or more per document section."""

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

MAX_WORDS = 300  # about 400 tokens of English text
OVERLAP_WORDS = 40  # about 50 tokens

STATUS_TEXT = {
    "in_stock": "In stock",
    "low_stock": "Low stock",
    "out_of_stock": "Out of stock",
    "discontinued": "Discontinued",
}


@dataclass
class Chunk:
    id: uuid.UUID
    source_table: str
    source_id: uuid.UUID
    chunk_no: int
    text: str
    content_hash: str
    is_public: bool
    allowed_group_ids: list[int]
    metadata: dict[str, Any] = field(default_factory=dict)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_chunk(source_table: str, source_id: uuid.UUID, chunk_no: int, text: str,
               is_public: bool, allowed_group_ids: list[int], metadata: dict[str, Any]) -> Chunk:
    return Chunk(
        # Derived from the record's ID and position, so rebuilding gives the same chunk IDs.
        id=uuid.uuid5(source_id, f"chunk:{chunk_no}"),
        source_table=source_table,
        source_id=source_id,
        chunk_no=chunk_no,
        text=text,
        content_hash=content_hash(text),
        is_public=is_public,
        allowed_group_ids=allowed_group_ids,
        metadata=metadata,
    )


def product_chunks(row: dict[str, Any]) -> list[Chunk]:
    text = (
        f"{row['name']} (SKU {row['sku']})\n"
        f"Price: ₹{row['price_inr']:,}\n"
        f"Availability: {STATUS_TEXT[row['status']]}\n"
        f"{row['description']}"
    )
    metadata = {"sku": row["sku"], "name": row["name"],
                "price_inr": row["price_inr"], "status": row["status"]}
    return [make_chunk("products", row["id"], 0, text, True, [], metadata)]


def split_sections(body: str) -> list[tuple[str, str]]:
    """Split Markdown on '## ' headings into (heading, text) pairs."""
    parts = re.split(r"^## ", body, flags=re.MULTILINE)
    sections = []
    if parts[0].strip():  # text before the first heading, if any
        sections.append(("", parts[0].strip()))
    for part in parts[1:]:
        heading, _, text = part.partition("\n")
        sections.append((heading.strip(), text.strip()))
    return sections


def word_windows(text: str) -> list[str]:
    """Split long text into overlapping windows of MAX_WORDS words."""
    words = text.split()
    if len(words) <= MAX_WORDS:
        return [text]
    step = MAX_WORDS - OVERLAP_WORDS
    return [" ".join(words[i:i + MAX_WORDS]) for i in range(0, len(words) - OVERLAP_WORDS, step)]


def document_chunks(row: dict[str, Any]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for heading, text in split_sections(row["body"]):
        # Every chunk starts with the document title, so it still makes sense on its own.
        header = f"{row['title']}\n## {heading}\n" if heading else f"{row['title']}\n"
        metadata = {"kind": row["kind"], "title": row["title"], "heading": heading}
        for piece in word_windows(text):
            chunks.append(make_chunk("documents", row["id"], len(chunks), header + piece,
                                     row["is_public"], row["group_ids"], metadata))
    return chunks