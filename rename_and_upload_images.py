"""
Rename JPGs in batch-image-sets, upload to S3, and save comma-separated URLs.

Usage:
    python rename_and_upload_images.py --bucket keith-ebay-images --prefix books

This script renames files like <folder>/<img>.jpg -> <folder>/<folder>-01.jpg,
uploads them to S3 under prefix/<folder>/<folder>-01.jpg (public-read), and writes
uploaded_urls.txt with comma-separated URLs for easy pasting into eBay templates.
"""

from __future__ import annotations

import argparse
import mimetypes
from pathlib import Path
from typing import Iterable

import boto3
from botocore.exceptions import BotoCoreError, ClientError

ROOT_DIR = Path("batch-image-sets")
OUTPUT_FILENAME = "uploaded_urls.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True, help="S3 bucket name.")
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT_DIR,
        help=f"Root directory containing child folders of JPGs (default: {ROOT_DIR})",
    )
    parser.add_argument("--prefix", default="", help="Optional S3 key prefix (e.g., books/.).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned renames/uploads without performing them.",
    )
    return parser.parse_args()


def iter_image_folders(root: Path) -> Iterable[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Image root '{root}' does not exist.")
    for path in sorted(root.iterdir()):
        if path.is_dir():
            yield path


def iter_jpgs(directory: Path) -> list[Path]:
    return sorted(
        [path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg"}],
        key=lambda p: p.name,
    )


def ensure_two_digit(index: int) -> str:
    return f"{index:02d}"


def rename_images(directory: Path, dry_run: bool = False) -> list[Path]:
    jpgs = iter_jpgs(directory)
    renamed_paths: list[Path] = []
    for idx, image_path in enumerate(jpgs, start=1):
        target_name = f"{directory.name}-{ensure_two_digit(idx)}{image_path.suffix.lower()}"
        target_path = directory / target_name
        if image_path.name == target_name:
            renamed_paths.append(target_path)
            continue
        print(f"Rename: {image_path.name} -> {target_name}")
        if not dry_run:
            image_path.rename(target_path)
        renamed_paths.append(target_path)
    return renamed_paths


def build_s3_key(prefix: str, directory: Path, file_path: Path) -> str:
    parts = []
    if prefix:
        parts.append(prefix.rstrip("/"))
    parts.append(directory.name)
    parts.append(file_path.name)
    return "/".join(parts)


def public_url(bucket: str, region: str, key: str) -> str:
    if region == "us-east-1" or not region:
        return f"https://{bucket}.s3.amazonaws.com/{key}"
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


def upload_files(
    client,
    bucket: str,
    directory: Path,
    files: list[Path],
    prefix: str,
    dry_run: bool,
) -> list[str]:
    urls: list[str] = []
    region = client.meta.region_name or boto3.session.Session().region_name or "us-east-1"
    for file_path in files:
        key = build_s3_key(prefix, directory, file_path)
        if dry_run:
            print(f"Upload (dry-run): {file_path} -> s3://{bucket}/{key}")
            url = public_url(bucket, region, key)
            urls.append(url)
            continue
        try:
            extra_args = {"ACL": "public-read"}
            content_type, _ = mimetypes.guess_type(file_path.name)
            if content_type:
                extra_args["ContentType"] = content_type
            client.upload_file(str(file_path), bucket, key, ExtraArgs=extra_args)
        except (ClientError, BotoCoreError) as exc:
            print(f"Failed to upload {file_path}: {exc}")
            continue
        url = public_url(bucket, region, key)
        urls.append(url)
        print(f"Uploaded {file_path.name} -> {url}")
    return urls


def write_url_manifest(directory: Path, urls: list[str]) -> None:
    manifest_path = directory / OUTPUT_FILENAME
    manifest_path.write_text(", ".join(urls), encoding="utf-8")
    print(f"{directory.name}: wrote {manifest_path.name}")


def process_directory(directory: Path, s3_client, bucket: str, prefix: str, dry_run: bool) -> None:
    renamed = rename_images(directory, dry_run=dry_run)
    if not renamed:
        print(f"{directory.name}: no JPG files to process.")
        return
    urls = upload_files(s3_client, bucket, directory, renamed, prefix, dry_run=dry_run)
    if urls and not dry_run:
        write_url_manifest(directory, urls)
    elif dry_run:
        print(f"{directory.name}: generated {len(urls)} URL(s) (dry-run)")


def main() -> None:
    args = parse_args()
    s3_client = boto3.client("s3")

    for folder in iter_image_folders(args.root):
        print(f"\nProcessing {folder.name}...")
        process_directory(folder, s3_client, args.bucket, args.prefix, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
