"""
QDaily body_html -> clean markdown, for the zh->en translation pipeline.

Goal: give the translator faithful, minimal markdown that (a) preserves block
structure and EVERY image, and (b) round-trips back to HTML at render time via the
`markdown` lib. Images become ![caption](url) so the model translates the caption
while leaving the URL untouched (the #1 QA invariant).

Tags seen in the corpus: p, span, img, a, div, strong/b, br, h2/h3/h4, em/i,
li/ul, td, blockquote, figure/figcaption.
"""
from __future__ import annotations

import re
from bs4 import BeautifulSoup, NavigableString, Tag

_WS = re.compile(r"[ \t ]+")


def _inline(node) -> str:
    """Render inline content of a tag to markdown text."""
    out = []
    for c in node.children:
        if isinstance(c, NavigableString):
            out.append(str(c))
        elif isinstance(c, Tag):
            if c.name in ("strong", "b"):
                t = _inline(c).strip()
                out.append(f"**{t}**" if t else "")
            elif c.name in ("em", "i"):
                t = _inline(c).strip()
                out.append(f"*{t}*" if t else "")
            elif c.name == "a":
                t = _inline(c).strip()
                href = (c.get("href") or "").strip()
                # keep link text; attach href only if it's a real external link
                out.append(f"[{t}]({href})" if (t and href.startswith("http")) else t)
            elif c.name == "br":
                out.append("\n")
            elif c.name == "img":
                out.append(_img(c))
            else:
                out.append(_inline(c))
    return "".join(out)


def _img(c: Tag, caption: str = "") -> str:
    src = (c.get("src") or c.get("data-src") or "").strip()
    if not src:
        return ""
    alt = caption or (c.get("alt") or "").strip()
    return f"![{alt}]({src})"


def _clean(text: str) -> str:
    # collapse runs of spaces but keep intentional newlines (from <br>)
    lines = [_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines).strip()


def _walk(node, blocks: list[str]) -> None:
    for c in node.children:
        if isinstance(c, NavigableString):
            t = _WS.sub(" ", str(c)).strip()
            if t:
                blocks.append(t)
            continue
        if not isinstance(c, Tag):
            continue
        name = c.name
        if name in ("script", "style"):
            continue
        if name == "p":
            t = _clean(_inline(c))
            if t:
                blocks.append(t)
        elif name in ("h2", "h3", "h4", "h5"):
            t = _clean(_inline(c))
            if t:
                level = {"h2": "##", "h3": "###", "h4": "####", "h5": "#####"}[name]
                blocks.append(f"{level} {t}")
        elif name == "blockquote":
            t = _clean(_inline(c))
            if t:
                blocks.append("\n".join(f"> {ln}" for ln in t.split("\n")))
            else:
                _walk(c, blocks)
        elif name in ("ul", "ol"):
            items = []
            for li in c.find_all("li", recursive=False):
                lt = _clean(_inline(li))
                if lt:
                    items.append(f"- {lt}")
            if items:
                blocks.append("\n".join(items))
        elif name == "figure":
            img = c.find("img")
            cap = c.find("figcaption")
            cap_t = _clean(_inline(cap)) if cap else ""
            if img:
                blocks.append(_img(img, cap_t))
            elif cap_t:
                blocks.append(cap_t)
        elif name == "img":
            s = _img(c)
            if s:
                blocks.append(s)
        elif name == "table":
            # flatten cells to lines; QDaily tables are rare and simple
            for tr in c.find_all("tr"):
                cells = [_clean(_inline(td)) for td in tr.find_all(["td", "th"])]
                row = " | ".join(x for x in cells if x)
                if row:
                    blocks.append(row)
        elif name in ("div", "section", "article", "span", "body", "html", "center"):
            _walk(c, blocks)
        else:
            t = _clean(_inline(c))
            if t:
                blocks.append(t)


def html_to_md(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    blocks: list[str] = []
    root = soup.body or soup
    _walk(root, blocks)
    # de-dupe consecutive identical image lines (corpus sometimes doubles them)
    out: list[str] = []
    for b in blocks:
        if out and b == out[-1] and b.startswith("!["):
            continue
        out.append(b)
    return "\n\n".join(out).strip()


def count_images(md: str) -> int:
    return len(re.findall(r"!\[[^\]]*\]\(", md or ""))


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "tools")
    from validate_corpus import load_corpus
    recs, _, _ = load_corpus("data/articles_extracted_*.jsonl")
    byid = {r["id"]: r for r in recs}
    tid = int(sys.argv[1]) if len(sys.argv) > 1 else 48703
    r = byid[tid]
    md = html_to_md(r.get("body_html") or "")
    print(f"id={tid} chars={len(md)} imgs={count_images(md)} (corpus imgs={len(r.get('images') or [])})\n")
    print(md[:1600])
