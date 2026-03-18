# Asset Pipeline — Blood in the Wire

Covers the full media lifecycle from raw drops to web-ready images.
Last updated: 2026-03 (v3 — depth-aware media source rules added).

---

## Directory Layout

```
project/assets/
├── incoming/    ← manual user drops; HIGHEST priority; files consumed on use
├── library/     ← auto-fetched Wikimedia assets (fetch_random_assets.py)
├── published/   ← timestamped archive of every asset that entered the pipeline
├── web/         ← live web-facing images (metadata-stripped, safe to serve)
├── manifest.json
└── README.md
```

---

## Pipeline Scripts

| Script | Role |
|---|---|
| `fetch_random_assets.py` | Downloads CC0/PD images from Wikimedia → `assets/library/` |
| `select_asset.py`        | Selects ONE asset, strips metadata, outputs web-ready path |

---

## Queue Order — Depth-Aware Source Policy

The pipeline enforces strict priority that depends on the **publish level** (depth):

### Surface level (`--level surface`, depth=0, default)

Used for the front-page / `index.html` and first-level inline cards:

1. **Scan `assets/incoming/`** — if ANY accepted image files exist (`.jpg`, `.jpeg`, `.png`), they are used **exclusively**.  The library is completely ignored until `incoming/` is empty.
2. **Fall back to `assets/library/`** — only when `incoming/` contains no eligible files.

### Deep level (`--level deep`, depth>0)

Used for all linked / recursive / lower-level pages (node pages, content pages reached via branch links):

1. **`assets/incoming/` is HARD-BLOCKED** — never selected, even if files are present.  A warning is logged when incoming files exist but are skipped.
2. **`assets/library/`** is the only permitted source.
3. If `library/` is empty, the call fails with a clear error (no image is better than a leaked incoming asset).

Files are selected alphabetically (deterministic, reproducible).

**Rationale:**
- `incoming/` assets are user-supplied and may carry editorial or personal context. They are appropriate for the surface/front-page where the operator controls the narrative directly.
- Linked/recursive pages are generated mechanically and must only use vetted, metadata-clean library assets that have no personal provenance.
- This rule prevents incoming assets from leaking into auto-generated sub-pages.

### Integration with branch_publish.py

When calling `select_asset.py` for a linked/deep page, pass `--level deep`:

```bash
# Surface: incoming preferred
web_path=$(python select_asset.py --level surface)

# Deep: library only
web_path=$(python select_asset.py --level deep)
```

When calling `branch_publish.py`, pass `--image-source incoming` or `--image-source library` to let the engine enforce the guard on recursive pages:

```bash
python branch_publish.py \
  --title "..." \
  --image-web-path "$web_path" \
  --image-source incoming  # or 'library'
```

At `depth>0`, any `incoming`-sourced image is automatically suppressed by the engine even if accidentally passed through.

---

## Consume-on-Use Behaviour

When an asset is selected for publishing:

- **From `incoming/`:** the file is **moved** (not copied) to `assets/published/`.  After this operation the file no longer exists in `incoming/` and cannot be served again.
- **From `library/`:** the file is **copied** to `assets/published/` (library entries are kept for reference and future re-use by the fetcher's dedup logic).

Published filenames are timestamped:

```
{original_stem}__{YYYYMMDD-HHMMSS}{ext}
```

e.g. `Boulevard-Hotel-Neon-sign-Miami-Beach__20250317-143022.jpg`

This gives full traceability: you can always see exactly which file was served at what time.

---

## Metadata Sanitation

Before an asset is written to `assets/web/` (web-facing), all EXIF, IPTC, and XMP metadata is stripped.  The pipeline tries three methods in order and logs which was used:

### Method 1 — Pillow re-encode (preferred)
Pillow (already a project dependency) re-encodes the image from pixel data only:
- JPEG: saved with an empty EXIF block (`exif=b""`) and `icc_profile=None`.
- PNG: reconstructed from raw pixel bytes → no tEXt / iTXt / zTXt chunks carried over.

**Log line:** `STRIP=pillow   src=X → dst=Y`

### Method 2 — exiftool fallback
If Pillow is unavailable or raises an exception, `exiftool -all= -overwrite_original` is attempted on a copy of the file.

**Log line:** `STRIP=exiftool  src=X → dst=Y`

### Method 3 — Raw copy (last resort)
If neither tool succeeds, the file is copied as-is with a loud warning logged.

**Log line:** `STRIP=none  — no stripping tool succeeded. … Metadata may remain.`

> **Note:** Unsupported file types (non-JPEG/PNG) will fall through to method 3 and log a warning.  The pipeline still outputs a path so downstream steps do not break, but manual review is recommended.

---

## Output

`select_asset.py` prints a **single absolute path** to `assets/web/<name>` on stdout.  This makes shell integration trivial:

```bash
# Capture the web-ready path
web_path=$(python select_asset.py)

# Use in a webpage build step
echo "<img src=\"${web_path}\">"
```

---

## Quick Demo

```bash
# Preview which file would be chosen (no side effects)
python select_asset.py --show

# Full pipeline: select, consume, strip, output path
python select_asset.py

# Drop a file to test user-supplied-first priority:
cp ../assets/library/L-door.png ../assets/incoming/
python select_asset.py   # will choose incoming/ file, not library
```

---

## Integration with fetch_random_assets.py

`fetch_random_assets.py` remains unchanged.  It populates `assets/library/` as before.  `select_asset.py` treats the library as the fallback source once `incoming/` is drained.

Typical workflow:

1. Run `fetch_random_assets.py` periodically to keep `library/` stocked.
2. Drop editorial images into `incoming/` whenever you have them.
3. Call `select_asset.py` as part of the site-build step to get a web-ready image.

---

## Security / Privacy Notes

- Metadata stripping prevents inadvertent publication of GPS coordinates, device info, or software watermarks embedded in source files.
- Files in `assets/web/` should be treated as public-safe; everything else (`incoming/`, `library/`, `published/`) is internal.
- Do not commit `assets/web/` or `assets/published/` to the public-facing branch unless you have reviewed their contents.
