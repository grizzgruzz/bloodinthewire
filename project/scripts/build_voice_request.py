#!/usr/bin/env python3
"""
build_voice_request.py  v1
==========================
Build a ready-to-use prompt file for the wirevoice-core agent.

PURPOSE
-------
Assemble a single, self-contained prompt file that can be fed directly to
wirevoice-core to generate a new Blood in the Wire post.

The script gathers:
  - The VOICE_BIBLE.md (canonical voice rules)
  - The GENERATOR_PROMPT.md (output format + voice execution rules)
  - The latest published note sidecar (if available)
  - The web-ready image path
  - Optional overrides for all GENERATOR_PROMPT inputs

It does NOT write any narrative.  Content production belongs to wirevoice-core.

USAGE
-----
    # Auto-pair note to image (by stem/basename match in published/):
    python build_voice_request.py \\
        --image-web-path assets/web/some_image.jpg

    # Fully auto: latest web image, note auto-paired by basename:
    python build_voice_request.py

    # Full explicit control:
    python build_voice_request.py \\
        --note-file assets/published/some__ts.note.txt \\
        --image-web-path assets/web/some_image.jpg \\
        --seed-context "prior entry mentioned the same car parked twice" \\
        --motif-focus wrong_people \\
        --intensity medium \\
        --length-mode medium \\
        --recurring-names "Caleb Dunn,Mara,Officer Leal" \\
        --out-file voice_requests/request-20260317.txt

OUTPUT
------
  A single text file containing the fully assembled prompt for wirevoice-core.
  Pipe it or paste it directly into a wirevoice-core session.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_DIR  = (SCRIPT_DIR / "..").resolve()
ASSETS_DIR   = PROJECT_DIR / "assets"
PUBLISHED_DIR = ASSETS_DIR / "published"
WEB_DIR       = ASSETS_DIR / "web"
VOICE_DIR     = PROJECT_DIR / "voice"
VOICE_BIBLE   = VOICE_DIR / "VOICE_BIBLE.md"
GENERATOR_PROMPT = VOICE_DIR / "GENERATOR_PROMPT.md"
REQUESTS_DIR  = PROJECT_DIR / "voice" / "requests"

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[build_voice_request] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("build_voice_request")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def find_paired_note(image_web_path: Path | str | None) -> Path | None:
    """
    Find the .note.txt in published/ that was archived alongside the given
    web image.

    Matching strategy
    -----------------
    The published/ filename pattern is ``{stem}__{timestamp}{ext}`` for
    images and ``{stem}__{timestamp}.note.txt`` for sidecars.  Both share
    the same ``{stem}`` (original basename without extension).

    Given the *web* image path (assets/web/{stem}_{timestamp}{ext}), we
    derive the original stem (strip the timestamp suffix), then look for a
    published note whose stem matches that original stem.

    If ``image_web_path`` is None or no match is found, returns None so
    callers can fall back gracefully.
    """
    if not PUBLISHED_DIR.is_dir():
        return None
    if image_web_path is None:
        return None

    img = Path(image_web_path)
    # web/ names use a single underscore separator: {stem}_{timestamp}{ext}
    # published/ names use a double-underscore separator: {stem}__{timestamp}{ext}
    # Derive the original stem by stripping the last _<timestamp> segment.
    # We split on "__" (published archive separator) first; if the web name
    # has the single-underscore form, reconstruct from that.
    #
    # Canonical approach: the published/ archive is the ground truth.
    # Scan published/ for a .note.txt whose non-timestamp stem prefix matches
    # the image's non-timestamp stem prefix.

    def _base_stem(name: str) -> str:
        """Strip the __<timestamp> or _<timestamp> suffix to get the original stem."""
        # Try double-underscore (published/ form) first
        if "__" in name:
            return name.split("__")[0]
        # Try single-underscore with a timestamp-shaped suffix (web/ form):
        # last segment looks like YYYYMMDD-HHMMSS
        import re
        m = re.match(r"^(.+)_(\d{8}-\d{6})$", name)
        if m:
            return m.group(1)
        # No recognised pattern — use full stem
        return name

    target_base = _base_stem(img.stem)

    # Look for matching note in published/
    candidates = sorted(
        PUBLISHED_DIR.glob("*.note.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,  # newest first so we pick the most recent if multiple match
    )
    for note in candidates:
        note_base = _base_stem(note.stem)  # note stem is e.g. "2ceb9b27-...__20260318-003852"
        if note_base == target_base:
            log.info(
                "PAIRED note: %s → matched to image stem %r",
                note.name, target_base,
            )
            return note

    log.debug("No paired note found in published/ for image stem %r", target_base)
    return None


def find_latest_web_image() -> Path | None:
    """Return the most recently modified image in web/."""
    if not WEB_DIR.is_dir():
        return None
    exts = {".jpg", ".jpeg", ".png"}
    images = sorted(
        (p for p in WEB_DIR.iterdir() if p.suffix.lower() in exts and p.name != ".gitkeep"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return images[0] if images else None


# ─── Builder ──────────────────────────────────────────────────────────────────

def build_request(
    note_file: Path | None,
    image_web_path: Path | str,
    seed_context: str,
    motif_focus: str,
    intensity: str,
    length_mode: str,
    recurring_names: list[str],
) -> str:
    """
    Assemble the full prompt string to feed to wirevoice-core.
    """
    # Load canonical docs
    if not VOICE_BIBLE.is_file():
        raise FileNotFoundError(f"VOICE_BIBLE.md not found at {VOICE_BIBLE}")
    if not GENERATOR_PROMPT.is_file():
        raise FileNotFoundError(f"GENERATOR_PROMPT.md not found at {GENERATOR_PROMPT}")

    voice_bible_text  = VOICE_BIBLE.read_text(encoding="utf-8")
    gen_prompt_text   = GENERATOR_PROMPT.read_text(encoding="utf-8")

    # Load note
    image_note = ""
    if note_file and note_file.is_file():
        raw = note_file.read_text(encoding="utf-8").strip()
        # Strip "NOTE: " prefix if present
        if raw.startswith("NOTE:"):
            image_note = raw[len("NOTE:"):].strip()
        else:
            image_note = raw
        log.info("NOTE loaded from %s", note_file.name)
    else:
        log.info("No note file — image_note will be empty")

    # Resolve image path
    img_str = str(image_web_path)

    # Build the assembled prompt
    parts: list[str] = []

    parts.append("=" * 70)
    parts.append("WIREVOICE-CORE REQUEST")
    parts.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    parts.append("=" * 70)
    parts.append("")
    parts.append("You are wirevoice-core. Read the VOICE_BIBLE below carefully, then")
    parts.append("use the GENERATOR_PROMPT to produce exactly one new post draft.")
    parts.append("Return ONLY the strict output format. No preamble, no explanation.")
    parts.append("")

    parts.append("─" * 70)
    parts.append("VOICE_BIBLE (canonical — do not deviate)")
    parts.append("─" * 70)
    parts.append(voice_bible_text)
    parts.append("")

    parts.append("─" * 70)
    parts.append("GENERATOR_PROMPT (format + execution rules)")
    parts.append("─" * 70)
    parts.append(gen_prompt_text)
    parts.append("")

    parts.append("─" * 70)
    parts.append("INVOCATION INPUTS")
    parts.append("─" * 70)
    parts.append("")

    parts.append(f"seed_context: {seed_context or '(none — infer from motif focus)'}")
    parts.append(f"image_note: {image_note or '(none)'}")
    parts.append(f"image_path: {img_str}")
    if recurring_names:
        import json
        parts.append(f"recurring_names: {json.dumps(recurring_names)}")
    else:
        parts.append("recurring_names: []")
    parts.append(f"motif_focus: {motif_focus or '(choose appropriate motif)'}")
    parts.append(f"intensity: {intensity}")
    parts.append(f"length_mode: {length_mode}")
    parts.append("")

    parts.append("─" * 70)
    parts.append("INSTRUCTION")
    parts.append("─" * 70)
    parts.append("")
    parts.append("Generate ONE new Blood in the Wire post draft using the inputs above.")
    parts.append("Return ONLY the strict output format from GENERATOR_PROMPT.md:")
    parts.append("")
    parts.append("  TITLE: ...")
    parts.append("  TIMESTAMP: ...")
    parts.append("  BODY:")
    parts.append("  ...")
    parts.append("")
    parts.append("  EVIDENCE_LINE: ...")
    parts.append("  TAGS: ...")
    parts.append("")
    parts.append("Nothing else. No commentary. No preamble. Just the block above.")
    parts.append("")

    return "\n".join(parts)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a wirevoice-core prompt bundle from latest published "
            "note+image (or explicit args). Does NOT write any content."
        )
    )
    parser.add_argument(
        "--note-file", default=None,
        help="Path to a .note.txt sidecar. Auto-detected if omitted."
    )
    parser.add_argument(
        "--image-web-path", default=None,
        help="Path to the web-ready image. Auto-detected from assets/web/ if omitted."
    )
    parser.add_argument(
        "--seed-context", default="",
        help="Short context from current timeline (optional)."
    )
    parser.add_argument(
        "--motif-focus", default="",
        choices=["", "wrong_people", "directed_patterns", "god_signals",
                 "missing_time", "utility_workers", "implied_retribution"],
        help="Motif to emphasise (optional; voice agent may choose)."
    )
    parser.add_argument(
        "--intensity", default="medium",
        choices=["low", "medium", "high"],
        help="Post intensity level (default: medium)."
    )
    parser.add_argument(
        "--length-mode", default="medium",
        choices=["short", "medium", "long", "mixed"],
        help="Post length mode (default: medium)."
    )
    parser.add_argument(
        "--recurring-names", default="",
        help="Comma-separated known recurring character names (optional)."
    )
    parser.add_argument(
        "--out-file", default=None,
        help="Output file path. Defaults to voice/requests/request-<timestamp>.txt"
    )
    args = parser.parse_args()

    # Resolve note file
    note_file: Path | None
    if args.note_file:
        note_file = Path(args.note_file)
        if not note_file.is_file():
            log.error("Note file not found: %s", note_file)
            return 1
    else:
        # Auto-detect: match the note to the selected image by basename stem.
        # This prevents accidentally loading an unrelated published note.
        # We resolve the image path first so we can use it for pairing.
        if args.image_web_path:
            _img_for_pairing: Path | str | None = args.image_web_path
        else:
            _img_for_pairing = find_latest_web_image()

        note_file = find_paired_note(_img_for_pairing)
        if note_file:
            log.info("Auto-paired note: %s", note_file.name)
        else:
            log.info("No paired note found in published/ for this image — proceeding without note")

    # Resolve image path
    if args.image_web_path:
        image_web_path = args.image_web_path
    else:
        detected = find_latest_web_image()
        if detected:
            image_web_path = str(detected)
            log.info("Auto-detected web image: %s", detected.name)
        else:
            log.error(
                "No image found in assets/web/ and --image-web-path not provided. "
                "Run select_asset.py first."
            )
            return 1

    # Parse recurring names
    recurring_names = [n.strip() for n in args.recurring_names.split(",") if n.strip()]

    # Build request
    try:
        prompt_text = build_request(
            note_file=note_file,
            image_web_path=image_web_path,
            seed_context=args.seed_context,
            motif_focus=args.motif_focus,
            intensity=args.intensity,
            length_mode=args.length_mode,
            recurring_names=recurring_names,
        )
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1
    except Exception as exc:
        log.error("Unexpected error: %s", exc)
        return 1

    # Determine output path
    if args.out_file:
        out_path = Path(args.out_file)
    else:
        REQUESTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = REQUESTS_DIR / f"request-{ts}.txt"

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(prompt_text, encoding="utf-8")
        log.info("Request bundle written → %s", out_path)
    except Exception as exc:
        log.error("Could not write output file: %s", exc)
        return 1

    # Print path to stdout for shell capture
    print(out_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
