"""
Generate small downscaled thumbnails of every worklist image and repoint the
alt-caption worklist at them, so the vision captioner reads ~512px JPEGs instead
of full-resolution photos. Image input tokens scale with pixel area, so this cuts
the per-image caption cost ~5x with no real loss for "describe what's visible".

Reads  data/alt_worklist.jsonl  ({url, asset, id, title, ctx, kind})
Writes data/caption_thumbs/<asset path>.jpg  (one ≤MAXPX JPEG per image)
Rewrites the worklist in place: "asset" -> the thumb path, keeping the original
under "orig_asset". Order/line-count are preserved (the scheduler's cursor stays
valid). Resumable: existing thumbs are reused; rerun any time.

Canonical flow when you rebuild the worklist:
    python tools/build_alt_worklist.py      # full-res asset paths
    python tools/gen_caption_thumbs.py      # -> thumbs, rewrites worklist
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

WORKLIST = Path("data/alt_worklist.jsonl")
THUMB_ROOT = Path("data/caption_thumbs")
MAXPX = 512          # longest edge, in pixels
QUALITY = 80


def thumb_path(asset: str) -> Path:
    # mirror the asset's relative path under THUMB_ROOT, force a .jpg suffix
    return THUMB_ROOT / (asset + ".jpg")


def make_thumb(src: Path, dst: Path) -> bool:
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((MAXPX, MAXPX))  # in place, preserves aspect ratio
            dst.parent.mkdir(parents=True, exist_ok=True)
            im.save(dst, "JPEG", quality=QUALITY)
        return True
    except Exception as e:
        print(f"  ! {src}: {type(e).__name__}: {e}")
        return False


def main() -> int:
    rows = [json.loads(l) for l in WORKLIST.read_text(encoding="utf-8").splitlines() if l.strip()]
    made = reused = failed = 0
    out = []
    for r in rows:
        orig = r.get("orig_asset") or r["asset"]  # idempotent on reruns
        src = Path(orig)
        dst = thumb_path(orig)
        if dst.exists() and dst.stat().st_size > 0:
            reused += 1
            ok = True
        else:
            ok = make_thumb(src, dst)
            if ok:
                made += 1
            else:
                failed += 1
        if ok:
            r["orig_asset"] = orig
            r["asset"] = str(dst)
        out.append(r)
        n = made + reused + failed
        if n % 1000 == 0:
            print(f"  {n}/{len(rows)} (made={made} reused={reused} failed={failed})")

    WORKLIST.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n",
                        encoding="utf-8")
    print(f"thumbs: {made} made, {reused} reused, {failed} failed; "
          f"worklist repointed ({len(out)} rows) -> {WORKLIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
