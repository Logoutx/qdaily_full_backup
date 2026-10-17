"""Exclude explicitly held translations; do not reclassify legacy archive files."""
from pathlib import Path

def held_ids(troot: Path) -> set[str]:
    return {p.name for p in (troot / 'held').glob('*') if p.is_dir() and p.name.isascii() and p.name.isdigit()}

def final_output_paths(troot: Path) -> list[Path]:
    held = held_ids(troot)
    return [p for p in (troot / 'en').glob('*.json') if p.stem not in held]
