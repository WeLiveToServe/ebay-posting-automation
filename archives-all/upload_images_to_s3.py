"""
Upload JPG images to Amazon S3 and record their public URLs.

Usage:
    python upload_images_to_s3.py --bucket keith-ebay-images

By default this script expects image folders in ./batch-image-sets and writes
an `uploaded_urls.txt` file inside each subdirectory after uploading.
"""

from __future__ import annotations

import argparse
import mimetypes
from pathlib import Path
from typing import Iterable

import boto3
from botocore.exceptions import BotoCoreError, ClientError

IMAGE_ROOT = Path("batch-image-sets")
OUTPUT_FILENAME = "uploaded_urls.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bucket",
        required=True,
        help="Target S3 bucket name.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=IMAGE_ROOT,
        help=f"Directory containing subfolders of JPGs (default: {IMAGE_ROOT}).",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="Optional key prefix to prepend before <folder>/<filename>.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip uploading files that already have an entry in uploaded_urls.txt.",
    )
    return parser.parse_args()


def iter_image_dirs(root: Path) -> Iterable[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Image root '{root}' does not exist.")
    for path in sorted(root.iterdir()):
        if path.is_dir():
            yield path


def iter_jpgs(directory: Path) -> Iterable[Path]:
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() in {".jpg", ".jpeg"} and path.is_file():
            yield path


def load_existing_urls(directory: Path) -> dict[str, str]:
    url_path = directory / OUTPUT_FILENAME
    if not url_path.is_file():
        return {}
    mapping: dict[str, str] = {}
    for line in url_path.read_text(encoding="utf-8").splitlines():
        if " " not in line:
            continue
        filename, url = line.split(" ", 1)
        mapping[filename.strip()] = url.strip()
    return mapping


def build_object_key(prefix: str, directory: Path, file_path: Path) -> str:
    parts = [prefix] if prefix else []
    parts.append(directory.name)
    parts.append(file_path.name)
    return "/".join(parts)


def build_public_url(bucket: str, region: str, key: str) -> str:
    if region == "us-east-1" or region is None:
        return f"https://{bucket}.s3.amazonaws.com/{key}"
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


def upload_file(client, bucket: str, key: str, file_path: Path) -> None:
    content_type, _ = mimetypes.guess_type(file_path.name)
    extra_args = {"ACL": "public-read"}
    if content_type:
        extra_args["ContentType"] = content_type
    client.upload_file(str(file_path), bucket, key, ExtraArgs=extra_args)


def main() -> None:
    args = parse_args()
    session = boto3.session.Session()
    s3 = session.client("s3")
    region = s3.meta.region_name or session.region_name or "us-east-1"

    for image_dir in iter_image_dirs(args.root):
        existing = load_existing_urls(image_dir)
        url_entries: list[str] = []
        url_entries.extend(f"{name} {url}" for name, url in existing.items())

        processed_files = set(existing.keys()) if args.skip_existing else set()
        new_uploads = 0

        for jpg_path in iter_jpgs(image_dir):
            if jpg_path.name in processed_files:
                continue

            key = build_object_key(args.prefix, image_dir, jpg_path)
            try:
                upload_file(s3, args.bucket, key, jpg_path)
            except (ClientError, BotoCoreError) as exc:
                print(f"Failed to upload {jpg_path}: {exc}")
                continue

            url = build_public_url(args.bucket, region, key)
            url_entries.append(f"{jpg_path.name} {url}")
            new_uploads += 1
            print(f"Uploaded {jpg_path} -> {url}")

        if new_uploads or not (image_dir / OUTPUT_FILENAME).exists():
            (image_dir / OUTPUT_FILENAME).write_text("\n".join(url_entries), encoding="utf-8")
        print(f"{image_dir.name}: {new_uploads} file(s) uploaded.")


if __name__ == "__main__":
    main()
