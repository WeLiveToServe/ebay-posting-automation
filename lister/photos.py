"""Photo intake: keep originals untouched, make listing-ready JPEGs, publish them to R2."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import boto3
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from .config import Settings
from .models import Item, Photo

register_heif_opener()  # iPhone HEIC uploads

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}


def add_photo(settings: Settings, item: Item, filename: str, data: bytes) -> Photo:
    """Store an original and its derivative; returns the new Photo (not yet uploaded)."""
    idx = max((p.idx for p in item.photos), default=0) + 1
    suffix = Path(filename).suffix.lower() or ".jpg"
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError(f"Unsupported image type: {filename}")

    original = settings.originals_dir / item.sku / f"{idx:02d}{suffix}"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(data)

    derived = settings.derived_dir / item.sku / f"{item.sku}-{idx:02d}.jpg"
    derived.parent.mkdir(parents=True, exist_ok=True)
    derived.write_bytes(normalize(data, settings.photos.max_edge_px, settings.photos.jpeg_quality))

    photo = Photo(idx=idx, original=str(original), derived=str(derived))
    item.photos.append(photo)
    return photo


def normalize(data: bytes, max_edge: int, quality: int) -> bytes:
    """Apply EXIF rotation, shrink to max_edge, and re-encode as JPEG without metadata (no GPS)."""
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
    return out.getvalue()


def remove_photo(item: Item, idx: int) -> None:
    item.photos = [p for p in item.photos if p.idx != idx]


def move_photo(item: Item, idx: int, delta: int) -> None:
    """Reorder by swapping positions; the first photo is the eBay gallery image."""
    order = [p.idx for p in item.photos]
    pos = order.index(idx)
    new = max(0, min(len(order) - 1, pos + delta))
    item.photos.insert(new, item.photos.pop(pos))


class R2Publisher:
    def __init__(self, settings: Settings):
        cfg = settings.r2
        if not cfg.configured:
            raise RuntimeError("R2 is not configured: set [r2] in config.toml and R2_* in .env")
        self.bucket = cfg.bucket
        self.base_url = cfg.public_base_url.rstrip("/")
        self.prefix = cfg.prefix.strip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{cfg.account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=cfg.access_key_id,
            aws_secret_access_key=cfg.secret_access_key,
            region_name="auto",
        )

    def key_for(self, item: Item, photo: Photo) -> str:
        # Content hash in the key so a replaced photo never serves a stale cached copy.
        digest = hashlib.sha256(Path(photo.derived).read_bytes()).hexdigest()[:10]
        parts = [self.prefix, item.sku, f"{item.sku}-{photo.idx:02d}-{digest}.jpg"]
        return "/".join(p for p in parts if p)

    def publish(self, item: Item) -> None:
        """Upload any photo without a URL. Raises on the first failure so nothing is half-exported."""
        for photo in item.photos:
            if photo.url:
                continue
            key = self.key_for(item, photo)
            self.client.upload_file(
                photo.derived,
                self.bucket,
                key,
                ExtraArgs={"ContentType": "image/jpeg", "CacheControl": "public, max-age=31536000, immutable"},
            )
            photo.url = f"{self.base_url}/{key}"
