# Asset Pipeline — Blood in the Wire

Covers the full media lifecycle from raw drops to web-ready images.
Last updated: 2025-03 (v2 — media-priority + hygiene workflow added).

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

## Queue Order (User-Supplied-First Policy)

The pipeline enforces strict priority:

1. **Scan `assets/incoming/`** — if ANY accepted image files exist (`.jpg`, `.jpeg`, `.png`), they are used **exclusively**.  The library is completely ignored until `incoming/` is empty.
2. **Fall back to `assets/library/`** — only when `incoming/` contains no eligible files.

Files are selected alphabetically (deterministic, reproducible).

**Rationale:** A user-supplied image always carries stronger editorial intent than an auto-fetched one.  This policy ensures that whenever a human places a file in `incoming/`, it gets used next — no accidental library override.

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
