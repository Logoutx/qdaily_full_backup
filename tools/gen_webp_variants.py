"""
Generate WebP variants for mirrored images.

For every raster asset under assets/<id>/<digest><ext> (jpg/jpeg/png, including
QDaily's '-w600' suffixed names), write a same-size WebP sibling at
assets/<id>/<digest>.webp. render.py emits these as a <picture><source
type="image/webp"> with the original as the <img> fallback, so modern browsers
get the smaller WebP and older ones the original — all from cdn.qdaily.org.

Skips animations (gif/mp4), files already in WebP, and any whose .webp already
exists — so it is resumable and cheap to re-run as the mirror grows.

Keyed by the asset's stem (= sha1(url)[:16]), which is exactly the digest
render.py recomputes from the original URL, so lookups need no manifest.

Usage:
    python -u tools/gen_webp_variants.py [--quality 80] [--workers 4]
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

# Base extensions worth re-encoding to WebP (suffix before any '-w600').
RASTER = {".jpg", ".jpeg", ".png"}
SKIP = {".webp", ".gif", ".mp4", ".bin"}


def base_ext(p: Path) -> str:
    # "082cab11e66e538d.jpg-w600" -> ".jpg" ; "x.jpg" -> ".jpg"
    return p.suffix.lower().split("-", 1)[0]


def webp_target(p: Path) -> Path:
    # stem strips the full suffix (incl. -w600) -> the bare digest
    return p.with_name(p.stem + ".webp")


def candidates(assets: Path) -> list[Path]:
    out = []
    for p in assets.rglob("*"):
        if not p.is_file():
            continue
        ext = base_ext(p)
        if ext in SKIP or ext not in RASTER:
            continue
        if webp_target(p).exists():
            continue
        out.append(p)
    return out


def convert(p: Path, quality: int) -> tuple[Path, str]:
    target = webp_target(p)
    try:
        with Image.open(p) as im:
            if getattr(im, "n_frames", 1) > 1:
                return p, "skip-animated"
            # Preserve alpha (PNG) as RGBA; flatten odd modes to RGB.
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
            else:
                im = im.convert("RGB")
            tmp = target.with_suffix(".webp.tmp")
            im.save(tmp, "WEBP", quality=quality, method=6)
            tmp.rename(target)
        return p, "ok"
    except Exception as e:  # noqa: BLE001 — log and continue
        return p, f"error: {type(e).__name__}: {str(e)[:80]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", default="assets")
    ap.add_argument("--quality", type=int, default=80)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    assets = Path(args.assets)
    todo = candidates(assets)
    if args.limit:
        todo = todo[: args.limit]
    print(f"webp variants to generate: {len(todo):,} "
          f"(quality={args.quality}, {args.workers}w)", flush=True)
    if not todo:
        print("nothing to do.")
        return 0

    n_ok = n_skip = n_err = 0
    bytes_in = bytes_out = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(convert, p, args.quality): p for p in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            p, st = fut.result()
            if st == "ok":
                n_ok += 1
                try:
                    bytes_in += p.stat().st_size
                    bytes_out += webp_target(p).stat().st_size
                except OSError:
                    pass
            elif st.startswith("skip"):
                n_skip += 1
            else:
                n_err += 1
                if n_err <= 10:
                    print(f"  {st}  {p}", flush=True)
            if i % 2000 == 0:
                print(f"  {i:,}/{len(todo):,}  ok={n_ok} skip={n_skip} err={n_err}",
                      flush=True)

    saved = (1 - bytes_out / bytes_in) * 100 if bytes_in else 0
    print(f"\ndone. ok={n_ok} skip={n_skip} err={n_err}; "
          f"size {bytes_in/1e6:.1f}MB -> {bytes_out/1e6:.1f}MB "
          f"({saved:.0f}% smaller on converted)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
