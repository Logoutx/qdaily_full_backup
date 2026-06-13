"""
Merge the per-batch TSV part files written by the alt-caption Workflow into
data/image_alts.json (the map render.py reads: original-image-URL -> alt).

Each part file (data/alt_parts/part_*.tsv) has lines:  <url><TAB><alt>
Empty alts are dropped (those images get no alt, same as before). Existing
entries in image_alts.json are preserved; new captions are merged in.
Idempotent — safe to run repeatedly as more parts land.

Usage: python tools/merge_alt_parts.py
"""
from __future__ import annotations

import glob
import json
from pathlib import Path


def main() -> int:
    dest = Path("data/image_alts.json")
    alts: dict[str, str] = {}
    if dest.exists():
        cur = json.loads(dest.read_text(encoding="utf-8"))
        for k, v in cur.items():
            alt = v.get("alt") if isinstance(v, dict) else v
            if alt:
                alts[k] = alt.strip()

    before = len(alts)
    n_lines = n_empty = n_bad = 0
    for pf in sorted(glob.glob("data/alt_parts/part_*.tsv")):
        for line in Path(pf).read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            n_lines += 1
            if "\t" not in line:
                n_bad += 1
                continue
            url, alt = line.split("\t", 1)
            url = url.strip()
            alt = alt.strip()
            if not url:
                n_bad += 1
                continue
            if not alt:
                n_empty += 1
                continue
            alts[url] = alt

    dest.write_text(
        json.dumps(alts, ensure_ascii=False, indent=0, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"merged parts: {n_lines:,} lines read "
          f"(empty={n_empty:,} malformed={n_bad:,}); "
          f"image_alts.json {before:,} -> {len(alts):,} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
