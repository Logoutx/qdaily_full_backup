"""
Mirror the local ``assets/`` tree to a Cloudflare R2 bucket.

Files are content-addressed (``assets/<article_id>/<sha1_16>.<ext>``), so the
upload diff is *strictly additive*: a key already in the bucket never needs to
be overwritten. We list the bucket once, build a set of keys + sizes, then walk
``assets/`` and upload only files whose key+size pair isn't already there.

Credentials come from standard AWS env vars; the R2 endpoint is derived from
``R2_ACCOUNT_ID``::

    AWS_ACCESS_KEY_ID=...          (R2 access key id)
    AWS_SECRET_ACCESS_KEY=...      (R2 secret access key)
    R2_ACCOUNT_ID=<32-char hex>    (find on Cloudflare R2 dashboard)

Designed to run from CI (idempotent, exits 0 with no work) and locally
(``--dry-run`` to preview).
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.config import Config


# Long, immutable Cache-Control because every asset path embeds a sha1.
# Bumping the source URL means a new digest means a new key.
CACHE_CONTROL = "public, max-age=31536000, immutable"

# Map odd QDaily-style extensions (e.g. ``.jpg-w600``) to the right MIME type.
# Anything not listed falls through to ``mimetypes.guess_type``.
EXT_MIME_OVERRIDES = {
    ".jpg-w600": "image/jpeg",
    ".jpeg-w600": "image/jpeg",
    ".png-w600": "image/png",
    ".webp-w600": "image/webp",
    ".gif-w600": "image/gif",
}


def guess_content_type(path: Path) -> str:
    name = path.name.lower()
    for suffix, mime in EXT_MIME_OVERRIDES.items():
        if name.endswith(suffix):
            return mime
    mime, _ = mimetypes.guess_type(name)
    return mime or "application/octet-stream"


def make_client(account_id: str):
    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        # R2's S3 API quirk: it ignores the region but the SDK demands one.
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 5, "mode": "standard"},
            # A modest pool sized to our upload concurrency.
            max_pool_connections=32,
        ),
    )


def list_remote(client, bucket: str) -> dict[str, int]:
    """Return {key: size} for every object currently in the bucket."""
    out: dict[str, int] = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents") or []:
            out[obj["Key"]] = obj["Size"]
    return out


def walk_local(assets_root: Path) -> list[tuple[str, Path, int]]:
    """Return [(key, local_path, size)] for every regular file under assets_root."""
    out: list[tuple[str, Path, int]] = []
    for p in assets_root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(assets_root)
        # Use forward slashes for the object key, regardless of host OS.
        key = "/".join(rel.parts)
        out.append((key, p, p.stat().st_size))
    return out


def upload_one(client, bucket: str, key: str, path: Path) -> tuple[str, str | None]:
    """Upload one file. Returns (key, error-message or None)."""
    try:
        with path.open("rb") as fh:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=fh,
                ContentType=guess_content_type(path),
                CacheControl=CACHE_CONTROL,
            )
        return key, None
    except Exception as e:  # noqa: BLE001 — surface every failure to the caller
        return key, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bucket", default=os.environ.get("R2_BUCKET", "qdaily-assets"))
    ap.add_argument("--assets", default="assets",
                    help="local asset directory to mirror")
    ap.add_argument("--account-id", default=os.environ.get("R2_ACCOUNT_ID"),
                    help="Cloudflare account id (or set R2_ACCOUNT_ID)")
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent uploads (R2 free tier handles 8 easily)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would upload without sending bytes")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap on number of uploads this run (smoke-test aid)")
    ap.add_argument("--prune", action="store_true",
                    help="DELETE remote objects that no longer exist locally "
                         "(e.g. GIFs replaced by MP4). Lists them and requires "
                         "--yes to actually delete; otherwise dry-run only.")
    ap.add_argument("--yes", action="store_true",
                    help="confirm destructive --prune deletions")
    args = ap.parse_args()

    if not args.account_id:
        print("error: R2_ACCOUNT_ID env var or --account-id required", file=sys.stderr)
        return 2

    assets_root = Path(args.assets)
    if not assets_root.is_dir():
        print(f"error: {assets_root} is not a directory", file=sys.stderr)
        return 2

    client = make_client(args.account_id)

    print(f"listing remote bucket {args.bucket} ...")
    remote = list_remote(client, args.bucket)
    print(f"  remote: {len(remote):,} objects")

    local = walk_local(assets_root)
    print(f"  local:  {len(local):,} files")

    # Diff: upload if key missing OR size differs.
    to_upload: list[tuple[str, Path, int]] = []
    size_mismatch = 0
    for key, path, size in local:
        r_size = remote.get(key)
        if r_size is None:
            to_upload.append((key, path, size))
        elif r_size != size:
            size_mismatch += 1
            to_upload.append((key, path, size))

    print(f"  to upload: {len(to_upload):,}  (of which {size_mismatch} size-mismatch overwrites)")

    if args.limit is not None:
        to_upload = to_upload[: args.limit]
        print(f"  --limit applied: {len(to_upload):,}")

    if args.dry_run:
        for key, path, size in to_upload[:20]:
            print(f"  DRY {key}  ({size:,} B)")
        if len(to_upload) > 20:
            print(f"  ... and {len(to_upload) - 20} more")
        return 0

    if not to_upload:
        print("nothing to do — bucket already mirrors local assets")
        return 0

    ok = err = 0
    errors: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(upload_one, client, args.bucket, key, path): key
                   for key, path, _ in to_upload}
        for i, fut in enumerate(as_completed(futures), 1):
            key, err_msg = fut.result()
            if err_msg is None:
                ok += 1
            else:
                err += 1
                errors.append((key, err_msg))
            if i % 100 == 0 or i == len(to_upload):
                print(f"  {i}/{len(to_upload)}  ok={ok}  err={err}")

    if errors:
        print(f"\n{err} failures (first 5):", file=sys.stderr)
        for key, msg in errors[:5]:
            print(f"  {key}: {msg}", file=sys.stderr)
        return 1
    print(f"done. uploaded {ok} files.")

    # Optional prune: delete remote keys with no local counterpart.
    if args.prune:
        local_keys = {key for key, _, _ in local}
        stale = sorted(k for k in remote if k not in local_keys)
        stale_bytes = sum(remote[k] for k in stale)
        print(f"\nprune: {len(stale):,} remote objects not present locally "
              f"({stale_bytes/1024/1024:.1f} MB)")
        for k in stale[:15]:
            print(f"  stale: {k}")
        if len(stale) > 15:
            print(f"  ... and {len(stale) - 15} more")
        if not stale:
            print("  nothing to prune.")
        elif not args.yes:
            print("  DRY RUN — re-run with --prune --yes to delete these.")
        else:
            deleted = 0
            # S3 delete_objects takes up to 1000 keys per call.
            for i in range(0, len(stale), 1000):
                batch = [{"Key": k} for k in stale[i:i + 1000]]
                client.delete_objects(Bucket=args.bucket, Delete={"Objects": batch})
                deleted += len(batch)
                print(f"  deleted {deleted}/{len(stale)}")
            print(f"prune done. removed {deleted} objects.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
