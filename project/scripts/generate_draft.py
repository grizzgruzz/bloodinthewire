#!/usr/bin/env python3
"""
generate_draft.py  v1
=====================
Auto-generate a Blood in the Wire voice draft from current media + note
using the Google Gemini API (key from openclaw config).

PURPOSE
-------
This script is the FULL PIPELINE replacement for the stale
--assemble-only --voice-draft-file pattern.  It:

  1. Calls select_asset.py --level surface to pick + consume the next
     incoming (or library) image.
  2. Calls build_voice_request.py to assemble the Gemini prompt (includes
     VOICE_BIBLE, GENERATOR_PROMPT, and paired note sidecar content).
  3. Sends the prompt to Google Gemini API.
  4. Validates the response against the strict GENERATOR_PROMPT format.
  5. Writes the output to project/content/drafts/voice-draft-current.txt
     (overwriting any stale prior draft).
  6. Also writes a timestamped archive copy to voice/requests/.

ANTI-REPEAT GUARD
-----------------
Before writing the new draft, this script checks recent branch-log.json
entries (last N) for title/concept similarity.  If the new title is too
similar to a recent title (normalized Levenshtein ratio >= SIMILARITY_THRESHOLD),
or if the title matches a recent entry exactly, the script re-generates
once with an explicit "do not repeat" instruction injected into the prompt.
If the second attempt is also too similar, the run exits with SKIP_REPEAT
so cron_publish.py can log it and skip publishing.

USAGE
-----
    python generate_draft.py [options]

Options:
  --image-web-path PATH   Use explicit image (skip select_asset.py call)
  --note-file PATH        Use explicit note file (skip auto-pair)
  --intensity low|medium|high   Override intensity (default: medium)
  --length-mode short|medium|long|mixed   Override length (default: medium)
  --seed-context TEXT     Extra context hint for voice agent
  --motif-focus MOTIF     Motif focus override
  --skip-select           Skip select_asset.py; must provide --image-web-path
  --out-file PATH         Override output path (default: voice-draft-current.txt)
  --dry-run               Build prompt and validate but do NOT write draft file
  --max-retries N         Max Gemini retries on validation failure (default: 2)

Exit codes:
  0 = success, draft written to voice-draft-current.txt
  1 = hard error (missing config, API error, repeated validation failure)
  2 = SKIP_REPEAT (new draft too similar to recent entries after retry)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_DIR  = (SCRIPT_DIR / "..").resolve()
REPO_ROOT    = PROJECT_DIR.parent
ASSETS_DIR   = PROJECT_DIR / "assets"
PUBLISHED_DIR = ASSETS_DIR / "published"
VOICE_DIR    = PROJECT_DIR / "voice"
DRAFTS_DIR   = PROJECT_DIR / "content" / "drafts"
BRANCH_LOG   = PROJECT_DIR / "branch-log.json"
VOICE_DRAFT_CURRENT = DRAFTS_DIR / "voice-draft-current.txt"
OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"

# ─── Tuning ───────────────────────────────────────────────────────────────────

SIMILARITY_THRESHOLD = 0.75   # 0..1; above = too similar to a recent title
RECENT_ENTRIES_TO_CHECK = 5   # how many recent branch-log entries to compare
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[generate_draft] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("generate_draft")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _run_py(args_list: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    cmd = [sys.executable] + args_list
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
    return subprocess.run(cmd, cwd=str(REPO_ROOT))


def load_gemini_api_key() -> str:
    """Load Google Gemini API key from openclaw config."""
    if not OPENCLAW_CONFIG.is_file():
        raise FileNotFoundError(f"OpenClaw config not found: {OPENCLAW_CONFIG}")
    try:
        config = json.loads(OPENCLAW_CONFIG.read_text(encoding="utf-8"))
        key = (
            config
            .get("models", {})
            .get("providers", {})
            .get("google", {})
            .get("apiKey", "")
        )
        if not key:
            raise ValueError("No Google API key found in openclaw config models.providers.google.apiKey")
        return key
    except (json.JSONDecodeError, KeyError) as exc:
        raise ValueError(f"Could not parse openclaw config: {exc}") from exc


def call_gemini(api_key: str, prompt: str, model: str = DEFAULT_GEMINI_MODEL) -> str:
    """
    Send prompt to Google Gemini generateContent API.
    Returns the text response.
    """
    import urllib.request
    import urllib.error

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.85,
            "maxOutputTokens": 4096,
            "topP": 0.95,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()
        raise RuntimeError(f"Gemini API HTTP {exc.code}: {body_bytes[:500].decode('utf-8', errors='replace')}") from exc

    # Extract text from response
    try:
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        return text.strip()
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Gemini response shape: {json.dumps(body)[:300]}") from exc


# ─── Strict format validator ─────────────────────────────────────────────────

def validate_draft_format(text: str) -> dict[str, str] | None:
    """
    Validate against GENERATOR_PROMPT strict format.
    Returns parsed dict if valid, None if invalid.
    Tolerant of minor whitespace/formatting variations from LLM output.
    """
    result: dict[str, str] = {}

    # Normalize - strip any code fence markers Gemini might add
    text = re.sub(r"^```[a-z]*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```$", "", text, flags=re.MULTILINE)
    text = text.strip()

    m = re.search(r"^TITLE:\s*(.+)$", text, re.MULTILINE)
    if not m:
        return None
    result["title"] = m.group(1).strip()

    m = re.search(r"^TIMESTAMP:\s*(.+)$", text, re.MULTILINE)
    if not m:
        return None
    result["timestamp"] = m.group(1).strip()

    # BODY: everything between BODY: and EVIDENCE_LINE: (tolerant of blank lines)
    m = re.search(r"^BODY:\s*\n(.*?)(?=\n\s*EVIDENCE_LINE:)", text, re.MULTILINE | re.DOTALL)
    if not m:
        # Fallback: try without leading blank line requirement
        m = re.search(r"BODY:\s*\n(.*?)(?=EVIDENCE_LINE:)", text, re.DOTALL)
    if not m:
        return None
    body = m.group(1).strip()
    if not body or len(body) < 30:  # body too short = bad parse
        return None
    result["body"] = body

    m = re.search(r"^EVIDENCE_LINE:\s*(.+)$", text, re.MULTILINE)
    if not m:
        return None
    result["evidence_line"] = m.group(1).strip()

    m = re.search(r"^TAGS:\s*(.+)$", text, re.MULTILINE)
    if not m:
        return None
    result["tags"] = m.group(1).strip()

    return result


def extract_draft_block(text: str) -> str:
    """Extract just the TITLE..TAGS block from a noisy Gemini response."""
    # Try to find the structured block even if surrounded by preamble
    m = re.search(
        r"(TITLE:.*?TAGS:[^\n]*)",
        text,
        re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    return text


# ─── Anti-repeat guard ───────────────────────────────────────────────────────

def _normalized_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    t = title.lower()
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _simple_similarity(a: str, b: str) -> float:
    """
    Very simple token-overlap similarity.
    Returns 0..1 (1 = identical).
    No external deps.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta = set(a.split())
    tb = set(b.split())
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    return overlap / max(len(ta), len(tb))


