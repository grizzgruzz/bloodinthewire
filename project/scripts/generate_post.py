#!/usr/bin/env python3
"""
generate_post.py  v1
====================
Consume the strict-format voice draft output from the wirevoice-core /
VOICE_BIBLE+GENERATOR_PROMPT workflow and produce publish-ready draft
artifacts.

PURPOSE
-------
This script is a MECHANICAL TRANSFORMER ONLY.
It parses, formats, and organizes.
It does NOT invent, improve, rephrase, or fill in any prose.
All narrative content is owned by wirevoice-core.

INPUT FORMAT (strict, as produced by GENERATOR_PROMPT.md)
----------------------------------------------------------
TITLE: <short title>
TIMESTAMP: <HH:MM or OMIT>
BODY:
<1-4 paragraphs>

EVIDENCE_LINE: <single short line>
TAGS: <comma-separated tags>

USAGE
-----
    python generate_post.py \\
        --voice-draft-file path/to/voice_draft.txt \\
        --image-web-path path/to/image.jpg \\
        [--out-dir ../content/drafts] \\
        [--slug-prefix sighting]

OUTPUTS
-------
  <out-dir>/<slug>.md          — draft markdown
  <out-dir>/<slug>.html.frag   — draft HTML fragment
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[generate_post] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("generate_post")

# ─── Defaults ─────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = (SCRIPT_DIR / ".." / "content" / "drafts").resolve()
DEFAULT_SLUG_PREFIX = "sighting"

# ─── Parser ───────────────────────────────────────────────────────────────────

class ParseError(ValueError):
    """Raised when the voice draft does not conform to the strict format."""


def parse_voice_draft(text: str) -> dict[str, str]:
    """
    Parse strict format produced by GENERATOR_PROMPT.md.

    Returns dict with keys: title, timestamp, body, evidence_line, tags_raw.

    Raises ParseError on missing required fields.
    """
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

    result: dict[str, str] = {}

    # TITLE (required)
    m = re.search(r"^TITLE:\s*(.+)$", text, re.MULTILINE)
    if not m:
        raise ParseError("Missing required field: TITLE")
    result["title"] = m.group(1).strip()

    # TIMESTAMP (required key, value may be OMIT)
    m = re.search(r"^TIMESTAMP:\s*(.+)$", text, re.MULTILINE)
    if not m:
        raise ParseError("Missing required field: TIMESTAMP")
    ts_val = m.group(1).strip()
    result["timestamp"] = "" if ts_val.upper() == "OMIT" else ts_val

    # BODY (required; everything between BODY: line and EVIDENCE_LINE:)
    m = re.search(r"^BODY:\s*\n(.*?)(?=^EVIDENCE_LINE:)", text, re.MULTILINE | re.DOTALL)
    if not m:
        raise ParseError("Missing required field: BODY (or improperly terminated)")
    result["body"] = m.group(1).strip()
    if not result["body"]:
        raise ParseError("BODY is empty — this script requires prose from wirevoice-core")

    # EVIDENCE_LINE (required)
    m = re.search(r"^EVIDENCE_LINE:\s*(.+)$", text, re.MULTILINE)
    if not m:
        raise ParseError("Missing required field: EVIDENCE_LINE")
    result["evidence_line"] = m.group(1).strip()

    # TAGS (required)
    m = re.search(r"^TAGS:\s*(.+)$", text, re.MULTILINE)
    if not m:
        raise ParseError("Missing required field: TAGS")
    result["tags_raw"] = m.group(1).strip()

    return result


def parse_tags(tags_raw: str) -> list[str]:
    """Split comma-separated tags, strip whitespace, lowercase."""
    return [t.strip().lower() for t in tags_raw.split(",") if t.strip()]


# ─── Slug ─────────────────────────────────────────────────────────────────────

def make_slug(prefix: str) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{ts}"


# ─── Renderers ────────────────────────────────────────────────────────────────

def render_markdown(parsed: dict[str, str], slug: str, image_web_path: str) -> str:
    """
    Produce a draft markdown file.

    All prose comes directly from parsed dict — no generation here.
    """
    title = parsed["title"]
    timestamp = parsed["timestamp"]
    body = parsed["body"]
    evidence_line = parsed["evidence_line"]
    tags = parse_tags(parsed["tags_raw"])

    # Frontmatter
    fm_lines = [
        "---",
        f'title: "{title}"',
        f"slug: {slug}",
        f"date: {time.strftime('%Y-%m-%d')}",
    ]
    if timestamp:
        fm_lines.append(f"timestamp: {timestamp}")
    fm_lines.append(f"tags: [{', '.join(tags)}]")
    fm_lines.append("status: draft")
    fm_lines.append("voice_owner: wirevoice-core")
    fm_lines.append("---")

    parts = ["\n".join(fm_lines), ""]

    # Title
    parts.append(f"# {title}")
    if timestamp:
        parts.append(f"\n*{timestamp}*")
    parts.append("")

    # Body (pass through verbatim)
    parts.append(body)
    parts.append("")

    # Image
    img_path = Path(image_web_path)
    parts.append(f'![evidence]({image_web_path} "{img_path.name}")')
    parts.append("")

    # Evidence line
    parts.append(f"*{evidence_line}*")
    parts.append("")

    # Tags footer
    tag_str = "  ".join(f"`{t}`" for t in tags)
    parts.append(f"<!-- tags: {tag_str} -->")

    return "\n".join(parts)


def render_html_fragment(parsed: dict[str, str], slug: str, image_web_path: str) -> str:
    """
    Produce a draft HTML fragment.

    All prose comes directly from parsed dict — no generation here.
    """
    import html as _html

    title = parsed["title"]
    timestamp = parsed["timestamp"]
    body = parsed["body"]
    evidence_line = parsed["evidence_line"]
    tags = parse_tags(parsed["tags_raw"])

    img_name = Path(image_web_path).name

    lines = [f'<article class="wire-entry" id="{slug}" data-status="draft" data-voice="wirevoice-core">']

    lines.append(f'  <h2 class="wire-title">{_html.escape(title)}</h2>')
    if timestamp:
        lines.append(f'  <time class="wire-ts">{_html.escape(timestamp)}</time>')

    lines.append('  <div class="wire-body">')
    # Wrap each paragraph in <p>
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    for para in paragraphs:
        # Inline newlines within a paragraph become <br> — preserve original line breaks
        inner = _html.escape(para).replace("\n", "<br>\n    ")
        lines.append(f"    <p>{inner}</p>")
    lines.append("  </div>")

    lines.append('  <figure class="wire-evidence">')
    lines.append(f'    <img src="{_html.escape(image_web_path)}" alt="{_html.escape(img_name)}">')
    lines.append(f'    <figcaption>{_html.escape(evidence_line)}</figcaption>')
    lines.append("  </figure>")

    tag_classes = " ".join(f"tag-{t.replace(' ', '-')}" for t in tags)
    lines.append(f'  <ul class="wire-tags {tag_classes}">')
    for tag in tags:
        lines.append(f"    <li>{_html.escape(tag)}</li>")
    lines.append("  </ul>")

    lines.append("</article>")

    return "\n".join(lines)


# ─── Next-step instructions ───────────────────────────────────────────────────

def emit_next_steps(md_path: Path, html_path: Path, image_web_path: str) -> None:
    print()
    print("=" * 60)
    print("NEXT STEPS — to publish this draft:")
    print("=" * 60)
    print()
    print(f"  Draft markdown:   {md_path}")
    print(f"  Draft HTML frag:  {html_path}")
    print(f"  Image (web):      {image_web_path}")
    print()
    print("  1. Review both files. Do NOT edit the body prose")
    print("     unless you are wirevoice-core regenerating it.")
    print()
    print("  2. Move or copy the HTML fragment into the live site:")
    print(f"       cp {html_path} \\")
    print(f"          /home/gruzz/bloodinthewire/fragments/{html_path.name}")
    print()
    print("  3. Link or embed the fragment from index.html as needed.")
    print()
    print("  4. When satisfied, update draft frontmatter status: draft → published")
    print(f"       sed -i 's/status: draft/status: published/' {md_path}")
    print()
    print("  5. Move the markdown into content/entries/:")
    print(f"       mv {md_path} \\")
    print(f"          {md_path.parent.parent / 'entries' / md_path.name}")
    print()
    print("  6. git add the new/changed files (do not commit until reviewed).")
    print()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Consume wirevoice-core strict-format voice draft "
            "and produce publish-ready draft artifacts. "
            "Does NOT invent prose."
        )
    )
    parser.add_argument(
        "--voice-draft-file", required=True,
        help="Path to text file containing the strict GENERATOR_PROMPT output."
    )
    parser.add_argument(
        "--image-web-path", required=True,
        help="Path or URL to the web-ready image (metadata-stripped)."
    )
    parser.add_argument(
        "--out-dir", default=str(DEFAULT_OUT_DIR),
        help=f"Output directory for draft files (default: {DEFAULT_OUT_DIR})"
    )
    parser.add_argument(
        "--slug-prefix", default=DEFAULT_SLUG_PREFIX,
        help=f"Prefix for draft slugs (default: {DEFAULT_SLUG_PREFIX})"
    )
    args = parser.parse_args()

    # Read voice draft
    draft_path = Path(args.voice_draft_file)
    if not draft_path.is_file():
        log.error("Voice draft file not found: %s", draft_path)
        return 1

    try:
        voice_text = draft_path.read_text(encoding="utf-8")
    except Exception as exc:
        log.error("Could not read voice draft file: %s", exc)
        return 1

    # Parse
    try:
        parsed = parse_voice_draft(voice_text)
    except ParseError as exc:
        log.error("Voice draft parse error: %s", exc)
        log.error("Ensure the draft was produced by wirevoice-core using GENERATOR_PROMPT.md format.")
        return 1

    log.info("Parsed: title=%r  timestamp=%r  tags=%r",
             parsed["title"], parsed["timestamp"] or "OMIT", parsed["tags_raw"])

    # Prepare output dir
    out_dir = Path(args.out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        log.error("Cannot create output dir %s: %s", out_dir, exc)
        return 1

    # Make slug
    slug = make_slug(args.slug_prefix)

    # Render
    md_content = render_markdown(parsed, slug, args.image_web_path)
    html_content = render_html_fragment(parsed, slug, args.image_web_path)

    md_path = out_dir / f"{slug}.md"
    html_path = out_dir / f"{slug}.html.frag"

    try:
        md_path.write_text(md_content, encoding="utf-8")
        log.info("WROTE markdown    → %s", md_path)
    except Exception as exc:
        log.error("Could not write markdown: %s", exc)
        return 1

    try:
        html_path.write_text(html_content, encoding="utf-8")
        log.info("WROTE html frag   → %s", html_path)
    except Exception as exc:
        log.error("Could not write HTML fragment: %s", exc)
        return 1

    # Print paths to stdout (for shell capture)
    print(md_path)
    print(html_path)

    # Next-step instructions to stderr
    emit_next_steps(md_path, html_path, args.image_web_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
