#!/usr/bin/env python3
"""
fetch_random_assets.py  v3
==========================
Fetch real candid-style photo assets from Wikimedia Commons using random
search terms.  Optional Brave Search-assisted discovery mode.

Strategy
--------
* Generates N random words per cycle (default 5).
* [Optional] Uses Brave Search API to discover additional Wikimedia Commons
  File: candidates for each term (--use-brave).
* Accepts only attribution-free licenses (Public Domain / CC0).
* Extended vibe scoring: penalises stock/polished terms, rewards
  documentary/awkward cues; adds mild penalty for standalone nature beauty shots.
* Collects all viable candidates first across the full cycle, then ranks by
  vibe score + random jitter before committing to metadata/download API calls.
* --dry-run  → print decisions without downloading or writing anything.
* --seed     → fix the random seed for reproducible runs.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# VIBE CONFIGURATION  (tune these without touching the logic below)
# ──────────────────────────────────────────────────────────────────────────────

# MIME types we'll accept. Everything else is hard-rejected (no SVG, GIF, TIFF…).
ACCEPT_MIME_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png"})

# If ANY of these substrings appear in a file title (case-insensitive), the
# file is hard-rejected — the "no polished/archival/illustration" rule.
REJECT_TITLE_TERMS: list[str] = [
    # Artwork / illustration
    "painting", "illustration", "drawing", "sketch", "artwork", "render",
    "poster", "album cover", "cover art", "scan", "facsimile",
    # Diagrams / data viz
    "diagram", "map", "chart", "plan", "blueprint", "schematic",
    # Heraldry / official imagery
    "coat of arms", "flag", "logo", "emblem", "icon", "stamp",
    # Space / engineering (often NASA press releases)
    "nasa", "esa", "launch", "rocket", "space shuttle", "satellite",
    "trajectory", "spacecraft",
    # Typography / vectors
    "vector", "typography", "font",
    # Archive reproduction styles
    "engraving", "lithograph", "woodcut", "daguerreotype",
    # Encyclopedia / captioned figures (EB1911, Fig. 3, Plate IV …)
    "eb1911", "encyclopædia", "encyclopaedia", "encyclopedia",
    " fig.", "fig. ", "plate ", "plate.",
    # Screencaps / UI
    "screenshot", "screencap", "screen capture",
]

# Each match subtracts 1 from vibe score (cumulative, capped at -3).
# These signal polished / stock / "beautiful" aesthetics we don't want.
STOCK_PENALTY_TERMS: list[str] = [
    "wallpaper", " hd", "4k", "8k", "bokeh",
    "beautiful", "aesthetic", "macro", "scenic",
    "award-winning", "award winning", "stunning", "breathtaking",
    "masterpiece", "perfect shot", "high resolution", "hi-res",
    "royalty free", "royalty-free", "stock photo", "press release",
    "official", "ceremony", "professional photography",
    "nature photography", "wildlife photography",
    "fine art", "glamour", "picturesque", "majestic",
]

# Each match adds 1 to vibe score (cumulative, capped at +3).
# These signal the unsettling / documentary / real vibe we want.
DOCUMENTARY_BONUS_TERMS: list[str] = [
    "street", "parking", "cctv", "blurry", "blurred", "night",
    "corner", "random", "intersection", "underpass", "doorway",
    "alleyway", "sidewalk", "snapshot", "candid", "documentary",
    "everyday", "ordinary", "mundane", "dumpster", "graffiti",
    "scratched", "worn", "faded", "broken", "abandoned", "empty",
    "fluorescent", "concrete", "asphalt", "chainlink", "chain link",
    "overpass", "bystander", "passerby",
]

# Nature-beauty single-subject terms → mild penalty (-1) unless cancelled
# by a situational context word (person, crowd, building, etc.).
NATURE_BEAUTY_TERMS: list[str] = [
    "sunset", "sunrise", "golden hour", "flower", "flowers",
    "butterfly", "waterfall", "mountain", "mountains",
    "landscape", "wildlife", "bird", "birds", "close-up", "closeup",
]

# If any of these appear alongside NATURE_BEAUTY_TERMS, the penalty is waived.
SITUATIONAL_CONTEXT_WORDS: list[str] = [
    "person", "people", "crowd", "building", "street", "road", "car",
    "urban", "city", "town", "man", "woman", "figure", "worker",
    "sign", "fence", "graffiti", "parking", "lot",
]

# If ANY of these appear in a title the file gets a candid-preference boost.
# Doesn't reject others — just prioritises the best matches.
PREFER_CANDID_TITLE_CUES: list[str] = [
    # Camera-roll / EXIF-derived filenames
    "img_", "dsc", "dscn", "dscf", "imag", "photo_",
    "p10", "p20", "p30",          # Huawei phone naming convention
    # Explicit handheld hints
    "iphone", "android", "phone", "mobile", "snapshot", "selfie",
    # Content that reads as candid / documentary
    "street", "candid", "documentary", "everyday",
]

# Soft-reject: subtract 1 from score but don't hard-reject.
SOFT_REJECT_TITLE_TERMS: list[str] = [
    "portrait studio", "professional", "stock photo", "royalty free",
    "press release", "official", "ceremony",
]

# The search term must appear as a whole word in the file title (word-boundary
# regex). Set False to fall back to a plain substring match.
REQUIRE_WORD_BOUNDARY_MATCH: bool = True

# Cap on exponential-back-off sleep after HTTP 429.
MAX_BACKOFF_SECONDS: int = 60

# Random jitter range added to vibe scores during final ranking.
# Prevents identical-score candidates from always resolving the same way.
VIBE_RANDOM_JITTER: float = 0.75

# ──────────────────────────────────────────────────────────────────────────────
# WORD POOL  (urban/everyday nouns for the random 5-word draws)
# ──────────────────────────────────────────────────────────────────────────────

COMMON_WORDS: list[str] = [
    "alley", "antenna", "attic", "avenue", "balcony", "basement", "bench", "billboard",
    "bridge", "bus", "cable", "canal", "car", "chain", "chair",
    "church", "clock", "corridor", "courtyard", "crosswalk", "curtain",
    "dog", "door", "driveway", "elevator", "fence", "field", "fire",
    "fog", "garage", "gate", "glass", "hallway", "hill", "hotel", "house", "intersection",
    "kitchen", "lamp", "leaf", "locker", "lot", "mailbox", "mirror", "motor",
    "neon", "night", "office", "parking", "path", "phone", "pipe", "pole", "porch",
    "rail", "rain", "river", "road", "roof", "room", "screen", "shadow", "sidewalk",
    "sign", "stair", "station", "street", "subway", "table", "train", "tree",
    "tunnel", "van", "wall", "warehouse", "water", "window", "wire", "yard",
]

# ──────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ──────────────────────────────────────────────────────────────────────────────

UA = "bloodinthewire-asset-fetcher/3.0 (https://bloodinthewire.com)"


def http_get_json(
    url: str,
    extra_headers: dict[str, str] | None = None,
    retries: int = 4,
) -> dict[str, Any]:
    """GET → JSON with exponential back-off on HTTP 429. Handles gzip responses."""
    delay = 4.0
    headers: dict[str, str] = {"User-Agent": UA}
    if extra_headers:
        headers.update(extra_headers)
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = min(delay * (2 ** attempt), MAX_BACKOFF_SECONDS)
                print(f"  [429] rate-limited — sleeping {wait:.0f}s …")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"HTTP GET failed after {retries} retries: {url}")


def download_file(url: str, dst: Path, retries: int = 3) -> None:
    """Download binary with exponential back-off on HTTP 429."""
    delay = 4.0
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                dst.write_bytes(r.read())
            return
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = min(delay * (2 ** attempt), MAX_BACKOFF_SECONDS)
                print(f"  [429] download rate-limited — sleeping {wait:.0f}s …")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"Download failed after {retries} retries: {url}")


# ──────────────────────────────────────────────────────────────────────────────
# Wikimedia Commons API helpers
# ──────────────────────────────────────────────────────────────────────────────

def commons_search_files(term: str, limit: int = 15) -> list[str]:
    q = urllib.parse.urlencode({
        "action": "query", "format": "json",
        "list": "search",
        "srsearch": f"filetype:bitmap {term}",
        "srnamespace": "6",   # File namespace
        "srlimit": str(limit),
    })
    data = http_get_json(f"https://commons.wikimedia.org/w/api.php?{q}")
    hits = data.get("query", {}).get("search", [])
    return [h["title"] for h in hits if h.get("title", "").startswith("File:")]


def commons_file_info(title: str) -> dict[str, Any] | None:
    q = urllib.parse.urlencode({
        "action": "query", "format": "json",
        "prop": "imageinfo", "titles": title,
        "iiprop": "url|size|mime|extmetadata",
    })
    data = http_get_json(f"https://commons.wikimedia.org/w/api.php?{q}")
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return None
    page = next(iter(pages.values()))
    infos = page.get("imageinfo") or []
    return infos[0] if infos else None


# ──────────────────────────────────────────────────────────────────────────────
# Brave Search helpers
# ──────────────────────────────────────────────────────────────────────────────

def brave_search_wikimedia(term: str, api_key: str, count: int = 5) -> list[str]:
    """
    Use Brave Search API to discover Wikimedia Commons File: candidates.
    Queries site:commons.wikimedia.org/wiki/File: <term>, extracts File:
    title strings from result URLs.

    Deduplication against Wikimedia search results happens in collect_candidates().
    All discovered titles are still validated through the existing Wikimedia
    metadata/license pipeline before being accepted.
    """
    query = f"site:commons.wikimedia.org/wiki/File: {term}"
    q = urllib.parse.urlencode({"q": query, "count": str(min(count, 20))})
    url = f"https://api.search.brave.com/res/v1/web/search?{q}"
    try:
        data = http_get_json(
            url,
            extra_headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            },
            retries=2,
        )
    except Exception as exc:
        print(f"  [brave error] term={term!r}: {exc}")
        return []

    titles: list[str] = []
    for result in data.get("web", {}).get("results", []):
        result_url = result.get("url", "")
        # e.g. https://commons.wikimedia.org/wiki/File:Something_here.jpg
        m = re.search(r"/wiki/(File:[^#?&\s]+)", result_url)
        if m:
            raw = m.group(1)
            title = urllib.parse.unquote(raw).replace("_", " ")
            if title.startswith("File:"):
                titles.append(title)
    return titles


# ──────────────────────────────────────────────────────────────────────────────
# Vibe filtering
# ──────────────────────────────────────────────────────────────────────────────

def is_attr_free_license(extmeta: dict[str, Any]) -> tuple[bool, str]:
    """Return (ok, label). ok=True only for Public Domain / CC0."""
    short = (extmeta.get("LicenseShortName") or {}).get("value", "")
    licurl = (extmeta.get("LicenseUrl") or {}).get("value", "")
    usage = (extmeta.get("UsageTerms") or {}).get("value", "")
    blob = " ".join([short, licurl, usage]).lower()
    if "public domain" in blob or "cc0" in blob or "creative commons zero" in blob:
        return True, short or usage or "Public Domain/CC0"
    return False, short or usage or "Unknown/Requires attribution"


def term_matches_title(term: str, title: str) -> bool:
    """
    True if `term` appears in the file-title component of `title`.
    Uses whole-word boundary matching when REQUIRE_WORD_BOUNDARY_MATCH is set.
    """
    name = title.lower().replace("File:", "").replace("-", " ").replace("_", " ")
    name = re.sub(r"\.\w{2,5}$", "", name)
    if not REQUIRE_WORD_BOUNDARY_MATCH:
        return term.lower() in name
    pattern = rf"\b{re.escape(term.lower())}\b"
    return bool(re.search(pattern, name))


def is_hard_reject(title: str) -> bool:
    """True if the title triggers any REJECT_TITLE_TERMS hit."""
    t = title.lower()
    return any(bad in t for bad in REJECT_TITLE_TERMS)


def vibe_score(title: str) -> int:
    """
    Extended vibe score for a file title.  Does NOT check hard-reject terms —
    call is_hard_reject() first.

    Positive  = documentary / awkward / plausibly-real.
    Negative  = stock / polished / beautiful.
    Zero      = neutral / acceptable.

    Approximate range: −6 … +5.

    Component breakdown:
      +1   legacy candid cues (IMG_, DSC_, "street", "candid", …)
      +1…+3 documentary/awkward bonus (per-hit, capped at +3)
      −1…−3 stock/polished penalty (per-hit, capped at −3)
      −1   nature-beauty single-subject penalty (waived if situational context present)
      −1   legacy soft-reject penalty
    """
    t = title.lower()
    score = 0

    # Legacy candid cues (camera-roll filenames, "street", etc.) → +1
    if any(cue in t for cue in PREFER_CANDID_TITLE_CUES):
        score += 1

    # Documentary/awkward bonus (capped at +3)
    doc_hits = sum(1 for cue in DOCUMENTARY_BONUS_TERMS if cue in t)
    score += min(doc_hits, 3)

    # Stock/polished penalty (capped at −3)
    stock_hits = sum(1 for term in STOCK_PENALTY_TERMS if term in t)
    score -= min(stock_hits, 3)

    # Nature-beauty single-subject penalty (−1) unless situational context
    if any(nb in t for nb in NATURE_BEAUTY_TERMS):
        if not any(ctx in t for ctx in SITUATIONAL_CONTEXT_WORDS):
            score -= 1

    # Legacy soft-reject (−1)
    if any(soft in t for soft in SOFT_REJECT_TITLE_TERMS):
        score -= 1

    return score


# ──────────────────────────────────────────────────────────────────────────────
# Candidate collection + ranking
# ──────────────────────────────────────────────────────────────────────────────

# Type alias for a ranked candidate entry.
# (ranked_score, raw_vibe, term, title, source)
Candidate = tuple[float, int, str, str, str]


def collect_candidates(
    terms: list[str],
    brave_api_key: str = "",
    brave_count: int = 5,
) -> list[Candidate]:
    """
    Discover all candidate titles for the given terms via Wikimedia search
    (always) and optionally Brave Search.  Applies hard-reject and relevance
    filters, scores each candidate, adds random jitter, and returns a list
    sorted descending by ranked score (best first).

    Duplicates (same title from both sources) are deduplicated; the first
    source seen wins.
    """
    seen: set[str] = set()
    raw_candidates: list[tuple[str, str, str]] = []  # (term, title, source)

    for term in terms:
        # Wikimedia search
        try:
            wm_titles = commons_search_files(term)
        except Exception as exc:
            print(f"  [wikimedia error] '{term}': {exc}")
            wm_titles = []

        for title in wm_titles:
            key = title.lower().strip()
            if key not in seen:
                seen.add(key)
                raw_candidates.append((term, title, "wikimedia"))

        # Brave-assisted discovery
        if brave_api_key:
            brave_titles = brave_search_wikimedia(term, brave_api_key, brave_count)
            for title in brave_titles:
                key = title.lower().strip()
                if key not in seen:
                    seen.add(key)
                    raw_candidates.append((term, title, "brave"))

    # Filter hard-rejects and relevance; score the rest
    scored: list[Candidate] = []
    for term, title, source in raw_candidates:
        if is_hard_reject(title):
            continue
        if not term_matches_title(term, title):
            continue
        raw = vibe_score(title)
        jitter = random.uniform(0.0, VIBE_RANDOM_JITTER)
        scored.append((raw + jitter, raw, term, title, source))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


# ──────────────────────────────────────────────────────────────────────────────
# Manifest helpers
# ──────────────────────────────────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip())
    return name[:180] or f"asset-{int(time.time())}.jpg"


def load_manifest(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"generatedAt": None, "assets": []}


def save_manifest(path: Path, data: dict[str, Any]) -> None:
    data["generatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def already_have_source(manifest: dict[str, Any], source_url: str) -> bool:
    return any(a.get("sourceUrl") == source_url for a in manifest.get("assets", []))


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def random_terms(n: int = 5) -> list[str]:
    return random.sample(COMMON_WORDS, k=min(n, len(COMMON_WORDS)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch candid-style attribution-free photos from Wikimedia Commons."
    )
    parser.add_argument("--count", type=int, default=8,
                        help="How many assets to fetch (default: 8)")
    parser.add_argument("--max-cycles", type=int, default=20,
                        help="Max random-term rounds before giving up (default: 20)")
    parser.add_argument("--out", default="../assets/library",
                        help="Output dir for downloaded files")
    parser.add_argument("--manifest", default="../assets/manifest.json",
                        help="Manifest JSON path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate without downloading or writing files")
    parser.add_argument("--seed", type=int, default=None,
                        help="Fix the random seed for reproducible runs")
    # Brave discovery flags
    parser.add_argument("--use-brave", action="store_true",
                        help="Enable Brave Search-assisted candidate discovery")
    parser.add_argument("--brave-api-key-env", default="BRAVE_API_KEY",
                        help="Env var holding the Brave API key (default: BRAVE_API_KEY)")
    parser.add_argument("--brave-count", type=int, default=5,
                        help="Max Brave results per term (default: 5)")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)
        print(f"[seed={args.seed}]")

    if args.dry_run:
        print("[dry-run] No files will be downloaded or written.\n")

    # Resolve Brave API key from environment
    brave_api_key = ""
    if args.use_brave:
        brave_api_key = os.environ.get(args.brave_api_key_env, "")
        if not brave_api_key:
            print(
                f"[brave] WARNING: --use-brave set but env var "
                f"{args.brave_api_key_env!r} is empty — Brave discovery disabled."
            )
        else:
            print(
                f"[brave] Discovery enabled "
                f"(env={args.brave_api_key_env!r}, count-per-term={args.brave_count})"
            )

    script_dir = Path(__file__).resolve().parent
    out_dir = (script_dir / args.out).resolve()
    manifest_path = (script_dir / args.manifest).resolve()

    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(manifest_path)
    grabbed = 0

    for cycle in range(args.max_cycles):
        if grabbed >= args.count:
            break

        terms = random_terms(5)
        print(f"\n[cycle {cycle+1}/{args.max_cycles}] terms={terms}")

        # Collect, score, and rank all candidates for this cycle's terms.
        # Hard-rejects and relevance-misses are filtered out here; the rest
        # are sorted best-vibe-first with random jitter.
        candidates = collect_candidates(
            terms,
            brave_api_key=brave_api_key,
            brave_count=args.brave_count,
        )

        if not candidates:
            print("  - no candidates survived vibe filter this cycle")
            continue

        print(f"  {len(candidates)} candidates ranked (vibe + jitter)")

        # Validate license/MIME and download in ranked order.
        for ranked_score, raw_vibe, term, title, source in candidates:
            if grabbed >= args.count:
                break

            try:
                info = commons_file_info(title)
            except Exception as exc:
                print(f"  [info error] {title}: {exc}")
                continue
            if not info:
                continue

            url = info.get("url", "")
            mime = info.get("mime", "")
            extmeta = info.get("extmetadata", {}) or {}
            ok, license_name = is_attr_free_license(extmeta)

            # Hard-reject non-photo MIME types.
            if mime not in ACCEPT_MIME_TYPES:
                continue
            if not ok or not url:
                continue
            if already_have_source(manifest, url):
                continue

            vibe_label = (
                "📸 candid" if raw_vibe > 0
                else "🔘 neutral" if raw_vibe == 0
                else "⚠ penalised"
            )
            src_tag = f" [brave]" if source == "brave" else ""

            if args.dry_run:
                print(
                    f"  [DRY-RUN] [{grabbed+1}/{args.count}] {title}\n"
                    f"             term={term!r}  mime={mime}  license={license_name!r}"
                    f"  vibe={raw_vibe:+d}  {vibe_label}{src_tag}"
                )
                grabbed += 1
                continue

            basename = sanitize_filename(title.replace("File:", ""))
            target = out_dir / basename
            if target.exists():
                target = out_dir / f"{target.stem}-{int(time.time())}{target.suffix}"

            try:
                download_file(url, target)
            except Exception as exc:
                print(f"  [download error] {title}: {exc}")
                continue

            manifest.setdefault("assets", []).append({
                "filename": target.name,
                "term": term,
                "title": title,
                "sourceUrl": url,
                "source": source,
                "license": license_name,
                "attributionRequired": False,
                "vibeScore": raw_vibe,
                "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            grabbed += 1
            print(
                f"  + saved [{grabbed}/{args.count}] {target.name}\n"
                f"           term={term!r}  license={license_name!r}"
                f"  vibe={raw_vibe:+d}  {vibe_label}{src_tag}"
            )

    if not args.dry_run:
        save_manifest(manifest_path, manifest)

    print(f"\ndone: grabbed={grabbed}/{args.count}  manifest={manifest_path}  out={out_dir}")
    return 0 if grabbed > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