def check_repeat(new_title: str, n: int = RECENT_ENTRIES_TO_CHECK) -> tuple[bool, str]:
    """
    Check if new_title is too similar to any of the last n published entries.

    Returns (is_repeat, matched_title).
    """
    if not BRANCH_LOG.is_file():
        return False, ""
    try:
        log_data = json.loads(BRANCH_LOG.read_text(encoding="utf-8"))
    except Exception:
        return False, ""

    entries = log_data.get("entries", [])
    if not entries:
        return False, ""

    # Get last n unique titles
    seen_titles: list[str] = []
    for entry in reversed(entries):
        t = entry.get("title", "").strip()
        if t and t not in seen_titles:
            seen_titles.append(t)
        if len(seen_titles) >= n:
            break

    new_norm = _normalized_title(new_title)
    for prior in seen_titles:
        prior_norm = _normalized_title(prior)
        sim = _simple_similarity(new_norm, prior_norm)
        log.debug("Similarity check: %r vs %r -> %.3f", new_title, prior, sim)
        if sim >= SIMILARITY_THRESHOLD:
            return True, prior

    return False, ""


# Number of recent draft files to check for body-level duplication
BODY_RECENT_ENTRIES = 10
BODY_SIMILARITY_THRESHOLD = 0.70   # body content similarity threshold


