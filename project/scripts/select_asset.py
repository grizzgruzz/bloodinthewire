#!/usr/bin/env python3
"""
select_asset.py  v2
===================
Media-priority + hygiene pipeline for Blood in the Wire.

Responsibilities
----------------
1. Level-aware source policy:
     SURFACE level (depth=0, --level surface, default):
       incoming/ is the HIGHEST priority source.  If incoming/ has files,
       they are used exclusively.  Falls back to library/ when incoming/ is
       empty.
     DEEP level (depth>0, --level deep):
       incoming/ is COMPLETELY FORBIDDEN.  Only library/ assets may be used.
       This is a hard rule — incoming/ images must never appear on linked /
       recursive / lower-level pages.
2. Consume-on-use:       The selected file is MOVED out of incoming (or
   copied from library) to assets/published/ with a timestamped name, so it
   cannot be accidentally reused.
3. Metadata stripping:   Before the asset is placed in assets/web/ (the
   web-facing folder), EXIF/IPTC/XMP metadata is stripped via:
     a. Pillow re-encode (preferred — dependency already present)
     b. exiftool -all= (fallback if Pillow unavailable or fails)
     c. Best-effort byte-copy with a warning if neither works.
4. Deterministic output: Prints a single absolute path to stdout (the
   stripped web-ready file) so calling scripts/templates can use it
   directly:
       python select_asset.py              # surface-level, picks & strips one asset
       python select_asset.py --level deep # deep-level, library only, never incoming
       python select_asset.py --show       # just print which would be chosen
       path=$(python select_asset.py)      # capture path for shell scripts

Usage
-----
    python select_asset.py [--show] [--dry-run] [--level surface|deep]

Flags
~~~~~
--level      'surface' (default) or 'deep'.
             surface: incoming/ takes priority over library/ when present.
             deep:    incoming/ is NEVER used — hard-blocked. Library only.
--show       Resolve + print which file would be selected; do NOT move/copy
             or strip.  Safe for preview.
--dry-run    Same as --show (alias).

Outputs
-------
On success: absolute path to assets/web/<name> printed to stdout.
On failure: non-zero exit, errors printed to stderr.

Directory layout (relative to project/assets/)
----------------------------------------------
    incoming/    ← user drops files here; takes priority; files consumed on use
    library/     ← auto-fetched library; used when incoming is empty
    published/   ← post-process archive (original + metadata stripped)
    web/         ← live web-facing assets (metadata stripped copies)
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
ASSETS_DIR = (SCRIPT_DIR / ".." / "assets").resolve()
INCOMING_DIR = ASSETS_DIR / "incoming"
LIBRARY_DIR  = ASSETS_DIR / "library"
PUBLISHED_DIR = ASSETS_DIR / "published"
WEB_DIR       = ASSETS_DIR / "web"

# Accepted extensions (lowercase).  Anything else is skipped.
ACCEPTED_EXT = frozenset({".jpg", ".jpeg", ".png"})

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[select_asset] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("select_asset")

# ─── Source selection ─────────────────────────────────────────────────────────

def _image_files(directory: Path) -> list[Path]:
    """Return sorted list of accepted image files in *directory* (non-recursive)."""
    if not directory.is_dir():
        return []
    files = sorted(
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in ACCEPTED_EXT
    )
    return files


def select_source(level: str = "surface") -> tuple[Path, str]:
    """
    Apply source-policy based on publish level.

    Parameters
    ----------
    level : 'surface' or 'deep'
        'surface' (default, depth=0):
            incoming/ takes priority.  Falls back to library/ when empty.
        'deep' (depth>0):
            incoming/ is HARD-BLOCKED — never used regardless of content.
            Only library/ assets are returned.
            Raises PermissionError if called with level='deep' but somehow
            an incoming file were accidentally selected (belt-and-suspenders).

    Returns
    -------
    (source_path, queue_label)  where queue_label is 'incoming' or 'library'.

    Raises
    ------
    FileNotFoundError  if no eligible asset exists in the allowed location(s).
    PermissionError    if level='deep' and incoming/ would have been chosen
                       (should not normally happen — guard only).
    ValueError         if level is not 'surface' or 'deep'.
    """
    if level not in ("surface", "deep"):
        raise ValueError(f"Invalid level '{level}': must be 'surface' or 'deep'.")

    if level == "surface":
        # Surface level: incoming/ has priority
        incoming_files = _image_files(INCOMING_DIR)
        if incoming_files:
            chosen = incoming_files[0]   # deterministic: alphabetical first
            log.info("SOURCE=incoming  level=surface  file=%s  (%d file(s) in queue)",
                     chosen.name, len(incoming_files))
            return chosen, "incoming"

        library_files = _image_files(LIBRARY_DIR)
        if library_files:
            chosen = library_files[0]
            log.info("SOURCE=library   level=surface  file=%s  (incoming is empty)",
                     chosen.name)
            return chosen, "library"

        raise FileNotFoundError(
            "No eligible image assets found in incoming/ or library/. "
            "Run fetch_random_assets.py or drop files into assets/incoming/."
        )

    else:
        # level == "deep": incoming/ is FORBIDDEN — library only.
        # Belt-and-suspenders: log explicitly that incoming is being skipped.
        incoming_files = _image_files(INCOMING_DIR)
        if incoming_files:
            log.info(
                "GUARD: level=deep — incoming/ has %d file(s) but is BLOCKED at deep level. "
                "Library only.",
                len(incoming_files),
            )

        library_files = _image_files(LIBRARY_DIR)
        if library_files:
            chosen = library_files[0]
            log.info("SOURCE=library   level=deep   file=%s  (incoming/ blocked at deep level)",
                     chosen.name)
            return chosen, "library"

        raise FileNotFoundError(
            "No eligible image assets found in library/ for deep-level selection. "
            "incoming/ is blocked at this level. "
            "Run fetch_random_assets.py to replenish assets/library/."
        )


# ─── Metadata stripping ───────────────────────────────────────────────────────

def _strip_with_pillow(src: Path, dst: Path) -> bool:
    """
    Re-encode via Pillow to drop all metadata.  Returns True on success.
    Only JPEG and PNG are handled; returns False for unknown formats.
    """
    try:
        from PIL import Image  # type: ignore[import]
    except ImportError:
        return False

    ext = src.suffix.lower()
    try:
        with Image.open(src) as img:
            if ext in (".jpg", ".jpeg"):
                # Convert mode if needed (RGBA/P not valid JPEG)
                if img.mode in ("RGBA", "P", "LA"):
                    img = img.convert("RGB")
                # Save with no extra info dict → strips Exif/XMP/IPTC
                img.save(dst, format="JPEG", quality=92, optimize=True,
                         exif=b"", icc_profile=None)
            elif ext == ".png":
                # PNG: strip tEXt / iTXt / zTXt chunks by not carrying them
                data = img.tobytes()
                clean = Image.frombytes(img.mode, img.size, data)
                clean.save(dst, format="PNG", optimize=True)
            else:
                return False
        log.info("STRIP=pillow     src=%s → dst=%s", src.name, dst.name)
        return True
    except Exception as exc:
        log.warning("STRIP=pillow FAILED (%s) — will try fallback", exc)
        return False


def _strip_with_exiftool(src: Path, dst: Path) -> bool:
    """
    Copy src → dst then run exiftool -all= -overwrite_original on dst.
    Returns True on success.
    """
    exiftool = shutil.which("exiftool")
    if not exiftool:
        return False
    try:
        shutil.copy2(src, dst)
        result = subprocess.run(
            [exiftool, "-all=", "-overwrite_original", str(dst)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.warning("STRIP=exiftool STDERR: %s", result.stderr.strip())
            return False
        log.info("STRIP=exiftool   src=%s → dst=%s", src.name, dst.name)
        return True
    except Exception as exc:
        log.warning("STRIP=exiftool FAILED (%s)", exc)
        return False


def strip_metadata(src: Path, dst: Path) -> str:
    """
    Strip EXIF/IPTC/XMP from *src* and write to *dst*.

    Tries methods in priority order:
      1. Pillow re-encode (preferred)
      2. exiftool
      3. Plain copy (best-effort, warns)

    Returns a string describing which method was used.
    """
    # Method 1: Pillow
    if _strip_with_pillow(src, dst):
        return "pillow"

    # Method 2: exiftool
    if _strip_with_exiftool(src, dst):
        return "exiftool"

    # Fallback: plain copy, log loudly
    log.warning(
        "STRIP=none  — no stripping tool succeeded. "
        "Copying %s as-is. Metadata may remain.", src.name
    )
    shutil.copy2(src, dst)
    return "none (raw copy — metadata may remain)"


# ─── Consume-on-use ───────────────────────────────────────────────────────────

def consume_and_publish(src: Path, queue: str) -> Path:
    """
    Move (incoming) or copy (library) *src* into published/ with a timestamp
    suffix so the original is archived and cannot be reused.

    If *src* is from incoming/ and has a matching .note.txt sidecar
    (same stem, e.g. ``<uuid>.note.txt`` alongside ``<uuid>.png``),
    that sidecar is consumed/moved at the same time under the same
    timestamped name so it stays paired with the image in published/.

    Returns the published/ path (image, not the note).
    """
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    published_name = f"{src.stem}__{ts}{src.suffix}"
    published_path = PUBLISHED_DIR / published_name

    if queue == "incoming":
        shutil.move(str(src), published_path)   # consume: removes from incoming
        log.info("CONSUME  moved %s → published/%s", src.name, published_name)

        # --- Sidecar: move matching .note.txt with the image ---
        sidecar_src = src.parent / f"{src.stem}.note.txt"
        if sidecar_src.is_file():
            sidecar_published_name = f"{src.stem}__{ts}.note.txt"
            sidecar_published_path = PUBLISHED_DIR / sidecar_published_name
            shutil.move(str(sidecar_src), sidecar_published_path)
            log.info(
                "CONSUME  moved sidecar %s → published/%s",
                sidecar_src.name, sidecar_published_name,
            )
        else:
            log.debug("No .note.txt sidecar found for %s — skipping", src.name)
    else:
        shutil.copy2(src, published_path)
        log.info("ARCHIVE  copied %s → published/%s", src.name, published_name)
        # Library assets have no user-supplied sidecars; nothing to move.

    return published_path


# ─── Main pipeline ────────────────────────────────────────────────────────────

def run(dry_run: bool = False, level: str = "surface") -> Path:
    """
    Full pipeline:  select → consume/archive → strip → web output.

    Parameters
    ----------
    dry_run : bool
        If True, resolve source but do NOT move/copy/strip.
    level : 'surface' or 'deep'
        Controls which sources are eligible.  See select_source() for details.
        'surface' (default): incoming/ priority, library/ fallback.
        'deep': library/ only; incoming/ hard-blocked.

    Returns the web-ready Path (in assets/web/).
    """
    # Ensure all dirs exist
    for d in (INCOMING_DIR, LIBRARY_DIR, PUBLISHED_DIR, WEB_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # 1. Select source (level-aware)
    source, queue = select_source(level=level)

    if dry_run:
        log.info("DRY-RUN: would select %s from %s  level=%s — stopping here.",
                 source.name, queue, level)
        return source

    # 2. Consume (move from incoming) or archive (copy from library)
    published_path = consume_and_publish(source, queue)

    # 3. Strip metadata into web/
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    web_name = published_path.stem.replace("__", "_") + published_path.suffix
    web_path = WEB_DIR / web_name
    method = strip_metadata(published_path, web_path)
    log.info("WEB_READY  path=%s  level=%s  strip_method=%s", web_path, level, method)

    return web_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select one asset, strip metadata, output web-ready path."
    )
    parser.add_argument(
        "--show", "--dry-run", action="store_true",
        help="Resolve which file would be selected without moving/stripping anything."
    )
    parser.add_argument(
        "--level", default="surface", choices=["surface", "deep"],
        help=(
            "Publish level controlling which sources are eligible. "
            "'surface' (default): incoming/ takes priority, library/ is fallback. "
            "'deep': incoming/ is HARD-BLOCKED; only library/ assets are used. "
            "Use 'deep' for all linked/recursive/lower-level pages."
        ),
    )
    args = parser.parse_args()

    try:
        result = run(dry_run=args.show, level=args.level)
        # Emit the path to stdout so shell scripts can capture it
        print(result)
        return 0
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1
    except PermissionError as exc:
        log.error("LEVEL GUARD: %s", exc)
        return 3
    except Exception as exc:
        log.error("Unexpected error: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
