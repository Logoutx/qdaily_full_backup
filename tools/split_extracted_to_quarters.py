"""
One-shot migration: split data/articles_extracted_<year>.jsonl files into
data/articles_extracted_<year>_Q<n>.jsonl based on each row's publish_date.

After a clean run, removes the original per-year files. Idempotent: re-running
when only per-quarter files are present is a no-op.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def quarter_bucket(publish_date: str) -> str:
    """Return 'YYYY_Qn' from a 'YYYY-MM-DD' publish_date; 'unknown' if invalid."""
    if not publish_date or len(publish_date) < 7:
        return "unknown"
    try:
        month = int(publish_date[5:7])
    except ValueError:
        return "unknown"
    if not 1 <= month <= 12:
        return "unknown"
    return f"{publish_date[:4]}_Q{(month - 1) // 3 + 1}"


def main() -> int:
    data_dir = Path("data")
    year_pat = re.compile(r"articles_extracted_(\d{4})\.jsonl")
    year_files = sorted(p for p in data_dir.glob("articles_extracted_*.jsonl")
                        if year_pat.fullmatch(p.name))
    if not year_files:
        print("no per-year files to split — already migrated?")
        return 0

    # buffers keyed by full bucket "YYYY_Qn" (or "unknown")
    out_lines: dict[str, list[str]] = {}
    total_in = 0
    mismatches: list[tuple[int, str, str]] = []  # (id, year_from_file, quarter)

    for yf in year_files:
        file_year = year_pat.fullmatch(yf.name).group(1)
        # split on '\n' to tolerate U+2028 inside body_html
        for line in yf.read_text(encoding="utf-8").split("\n"):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            total_in += 1
            bucket = quarter_bucket(rec.get("publish_date") or "")
            # Sanity: bucket year should match the source file's year (with
            # "unknown" being the only legitimate exception).
            if bucket != "unknown" and not bucket.startswith(file_year + "_"):
                mismatches.append((rec.get("id"), file_year, bucket))
            out_lines.setdefault(bucket, []).append(line)

    # Write each bucket via .tmp → atomic rename.
    for bucket, lines in sorted(out_lines.items()):
        final = data_dir / f"articles_extracted_{bucket}.jsonl"
        tmp = final.with_suffix(final.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line)
                fh.write("\n")
        tmp.replace(final)
        size_mb = final.stat().st_size / (1024 * 1024)
        print(f"  {final.name}: {len(lines):>5} rows · {size_mb:.1f} MB")

    # Only delete originals after every bucket landed cleanly.
    for yf in year_files:
        yf.unlink()
    print(f"\nsplit complete: {total_in} rows from {len(year_files)} files "
          f"→ {len(out_lines)} per-quarter files")
    if mismatches:
        print(f"\n⚠ {len(mismatches)} rows landed in a different year-bucket "
              f"than their source file (first 5):")
        for mid, fyear, bucket in mismatches[:5]:
            print(f"    id={mid}  src=articles_extracted_{fyear}.jsonl  →  {bucket}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