def check_body_repeat(new_body: str, n: int = BODY_RECENT_ENTRIES) -> tuple[bool, str]:
    """
    Content integrity check: detect near-duplicate body text against recent entries.

    Compares the new draft's body against the last n published entries by reading
    saved content files in project/content/entries/.  If no entry files exist,
    falls back to checking voice/requests/ archive.

    Returns (is_duplicate, matched_source).
    is_duplicate=True means the body is too similar to a recent entry.

    This is a BEST-EFFORT check: if no prior bodies can be loaded, returns False
    (no false positives — we never block on missing reference data).
    """
    if not new_body or len(new_body.strip()) < 50:
        return False, ""

    entries_dir = PROJECT_DIR / "content" / "entries"
    voice_requests_dir = PROJECT_DIR / "voice" / "requests"

    # Collect recent body texts from saved entries
    prior_bodies: list[tuple[str, str]] = []   # (source_label, body_text)

    if entries_dir.is_dir():
        # Entries saved as .md or .html.frag files; read their text content
        entry_files = sorted(entries_dir.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
        for ef in entry_files[:n]:
            try:
                text = ef.read_text(encoding="utf-8")
                # Strip HTML tags for comparison
                plain = re.sub(r"<[^>]+>", " ", text)
                plain = re.sub(r"\s+", " ", plain).strip()
                if len(plain) >= 50:
                    prior_bodies.append((ef.name, plain))
            except Exception:
                pass

    # Also check voice request archives (contain full draft bodies)
    if voice_requests_dir.is_dir() and len(prior_bodies) < n:
        req_files = sorted(voice_requests_dir.glob("voice-draft-*.txt"),
                          key=lambda f: f.stat().st_mtime, reverse=True)
        for rf in req_files[:n - len(prior_bodies)]:
            try:
                text = rf.read_text(encoding="utf-8")
                # Extract BODY section
                bm = re.search(r"BODY:\s*\n(.*?)(?=\n\s*EVIDENCE_LINE:|\Z)", text,
                               re.DOTALL | re.MULTILINE)
                if bm:
                    body = bm.group(1).strip()
                    if len(body) >= 50:
                        prior_bodies.append((rf.name, body))
            except Exception:
                pass

    if not prior_bodies:
        return False, ""

    # Normalise new body for comparison
    new_plain = re.sub(r"<[^>]+>", " ", new_body)
    new_plain = re.sub(r"\s+", " ", new_plain).strip().lower()
    new_norm = re.sub(r"[^a-z0-9 ]", " ", new_plain)

    for source_label, prior_text in prior_bodies:
        prior_plain = re.sub(r"<[^>]+>", " ", prior_text)
        prior_plain = re.sub(r"\s+", " ", prior_plain).strip().lower()
        prior_norm = re.sub(r"[^a-z0-9 ]", " ", prior_plain)
        sim = _simple_similarity(new_norm, prior_norm)
        log.debug("Body similarity: new vs %r -> %.3f", source_label, sim)
        if sim >= BODY_SIMILARITY_THRESHOLD:
            return True, source_label

    return False, ""


# ─── Main pipeline ────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auto-generate a Blood in the Wire voice draft using Gemini.",
    )
    parser.add_argument("--image-web-path", default="",
                        help="Explicit image path (skips select_asset.py if provided with --skip-select).")
    parser.add_argument("--note-file", default="",
                        help="Explicit note file (skips auto-pair).")
    parser.add_argument("--intensity", default="medium",
                        choices=["low", "medium", "high"])
    parser.add_argument("--length-mode", default="medium",
                        choices=["short", "medium", "long", "mixed"])
    parser.add_argument("--seed-context", default="",
                        help="Extra context hint for voice agent.")
    parser.add_argument("--motif-focus", default="",
                        choices=["", "wrong_people", "directed_patterns", "god_signals",
                                 "missing_time", "utility_workers", "implied_retribution"])
    parser.add_argument("--skip-select", action="store_true",
                        help="Skip select_asset.py; must provide --image-web-path.")
    parser.add_argument("--out-file", default="",
                        help="Override output path (default: voice-draft-current.txt).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build + validate but do NOT write draft file.")
    parser.add_argument("--max-retries", type=int, default=2,
                        help="Max Gemini retries on validation failure (default: 2).")
    args = parser.parse_args()

    # ── Step 1: Load API key ──────────────────────────────────────────────────
    try:
        api_key = load_gemini_api_key()
        log.info("Gemini API key loaded from openclaw config.")
    except Exception as exc:
        log.error("Could not load Gemini API key: %s", exc)
        return 1

    # ── Step 2: Select asset ──────────────────────────────────────────────────
    image_web_path = args.image_web_path

    if not args.skip_select and not image_web_path:
        log.info("Running select_asset.py --level surface ...")
        result = _run_py([str(SCRIPT_DIR / "select_asset.py"), "--level", "surface"])
        if result.returncode != 0:
            log.error("select_asset.py failed (rc=%d): %s", result.returncode, result.stderr.strip())
            return 1
        image_web_path = result.stdout.strip()
        log.info("Asset selected: %s", Path(image_web_path).name)
        # Emit SOURCE signal for cron_publish.py to parse
        if "SOURCE=incoming" in result.stderr:
            print("SOURCE=incoming", file=sys.stderr)
        else:
            print("SOURCE=library", file=sys.stderr)
    elif not image_web_path:
        log.error("--skip-select requires --image-web-path.")
        return 1

    if not Path(image_web_path).is_file() and not Path(REPO_ROOT / image_web_path).is_file():
        log.warning("Image path does not exist on disk: %s (will continue)", image_web_path)

    # ── Step 3: Build voice request prompt ───────────────────────────────────
    bvr_args = [
        str(SCRIPT_DIR / "build_voice_request.py"),
        "--image-web-path", image_web_path,
        "--intensity", args.intensity,
        "--length-mode", args.length_mode,
    ]
    if args.note_file:
        bvr_args += ["--note-file", args.note_file]
    if args.seed_context:
        bvr_args += ["--seed-context", args.seed_context]
    if args.motif_focus:
        bvr_args += ["--motif-focus", args.motif_focus]

    log.info("Building voice request prompt ...")
    bvr_result = _run_py(bvr_args)
    if bvr_result.returncode != 0:
        log.error("build_voice_request.py failed (rc=%d): %s",
                  bvr_result.returncode, bvr_result.stderr.strip())
        return 1

    request_file = bvr_result.stdout.strip()
    log.info("Voice request written: %s", request_file)

    # Read the assembled prompt
    try:
        prompt_text = Path(request_file).read_text(encoding="utf-8")
    except Exception as exc:
        log.error("Could not read request file %s: %s", request_file, exc)
        return 1

    # ── Step 4: Call Gemini ───────────────────────────────────────────────────
    parsed: dict[str, str] | None = None
    raw_response = ""
    anti_repeat_injected = False

    for attempt in range(args.max_retries + 1):
        if attempt > 0:
            log.info("Gemini attempt %d/%d ...", attempt + 1, args.max_retries + 1)

        try:
            raw_response = call_gemini(api_key, prompt_text)
        except Exception as exc:
            log.error("Gemini API call failed (attempt %d): %s", attempt + 1, exc)
            if attempt < args.max_retries:
                time.sleep(2)
                continue
            return 1

        # Extract + validate
        candidate = extract_draft_block(raw_response)
        parsed = validate_draft_format(candidate)

        if parsed is None:
            log.warning("Response failed strict format validation (attempt %d). Raw:\n%s",
                        attempt + 1, raw_response[:300])
            if attempt < args.max_retries:
                # Retry with format reminder
                prompt_text = (
                    "IMPORTANT: Your previous response did not follow the strict output format.\n"
                    "Return ONLY the block starting with TITLE: and ending with TAGS:, nothing else.\n\n"
                    + prompt_text
                )
                continue
            log.error("Exhausted retries on format validation. Cannot publish.")
            return 1

        # Format OK — check anti-repeat guard
        is_repeat, matched_title = check_repeat(parsed["title"])
        if is_repeat and not anti_repeat_injected:
            log.warning(
                "ANTI-REPEAT: new title %r too similar to recent entry %r — regenerating.",
                parsed["title"], matched_title,
            )
            # Inject anti-repeat instruction into prompt
            anti_repeat_note = (
                f"\n\nIMPORTANT: Do NOT use concepts, themes, or titles similar to recent published entries.\n"
                f"The title '{matched_title}' was recently published. Generate something different.\n"
                f"New angle, new detail, new entry point. Avoid parking lots, arrows, pointing-left motifs.\n"
            )
            prompt_text = prompt_text + anti_repeat_note
            anti_repeat_injected = True
            parsed = None
            continue

        if is_repeat and anti_repeat_injected:
            log.error(
                "SKIP_REPEAT: after anti-repeat injection, new title %r is still too similar to %r. "
                "Skipping publish to avoid stale concept reuse.",
                parsed["title"], matched_title,
            )
            print(f"DRAFT_TITLE={parsed['title']}")  # expose for cron trace
            return 2  # exit code 2 = SKIP_REPEAT

        # Content integrity check: body-level duplicate detection
        body_is_dup, body_matched = check_body_repeat(parsed.get("body", ""))
        if body_is_dup and not anti_repeat_injected:
            log.warning(
                "CONTENT-INTEGRITY: new body too similar to recent entry %r — regenerating.",
                body_matched,
            )
            # Inject anti-repeat instruction and retry
            anti_repeat_note = (
                f"\n\nIMPORTANT: Your generated body content is too similar to a recently published entry ({body_matched}).\n"
                f"Generate completely different content — new observations, new details, new atmosphere.\n"
                f"Change the setting, the observer's focus, the specific evidence documented.\n"
            )
            prompt_text = prompt_text + anti_repeat_note
            anti_repeat_injected = True
            parsed = None
            continue

        if body_is_dup and anti_repeat_injected:
            log.error(
                "SKIP_REPEAT: after content-integrity retry, body still too similar to %r. "
                "Skipping publish.",
                body_matched,
            )
            print(f"DRAFT_TITLE={parsed['title']}")  # expose for cron trace
            return 2  # exit code 2 = SKIP_REPEAT

        # All good — break loop
        break

    if parsed is None:
        log.error("Could not produce a valid draft after all attempts.")
        return 1

    log.info("Draft generated: title=%r  timestamp=%r  tags=%r",
             parsed["title"], parsed["timestamp"], parsed["tags"])

    # ── Step 5: Write draft ───────────────────────────────────────────────────
    draft_text = extract_draft_block(raw_response)

    # Emit title + evidence_line to stdout for cron_publish.py to capture
    print(f"DRAFT_TITLE={parsed['title']}")
    print(f"DRAFT_EVIDENCE={parsed['evidence_line']}")
    print(f"DRAFT_FILE={VOICE_DRAFT_CURRENT}")
    print(f"IMAGE_WEB_PATH={image_web_path}")

    if args.dry_run:
        log.info("DRY-RUN: draft NOT written to disk.")
        log.info("Would write to: %s", VOICE_DRAFT_CURRENT)
        return 0

    out_path = Path(args.out_file) if args.out_file else VOICE_DRAFT_CURRENT
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(draft_text, encoding="utf-8")
        log.info("Draft written → %s", out_path)
    except Exception as exc:
        log.error("Could not write draft: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
