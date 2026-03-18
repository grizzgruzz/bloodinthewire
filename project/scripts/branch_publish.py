#!/usr/bin/env python3
"""
branch_publish.py  v5
=====================
Branching-publish engine for bloodinthewire.

OVERVIEW
--------
When a new post is ready to publish, this script decides (by random coin
flip, stored for reproducibility) whether the content appears:

  • INLINE on the current page (roll=1):
      → A rich cascade card is inserted into index.html / the target page.

  • AS A LINK (roll=0):
      → The engine first checks for a contextually relevant EXISTING page.
        If one is found (by motif/keyword overlap ≥ convergence threshold),
        the link points there and BRANCHING ENDS — no new page is created.
        If no relevant existing page is found, a new destination page is
        created and the engine recurses up to --depth-cap rolls.

This is the CONVERGENCE RULE (v3, hard rule):
  - A generated link may point to a new contextual page OR an already
    existing contextual page.
  - If it points to an existing page, branching ENDS there (no recursion).
  - Reused/existing link targets must be contextually relevant to the
    source post (shared motif / tone / evidence class / semantic fit).
  - Relevance is determined mechanically by motif-word overlap between
    the new post's title+teaser and the candidate page's slug+title+h2.

v2 additions
------------
  • ORIENTATION roll (vertical|horizontal): stored alongside branch roll.
    Horizontal cards display image and text side-by-side.
  • INSERTION POSITION roll: new cards land at any position within the
    CASCADE block, not always at the top. The index is stored for
    reproducibility. 0 = before first block, N = after Nth block.

v3 additions
------------
  • CONVERGENCE RULE: link-existing action, branching stops on reuse.
  • Contextual relevance selection for existing page reuse.
  • --convergence-threshold option (default 1: any shared keyword qualifies).
  • --no-convergence flag to disable for testing/forced-growth passes.

v4 additions
------------
  • DEPTH-AWARE MEDIA SOURCE RULE (hard):
      depth=0 (surface / front-page):  may use assets from incoming/ when
        the media roll allows an image.  incoming/ retains highest priority.
      depth>0 (lower-level / linked / recursive pages):  incoming/ is
        NEVER used.  Only vetted library assets may appear.
  • image_web_path sourced from incoming/ is explicitly CLEARED when
    passed down to deeper pages so it cannot leak into child/node pages.
  • Existing sanitation rules (metadata strip, consume-on-use) remain in
    force at every level where an image is used.

v5 additions
------------
  • SURFACE MEDIA VISIBILITY RULE (hard):
      If the surface media roll hits (image_web_path non-empty at depth=0),
        the homepage/surface card MUST visibly show that media.
      If the roll misses (image_web_path empty), the card shows no media.
      This rule applies to ALL card types, including link and convergence cards.
      Previously, make_link_card silently discarded image_web_path — this is
      now fixed.  Link/convergence cards render a visible thumbnail (.link-thumb)
      when image_web_path is non-empty.
  • make_link_card now accepts image_web_path parameter.
  • branch_resolve passes effective_image to all make_link_card calls.
  • log_record now records image_web_path for auditability.

USAGE
-----
  python branch_publish.py \\
      --title "entry_0007 :: something" \\
      --teaser "one-line teaser" \\
      --posted-date 2026-03-18 \\
      --fragment-href "fragments/entry-0007.html" \\
      [--timestamp "HH:MM"] \\
      [--body-file path/to/body.html] \\
      [--image-web-path project/assets/web/img.jpg] \\
      [--image-source incoming|library]     # tracks origin; 'incoming' blocked below depth=0
      [--target-page index.html]            # defaults to index.html
      [--depth-cap 5]                       # max branching depth (default 5)
      [--convergence-threshold 1]           # min shared keywords to reuse (default 1)
      [--no-convergence]                    # disable reuse check (testing only)
      [--force-roll 1]                      # override branch roll (testing only)
      [--force-orientation vertical]        # override orientation (testing only)
      [--force-insertion-index 0]           # override insertion index (testing only)
      [--links-note "short note"]

OUTPUTS (depending on roll results)
-------------------------------------
  • Edits index.html (or --target-page) in place
  • May create nodes/<slug>.html (junction pages)
  • Appends to project/branch-log.json
  • Prints a summary to stdout

DESIGN NOTES
------------
  - Depth cap is soft: at max depth, always treated as roll=1 (inline).
  - Cascade position (cp-a…cp-g) cycles deterministically by entry count.
  - Node pages accumulate links and are themselves valid target pages for
    future deeper posts. The spiderweb grows organically.
  - Orientation (vertical|horizontal) is rolled once per root publish call
    and stored permanently in branch-log.json. It is not re-derived.
  - Insertion index is rolled once and stored. 0 = top, N = after Nth
    existing cascade block. Ensures reproducible page structure.
  - PUBLIC LINK ENFORCEMENT: --fragment-href is validated at startup.
    Only fragments/*.html, nodes/*.html, index.html, and root-level .html
    pages are accepted.  Any path into project/, content/, *.md, *.frag,
    *.json, etc. is hard-rejected.  This ensures the live site never links
    to metadata, draft artifacts, or implementation scaffolding.
  - CONVERGENCE: when roll=0, the engine first asks "is there an existing
    page contextually relevant to this post?" before creating anything new.
    Relevance = shared keyword/motif overlap between post title+teaser and
    candidate page slug+title+h2. Score is the intersection count; the
    best-scoring candidate above threshold wins. Ties break alphabetically
    (deterministic). If no relevant existing page exists, a new page is
    created as in v2. This keeps the site web-like — things link to things
    that make sense — and prevents runaway node sprawl.
  - This script uses stdlib only (no third-party deps).
"""

from __future__ import annotations

import argparse
import html as _html
import json
import os
import random
import re
import sys
import time
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

REPO_ROOT    = Path(__file__).resolve().parent.parent.parent   # /home/gruzz/bloodinthewire
INDEX_HTML   = REPO_ROOT / 'index.html'
NODES_DIR    = REPO_ROOT / 'nodes'
BRANCH_LOG   = REPO_ROOT / 'project' / 'branch-log.json'

DEPTH_CAP_DEFAULT           = 5
CONVERGENCE_THRESHOLD_DEFAULT = 1   # min shared motif words to reuse existing page
CASCADE_POSITIONS = ['cp-a', 'cp-b', 'cp-c', 'cp-d', 'cp-e', 'cp-f', 'cp-g']
ORIENTATIONS      = ['vertical', 'horizontal']

# ── Public link allowlist ─────────────────────────────────────────────────────
# Any --fragment-href passed to branch_publish MUST match one of these prefixes
# (relative to repo root, forward-slash separated).  Paths that do NOT match
# are rejected at startup so non-diegetic / mechanical targets can never appear
# in the live site.
#
# Approved destinations:
#   fragments/*.html   — public field-note / sighting pages
#   nodes/*.html       — branch junction pages (generated by this script)
#   index.html         — main entrypoint
#   *.html             — any root-level public HTML page
PUBLIC_LINK_PREFIXES = (
    'fragments/',
    'nodes/',
)
PUBLIC_LINK_EXACT = {
    'index.html',
}

# Fragments that look like these patterns are NEVER public:
_NON_PUBLIC_PATTERNS = re.compile(
    r'(project/|content/|scripts/|voice/|\.frag$|\.md$|\.json$|\.txt$|\.py$)',
    re.IGNORECASE,
)


def validate_fragment_href(href: str) -> str:
    """
    Validate that a fragment href is a legal public-site destination.

    Raises SystemExit with an explanatory message if the href is non-public
    (e.g. points into project/, content/, or has a non-HTML extension).

    Returns the href unchanged when valid.
    """
    if not href:
        # Empty href is allowed (inline card with no link)
        return href

    # Normalise separators
    norm = href.replace('\\', '/').lstrip('/')

    # Hard block: anything that looks like an internal/mechanical path
    if _NON_PUBLIC_PATTERNS.search(norm):
        raise SystemExit(
            f'ERROR: --fragment-href "{href}" points to a non-public path.\n'
            f'  Allowed destinations: fragments/*.html, nodes/*.html, index.html, '
            f'or another root-level .html page.\n'
            f'  Internal paths (project/, content/, .md, .frag, .json, etc.) '
            f'must never appear on the public site.\n'
            f'  Create a proper fragments/<slug>.html page first, then re-run.'
        )

    # Must match an approved prefix or be a known exact match
    is_approved = (
        any(norm.startswith(pfx) for pfx in PUBLIC_LINK_PREFIXES)
        or norm in PUBLIC_LINK_EXACT
        or (norm.endswith('.html') and '/' not in norm)  # root-level html
    )
    if not is_approved:
        raise SystemExit(
            f'ERROR: --fragment-href "{href}" is not in the approved public path list.\n'
            f'  Allowed: fragments/*.html, nodes/*.html, index.html, or a root-level .html.\n'
            f'  Got: "{norm}"\n'
            f'  If this is a new public page, place it under fragments/ or nodes/.'
        )

    # Must end in .html
    if not norm.endswith('.html'):
        raise SystemExit(
            f'ERROR: --fragment-href "{href}" does not end in .html.\n'
            f'  Only rendered HTML pages may be linked from the public site.'
        )

    return href


# ── Branch log ────────────────────────────────────────────────────────────────

def load_branch_log() -> dict:
    if BRANCH_LOG.exists():
        try:
            return json.loads(BRANCH_LOG.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'entries': [], 'meta': {'version': 3}}


def save_branch_log(log: dict) -> None:
    BRANCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    BRANCH_LOG.write_text(json.dumps(log, indent=2), encoding='utf-8')


def log_entry(log: dict, entry: dict) -> None:
    log.setdefault('entries', []).append(entry)


# ── Cascade position picker ───────────────────────────────────────────────────

def pick_cascade_pos(page_path: Path) -> str:
    """
    Cycle through CASCADE_POSITIONS based on how many cascade-blocks already
    exist on the target page. Deterministic: count of existing blocks mod 7.
    """
    try:
        src = page_path.read_text(encoding='utf-8')
        count = len(re.findall(r'class="cascade-block', src))
        return CASCADE_POSITIONS[count % len(CASCADE_POSITIONS)]
    except Exception:
        return CASCADE_POSITIONS[0]


def count_cascade_blocks(page_path: Path) -> int:
    """Return number of cascade-block sections in the CASCADE region of a page."""
    try:
        src = page_path.read_text(encoding='utf-8')
        cascade_start = '<!-- CASCADE:START -->'
        cascade_end   = '<!-- CASCADE:END -->'
        if cascade_start not in src:
            return 0
        start = src.index(cascade_start) + len(cascade_start)
        end   = src.index(cascade_end) if cascade_end in src else len(src)
        region = src[start:end]
        return len(re.findall(r'<section\s+class="cascade-block', region))
    except Exception:
        return 0


# ── Orientation + insertion rolls ─────────────────────────────────────────────

def roll_orientation(force: str | None = None) -> str:
    """Roll vertical or horizontal orientation. Stored once, never re-rolled."""
    if force is not None and force in ORIENTATIONS:
        return force
    return random.choice(ORIENTATIONS)


def roll_insertion_index(n_existing: int, force: int | None = None) -> int:
    """
    Roll an insertion index in [0, n_existing].
    0 = before first block (top), N = after Nth block.
    Stored once; reproducible from branch-log.
    """
    if force is not None:
        return max(0, min(int(force), n_existing))
    if n_existing <= 0:
        return 0
    return random.randint(0, n_existing)


# ── Contextual relevance for existing-page convergence ───────────────────────
# Hard rule (v3): when roll=0, the engine checks for an existing page that is
# contextually relevant to the new post BEFORE creating anything new.
#
# Relevance is determined by motif-word overlap:
#   - "Motif words" = lowercase alphanum tokens ≥ 4 chars, filtered for common
#     stop words, extracted from the new post's title+teaser.
#   - Each candidate page's motif words are derived from its filename slug,
#     HTML <title>, and first <h2>.
#   - Score = intersection size.  Ties break alphabetically (deterministic).
#   - If score >= convergence_threshold, the highest-scoring candidate wins.
#   - If no candidate meets the threshold, no convergence — new page created.
#
# This keeps the site web-like and contextually coherent: pages about sightings
# link back to sighting pages; frequency anomalies link back to frequency notes.

_STOP_WORDS = frozenset({
    'this', 'that', 'with', 'from', 'have', 'been', 'will', 'they',
    'were', 'when', 'then', 'than', 'into', 'some', 'what', 'also',
    'more', 'each', 'only', 'over', 'such', 'very', 'just', 'like',
    'here', 'there', 'about', 'after', 'before', 'still', 'again',
    'other', 'first', 'would', 'could', 'their', 'which', 'where',
    'same', 'once', 'back', 'down', 'away', 'does', 'both',
})


def _motif_words(text: str) -> frozenset:
    """
    Extract normalized motif words (length >= 4, no stop words) from free text.
    Used for both post sources and candidate pages.
    """
    tokens = re.split(r'[^a-z0-9]+', text.lower())
    return frozenset(t for t in tokens if len(t) >= 4 and t not in _STOP_WORDS)


def _page_motif_words(page_path: Path) -> frozenset:
    """
    Extract motif words from a page's filename slug, <title>, and first <h2>.
    Also reads optional data-motif="..." attribute if present.
    Used for relevance scoring of existing pages.
    """
    words: set = set()

    # Filename slug (e.g. "sighting-0002" → {"sighting", "0002"})
    stem = page_path.stem.replace('-', ' ').replace('_', ' ')
    words |= _motif_words(stem)

    try:
        src = page_path.read_text(encoding='utf-8')

        # <title> text
        m = re.search(r'<title[^>]*>(.*?)</title>', src, re.IGNORECASE | re.DOTALL)
        if m:
            title_text = re.sub(r'<[^>]+>', '', m.group(1))
            words |= _motif_words(title_text)

        # First <h2> text
        m2 = re.search(r'<h2[^>]*>(.*?)</h2>', src, re.IGNORECASE | re.DOTALL)
        if m2:
            h2_text = re.sub(r'<[^>]+>', '', m2.group(1))
            words |= _motif_words(h2_text)

        # Optional data-motif attribute for hand-crafted relevance hints
        m3 = re.search(r'data-motif="([^"]*)"', src)
        if m3:
            words |= _motif_words(m3.group(1).replace(',', ' '))

    except Exception:
        pass

    return frozenset(words)


def _dest_href_from_target(dest_path: Path, repo_root: Path, target_page: Path) -> str:
    """
    Compute the relative href from target_page to dest_path,
    both relative to repo_root.

    If target_page is at root (index.html), returns "fragments/foo.html".
    If target_page is one level deep (nodes/), returns "../fragments/foo.html".
    """
    dest_rel = str(dest_path.relative_to(repo_root)).replace('\\', '/')
    target_dir = target_page.parent
    target_dir_rel = str(target_dir.relative_to(repo_root)).replace('\\', '/')

    if target_dir_rel == '.':
        return dest_rel
    else:
        # One level deep — prefix with ../
        return '../' + dest_rel


def find_existing_contextual_pages(
    repo_root: Path,
    exclude_norm_paths: set,
) -> list:
    """
    Return list of (page_path, motif_words_frozenset) for all existing public
    fragment pages and content-type node pages.

    exclude_norm_paths: set of forward-slash normalised paths relative to
    repo_root to skip — typically the current target page and the current
    post's own fragment href.

    Returns an empty list if the fragments/ dir does not exist yet.
    """
    candidates = []
    frags_dir = repo_root / 'fragments'
    nodes_dir = repo_root / 'nodes'

    def _norm(p: Path) -> str:
        return str(p.relative_to(repo_root)).replace('\\', '/')

    if frags_dir.exists():
        for page in sorted(frags_dir.glob('*.html')):
            if _norm(page) in exclude_norm_paths:
                continue
            candidates.append((page, _page_motif_words(page)))

    if nodes_dir.exists():
        for page in sorted(nodes_dir.glob('*.html')):
            if _norm(page) in exclude_norm_paths:
                continue
            # Only include content-type nodes (not junction nodes)
            try:
                src = page.read_text(encoding='utf-8')
                # Content nodes have "CONTENT NODE" in their header stamp
                if 'CONTENT NODE' in src:
                    candidates.append((page, _page_motif_words(page)))
            except Exception:
                pass

    return candidates


def select_relevant_existing_page(
    title: str,
    teaser: str,
    candidates: list,
    threshold: int = 1,
):
    """
    Select the most contextually relevant existing page for a new post.

    Scores each candidate by the number of shared motif words between the new
    post (title + teaser) and the candidate page's motif word set.

    Returns the highest-scoring (page_path, score) tuple if score >= threshold.
    Returns None if no candidate meets the threshold.

    Ties broken by candidate filename (alphabetical, deterministic — not random).
    Relevance preference: shared motif / tone / evidence class / semantic fit.
    """
    post_words = _motif_words(title + ' ' + teaser)
    if not post_words:
        return None

    scored = []
    for page, page_words in candidates:
        score = len(post_words & page_words)
        if score >= threshold:
            scored.append((score, page.name, page))

    if not scored:
        return None

    # Highest score wins; ties break alphabetically (deterministic)
    scored.sort(key=lambda x: (-x[0], x[1]))
    best_score = scored[0][0]
    best_page  = scored[0][2]
    return best_page, best_score


# ── HTML builders ─────────────────────────────────────────────────────────────

def make_rich_card(
    entry_id: str,
    title: str,
    teaser: str,
    fragment_href: str,
    posted_date: str,
    timestamp: str,
    body_html: str,
    image_web_path: str,
    cascade_pos: str,
    depth: int,
    roll_seed: int,
    orientation: str = 'vertical',
) -> str:
    """Rich inline card — full teaser or body visible.

    orientation='vertical'   → stacked layout (image below text, default)
    orientation='horizontal' → side-by-side layout (image left, text right)
    """
    orient_class = f'cascade-orient-{orientation}'

    ts_line = ''
    if timestamp:
        ts_line = f'      <p><strong>{_html.escape(timestamp)}</strong> — {_html.escape(teaser)}</p>\n'
    else:
        ts_line = f'      <p>{_html.escape(teaser)}</p>\n'

    body_section = ''
    if body_html:
        body_section = f'      <div class="wire-body">{body_html}</div>\n'

    img_section = ''
    if image_web_path:
        img_section = (
            f'      <figure class="evidence">'
            f'<img src="{_html.escape(image_web_path)}" alt="evidence"></figure>\n'
        )

    link_line = ''
    if fragment_href:
        link_line = f'      <p><a href="{_html.escape(fragment_href)}">open entry</a></p>\n'

    stamp_line = f'      <p class="stamp">posted: {_html.escape(posted_date)}</p>\n'

    if orientation == 'horizontal' and image_web_path:
        # Side-by-side: image left, text content right
        inner = (
            f'      <div class="horiz-media">\n'
            f'        <figure class="evidence">'
            f'<img src="{_html.escape(image_web_path)}" alt="evidence"></figure>\n'
            f'      </div>\n'
            f'      <div class="horiz-text">\n'
            f'        <h2>{_html.escape(title)}</h2>\n'
        )
        if timestamp:
            inner += f'        <p><strong>{_html.escape(timestamp)}</strong> — {_html.escape(teaser)}</p>\n'
        else:
            inner += f'        <p>{_html.escape(teaser)}</p>\n'
        if body_html:
            inner += f'        <div class="wire-body">{body_html}</div>\n'
        if fragment_href:
            inner += f'        <p><a href="{_html.escape(fragment_href)}">open entry</a></p>\n'
        inner += f'        <p class="stamp">posted: {_html.escape(posted_date)}</p>\n'
        inner += '      </div>\n'

        return (
            f'    <!-- branch: inline  depth={depth}  seed={roll_seed}  orient={orientation} -->\n'
            f'    <section class="cascade-block cascade-rich {cascade_pos} {orient_class}"'
            f' data-entry="{_html.escape(entry_id)}"'
            f' data-type="inline"'
            f' data-depth="{depth}"'
            f' data-branch-seed="{roll_seed}"'
            f' data-orientation="{orientation}">\n'
            f'{inner}'
            f'    </section>\n\n'
        )
    else:
        # Vertical (default): stacked layout
        return (
            f'    <!-- branch: inline  depth={depth}  seed={roll_seed}  orient={orientation} -->\n'
            f'    <section class="cascade-block cascade-rich {cascade_pos} {orient_class}"'
            f' data-entry="{_html.escape(entry_id)}"'
            f' data-type="inline"'
            f' data-depth="{depth}"'
            f' data-branch-seed="{roll_seed}"'
            f' data-orientation="{orientation}">\n'
            f'      <h2>{_html.escape(title)}</h2>\n'
            f'{ts_line}'
            f'{body_section}'
            f'{img_section}'
            f'{link_line}'
            f'{stamp_line}'
            f'    </section>\n\n'
        )


def make_link_card(
    entry_id: str,
    title: str,
    teaser: str,
    dest_href: str,
    posted_date: str,
    cascade_pos: str,
    depth: int,
    roll_seed: int,
    is_node: bool = False,
    orientation: str = 'vertical',
    converges: bool = False,
    image_web_path: str = '',
) -> str:
    """Lean link card — label + hyperlink, with optional visible media thumbnail.

    converges=True adds a visual hint that this link points to an existing page
    (content class 'cascade-converge' is added alongside the standard class).

    image_web_path: if non-empty, a visible thumbnail is rendered at the top of
    the card (surface media roll hit).  If empty, no media is shown (roll miss).
    This enforces the hard rule: surface media roll result is always visible
    on the card itself, regardless of card type.
    """
    card_class  = 'cascade-node' if is_node else 'cascade-link'
    if converges:
        card_class += ' cascade-converge'
    link_text   = 'open node' if is_node else 'open entry'
    orient_class = f'cascade-orient-{orientation}'
    branch_comment = 'link-existing' if converges else 'link'

    # Surface media rule: if image provided, render it as a visible thumbnail.
    thumb_html = ''
    if image_web_path:
        thumb_html = (
            f'      <figure class="link-thumb">'
            f'<img src="{_html.escape(image_web_path)}" alt="evidence thumbnail"></figure>\n'
        )

    return (
        f'    <!-- branch: {branch_comment}  depth={depth}  seed={roll_seed}  orient={orientation} -->\n'
        f'    <section class="cascade-block {card_class} {cascade_pos} {orient_class}"'
        f' data-entry="{_html.escape(entry_id)}"'
        f' data-type="{"link-existing" if converges else "link"}"'
        f' data-depth="{depth}"'
        f' data-branch-seed="{roll_seed}"'
        f' data-orientation="{orientation}">\n'
        f'{thumb_html}'
        f'      <h2>{_html.escape(title)}</h2>\n'
        f'      <span class="lean-link">'
        f'<a href="{_html.escape(dest_href)}">{link_text}</a>'
        f' <span class="stamp">// {_html.escape(teaser)}</span></span>\n'
        f'      <p class="stamp">posted: {_html.escape(posted_date)}</p>\n'
        f'    </section>\n\n'
    )


def make_node_page(
    node_slug: str,
    entry_title: str,
    posted_date: str,
    depth: int,
) -> str:
    """Generate a node junction page shell."""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>bloodinthewire :: {_html.escape(node_slug)}</title>
  <link rel="stylesheet" href="../styles.css" />
</head>
<body>
  <div class="noise"></div>
  <main class="container">
    <header>
      <p class="stamp">NODE // depth={depth} // branched from: {_html.escape(entry_title)}</p>
      <h2>{_html.escape(node_slug)}</h2>
      <p class="sub">branch junction // follow the threads</p>
      <hr />
    </header>

    <div class="node-shell">
      <p class="node-label">NODE :: {_html.escape(node_slug)} // generated: {posted_date}</p>
      <!-- CASCADE:START -->
      <!-- future posts may land here via deeper branch rolls -->
      <!-- CASCADE:END -->
    </div>

    <div class="node-threads">
      <h4>threads // from this node</h4>
      <ul>
        <!-- LINKS:START -->
        <!-- LINKS:END -->
      </ul>
    </div>

    <footer>
      <hr />
      <p><a href="../index.html">return to entrypoint</a></p>
      <p class="tiny-note">depth={depth} // branched: {posted_date}</p>
    </footer>
  </main>
</body>
</html>
"""


def make_content_page(
    node_slug: str,
    entry_title: str,
    teaser: str,
    fragment_href: str,
    body_html: str,
    image_web_path: str,
    timestamp: str,
    posted_date: str,
    depth: int,
    orientation: str = 'vertical',
) -> str:
    """Generate a fresh content page (roll=1 at deeper depth)."""
    ts_block = ''
    if timestamp:
        ts_block = f'    <p><strong>{_html.escape(timestamp)}</strong> — {_html.escape(teaser)}</p>\n'
    else:
        ts_block = f'    <p>{_html.escape(teaser)}</p>\n'

    body_block = ''
    if body_html:
        body_block = f'    <div class="wire-body">\n{body_html}\n    </div>\n'

    img_block = ''
    if image_web_path:
        img_block = (
            f'    <figure class="evidence">\n'
            f'      <img src="{_html.escape(image_web_path)}" alt="evidence">\n'
            f'    </figure>\n'
        )

    frag_link = ''
    if fragment_href:
        frag_link = f'    <p><a href="../{_html.escape(fragment_href)}">full entry</a></p>\n'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>bloodinthewire :: {_html.escape(entry_title)}</title>
  <link rel="stylesheet" href="../styles.css" />
</head>
<body>
  <div class="noise"></div>
  <main class="container">
    <header>
      <p class="stamp">CONTENT NODE // depth={depth} // orient={orientation}</p>
      <h2>{_html.escape(entry_title)}</h2>
      <hr />
    </header>

{ts_block}{body_block}{img_block}{frag_link}
    <footer>
      <hr />
      <p><a href="../index.html">return to entrypoint</a></p>
      <p class="tiny-note">depth={depth} // posted: {posted_date}</p>
    </footer>
  </main>
</body>
</html>
"""


# ── Page insertion helpers ─────────────────────────────────────────────────────

def insert_cascade_card(page_path: Path, card_html: str, insertion_index: int = 0) -> None:
    """
    Insert a cascade card at a specific position within the CASCADE:START/END block.

    insertion_index=0 → before the first existing cascade block (top of stack).
    insertion_index=N → after the Nth existing cascade block.

    If insertion_index >= number of existing blocks, appends at the bottom.
    The index is rolled once at publish time and stored in branch-log.json.
    """
    src = page_path.read_text(encoding='utf-8')
    cascade_start_marker = '<!-- CASCADE:START -->'
    cascade_end_marker   = '<!-- CASCADE:END -->'

    if cascade_start_marker not in src:
        raise RuntimeError(f"No CASCADE:START marker found in {page_path}")

    # Locate the cascade region bounds in src
    start_marker_end = src.index(cascade_start_marker) + len(cascade_start_marker)
    end_marker_start = src.index(cascade_end_marker) if cascade_end_marker in src else len(src)

    cascade_region = src[start_marker_end:end_marker_start]

    # Find all <section class="cascade-block..." in the region
    block_pattern = re.compile(
        r'(<section\s+class="cascade-block[^>]*>.*?</section>)',
        re.DOTALL,
    )
    matches = list(block_pattern.finditer(cascade_region))

    if insertion_index <= 0 or not matches:
        # Insert at the very top of the cascade region (after marker line)
        insert_abs = start_marker_end
        # Skip past the immediately following newline(s)
        while insert_abs < end_marker_start and src[insert_abs] in ('\n', '\r'):
            insert_abs += 1
        src = src[:insert_abs] + '\n' + card_html + src[insert_abs:]
    else:
        # Insert after the (insertion_index)-th block (1-based clamp to count)
        idx = min(insertion_index, len(matches)) - 1
        match = matches[idx]
        # Absolute position in src = base + match.end()
        abs_insert_pos = start_marker_end + match.end()
        src = src[:abs_insert_pos] + '\n\n' + card_html + src[abs_insert_pos:]

    page_path.write_text(src, encoding='utf-8')


def insert_links_entry(page_path: Path, href: str, label: str, note: str) -> None:
    """Insert a links list item at the top of LINKS:START block."""
    src = page_path.read_text(encoding='utf-8')
    marker = '        <!-- LINKS:START -->'
    if marker not in src:
        return  # no links section, skip gracefully
    li = f'        <li><a href="{_html.escape(href)}">{_html.escape(label)}</a> ({_html.escape(note)})</li>\n'
    insert_pos = src.index(marker) + len(marker) + 1
    src = src[:insert_pos] + li + src[insert_pos:]
    page_path.write_text(src, encoding='utf-8')


# ── Depth-aware image guard ───────────────────────────────────────────────────

def image_allowed_at_depth(image_web_path: str, image_source: str, depth: int) -> str:
    """
    Enforce the hard depth-based media source rule:

    depth == 0 (surface / front-page):
        Any image is allowed, including those sourced from incoming/.

    depth > 0 (lower-level / linked / recursive pages):
        incoming/-sourced images are HARD-BLOCKED.
        If image_source == 'incoming', the image is suppressed (empty string
        returned) and a warning is logged to stderr.
        Only library-sourced images (or no image) may appear here.

    Returns the image path to use (may be empty string if blocked).
    """
    if depth == 0:
        return image_web_path  # surface level: all sources permitted

    if image_source == "incoming" and image_web_path:
        import sys
        print(
            f"[branch_publish] GUARD: depth={depth} — incoming/-sourced image "
            f"'{image_web_path}' is BLOCKED at lower level. "
            "Only library assets may appear on linked/recursive pages.",
            file=sys.stderr,
        )
        return ""  # suppress incoming image on deep pages

    return image_web_path  # library source or no image: allowed at all depths


# ── Core recursive branching logic ────────────────────────────────────────────

def roll(force: int | None = None) -> int:
    """Return 0 or 1. If force is given, use that instead."""
    if force is not None:
        return int(bool(force))
    return random.randint(0, 1)


def make_entry_id(title: str) -> str:
    """Slug-ify title for use as an entry id."""
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    return slug[:40]


def make_node_slug(base_id: str, depth: int) -> str:
    ts = time.strftime('%Y%m%d-%H%M%S')
    return f'node-{base_id[:16]}-d{depth}-{ts}'


def branch_resolve(
    *,
    entry_id: str,
    title: str,
    teaser: str,
    fragment_href: str,
    posted_date: str,
    timestamp: str,
    body_html: str,
    image_web_path: str,
    image_source: str,
    target_page: Path,
    depth: int,
    depth_cap: int,
    convergence_threshold: int,
    no_convergence: bool,
    force_roll: int | None,
    force_orientation: str | None,
    force_insertion_index: int | None,
    links_note: str,
    branch_log: dict,
    summary: list,
    is_root: bool = True,
    # orientation is rolled once at root and propagated down
    orientation: str | None = None,
) -> None:
    """
    Recursively resolve branch decisions and write pages.

    At each call:
      - Roll 0/1 (or use depth_cap override)
      - Roll orientation (vertical|horizontal) — only at root, then propagated
      - Roll insertion index — once per call, stored in log
      - Roll=1: insert rich card inline on target_page
      - Roll=0:
          v3 CONVERGENCE CHECK FIRST: scan for a contextually relevant existing
          page. If found (score >= convergence_threshold), insert a lean link
          card pointing to it and STOP (no new pages created, no recursion).
          If not found: insert link card on target_page, create destination page,
          recurse into destination (original v2 behaviour).

    All rolls are stored in branch_log for reproducibility.

    DEPTH-AWARE IMAGE RULE (v4):
      image_web_path from incoming/ is only used at depth=0 (surface/front-page).
      At depth>0, if image_source=='incoming', the image is suppressed and a
      warning is emitted.  Only library assets may appear on deeper pages.
    """
    # Apply depth-based media source guard before any HTML is written
    effective_image = image_allowed_at_depth(image_web_path, image_source, depth)
    # At cap → force inline
    effective_roll = 1 if depth >= depth_cap else roll(force_roll if is_root else None)

    # Orientation: roll once at root, propagate to recursions
    if orientation is None:
        orientation = roll_orientation(force_orientation if is_root else None)

    # Count existing blocks on this page to determine insertion index range
    n_existing = count_cascade_blocks(target_page)
    ins_idx = roll_insertion_index(
        n_existing,
        force_insertion_index if is_root else None,
    )

    cascade_pos = pick_cascade_pos(target_page)

    log_record: dict = {
        'entry_id':        entry_id,
        'title':           title,
        'depth':           depth,
        'roll':            effective_roll,
        'orientation':     orientation,
        'insertion_index': ins_idx,
        'n_existing_at_publish': n_existing,
        'target_page':    str(target_page.relative_to(REPO_ROOT)),
        'timestamp_utc':  time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'posted_date':    posted_date,
        'image_source':   image_source,
        'image_blocked':  (image_source == 'incoming' and depth > 0),
        'image_web_path': effective_image,   # actual image used on card (empty = no media shown)
    }

    if effective_roll == 1:
        # ── INLINE ──────────────────────────────────────────────────────────
        card = make_rich_card(
            entry_id=entry_id,
            title=title,
            teaser=teaser,
            fragment_href=fragment_href,
            posted_date=posted_date,
            timestamp=timestamp,
            body_html=body_html,
            image_web_path=effective_image,   # v4: depth-guarded
            cascade_pos=cascade_pos,
            depth=depth,
            roll_seed=effective_roll,
            orientation=orientation,
        )
        insert_cascade_card(target_page, card, insertion_index=ins_idx)

        frag_label = Path(fragment_href).stem if fragment_href else entry_id
        insert_links_entry(target_page, fragment_href or '#', frag_label, links_note)

        log_record['action'] = 'inline'
        log_record['page']   = str(target_page.relative_to(REPO_ROOT))
        summary.append(
            f'  depth={depth}  INLINE → {target_page.relative_to(REPO_ROOT)}'
            f'  pos={cascade_pos}  orient={orientation}  insert_idx={ins_idx}/{n_existing}'
        )

    else:
        # ── LINK ─────────────────────────────────────────────────────────────
        #
        # v3 CONVERGENCE RULE (hard):
        # Before creating any new page, check whether an existing page is
        # contextually relevant to this post (by motif-word overlap).
        # If yes → link to it and STOP. Branching ends here.
        # If no  → create new destination page and recurse as in v2.
        #
        # This prevents runaway node sprawl and keeps the site web-like.
        # Reused links must be contextually appropriate — not random.

        convergence_result = None
        if not no_convergence:
            exclude_norm = set()
            exclude_norm.add(
                str(target_page.relative_to(REPO_ROOT)).replace('\\', '/')
            )
            if fragment_href:
                exclude_norm.add(fragment_href.lstrip('/').replace('\\', '/'))

            existing_candidates = find_existing_contextual_pages(
                REPO_ROOT, exclude_norm
            )
            convergence_result = select_relevant_existing_page(
                title, teaser, existing_candidates, threshold=convergence_threshold
            )

        if convergence_result is not None:
            # ── CONVERGE: link to existing page, branching ends ──────────────
            conv_page, conv_score = convergence_result
            dest_href = _dest_href_from_target(conv_page, REPO_ROOT, target_page)
            conv_rel  = str(conv_page.relative_to(REPO_ROOT)).replace('\\', '/')

            card = make_link_card(
                entry_id=entry_id,
                title=title,
                teaser=teaser,
                dest_href=dest_href,
                posted_date=posted_date,
                cascade_pos=cascade_pos,
                depth=depth,
                roll_seed=effective_roll,
                is_node=False,
                orientation=orientation,
                converges=True,
                image_web_path=effective_image,   # surface media rule: show if hit
            )
            insert_cascade_card(target_page, card, insertion_index=ins_idx)
            insert_links_entry(
                target_page, dest_href, conv_page.stem, links_note
            )

            log_record['action']             = 'link-existing'
            log_record['dest_page']          = conv_rel
            log_record['dest_type']          = 'existing-content'
            log_record['convergence']        = True
            log_record['convergence_score']  = conv_score
            log_record['convergence_threshold'] = convergence_threshold
            summary.append(
                f'  depth={depth}  LINK-EXISTING → {conv_rel}'
                f'  (score={conv_score}, threshold={convergence_threshold})'
                f'  pos={cascade_pos}  orient={orientation}'
                f'  *** BRANCHING ENDS HERE (convergence) ***'
            )
            # NO RECURSION — branching ends at an existing page

        else:
            # ── CREATE NEW: original v2 link + recurse ───────────────────────
            node_slug = make_node_slug(entry_id, depth)
            NODES_DIR.mkdir(parents=True, exist_ok=True)
            node_path = NODES_DIR / f'{node_slug}.html'

            # Recursive roll for the destination
            dest_roll = roll()
            deeper_depth = depth + 1

            if dest_roll == 1 or deeper_depth >= depth_cap:
                # Destination is a fresh content page
                # v4: deeper pages must never receive incoming/-sourced images
                deeper_image = image_allowed_at_depth(image_web_path, image_source, deeper_depth)
                node_html = make_content_page(
                    node_slug=node_slug,
                    entry_title=title,
                    teaser=teaser,
                    fragment_href=fragment_href,
                    body_html=body_html,
                    image_web_path=deeper_image,   # v4: depth-guarded
                    timestamp=timestamp,
                    posted_date=posted_date,
                    depth=deeper_depth,
                    orientation=orientation,
                )
                dest_type = 'content'
            else:
                # Destination is a junction node (can receive future posts)
                node_html = make_node_page(
                    node_slug=node_slug,
                    entry_title=title,
                    posted_date=posted_date,
                    depth=deeper_depth,
                )
                dest_type = 'node'

            node_path.write_text(node_html, encoding='utf-8')

            # Relative href from target_page to node
            node_href = _dest_href_from_target(node_path, REPO_ROOT, target_page)

            # Insert lean link card on the current target page
            card = make_link_card(
                entry_id=entry_id,
                title=title,
                teaser=teaser,
                dest_href=node_href,
                posted_date=posted_date,
                cascade_pos=cascade_pos,
                depth=depth,
                roll_seed=effective_roll,
                is_node=(dest_type == 'node'),
                orientation=orientation,
                converges=False,
                image_web_path=effective_image,   # surface media rule: show if hit
            )
            insert_cascade_card(target_page, card, insertion_index=ins_idx)
            insert_links_entry(target_page, node_href, node_slug, links_note)

            log_record['action']    = 'link'
            log_record['dest_page'] = str(node_path.relative_to(REPO_ROOT)).replace('\\', '/')
            log_record['dest_type'] = dest_type
            log_record['dest_roll'] = dest_roll
            summary.append(
                f'  depth={depth}  LINK  → nodes/{node_slug}.html  ({dest_type})'
                f'  pos={cascade_pos}  orient={orientation}  insert_idx={ins_idx}/{n_existing}'
            )

            # If destination is a node (junction), recursively plant the content there
            if dest_type == 'node':
                branch_resolve(
                    entry_id=entry_id,
                    title=title,
                    teaser=teaser,
                    fragment_href=fragment_href,
                    posted_date=posted_date,
                    timestamp=timestamp,
                    body_html=body_html,
                    image_web_path=image_web_path,  # original path passed; guard re-applied inside
                    image_source=image_source,       # v4: propagate source label for guard
                    target_page=node_path,
                    depth=deeper_depth,
                    depth_cap=depth_cap,
                    convergence_threshold=convergence_threshold,
                    no_convergence=no_convergence,
                    force_roll=None,         # only override at root
                    force_orientation=None,
                    force_insertion_index=None,
                    links_note=links_note,
                    branch_log=branch_log,
                    summary=summary,
                    is_root=False,
                    orientation=orientation, # propagate from root roll
                )

    log_entry(branch_log, log_record)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description='Branching publish engine for bloodinthewire (v5).',
    )
    p.add_argument('--title',                required=True,  help='Entry title')
    p.add_argument('--teaser',               required=True,  help='One-line teaser')
    p.add_argument('--posted-date',          required=True,  help='YYYY-MM-DD')
    p.add_argument('--fragment-href',        default='',     help='Relative href to fragment page')
    p.add_argument('--timestamp',            default='',     help='HH:MM timestamp or omit')
    p.add_argument('--body-file',            default='',     help='Path to HTML body fragment file')
    p.add_argument('--image-web-path',       default='',     help='Relative path to web-ready image')
    p.add_argument('--image-source',         default='library',
                                             choices=['incoming', 'library', ''],
                                             help=(
                                                 'Origin of the image asset. '
                                                 '"incoming" = user drop (highest priority at surface, '
                                                 'BLOCKED on all deeper pages). '
                                                 '"library" = vetted library asset (allowed at all depths). '
                                                 'Default: library.'
                                             ))
    p.add_argument('--target-page',          default='',     help='Target page (default: index.html)')
    p.add_argument('--depth-cap',            type=int, default=DEPTH_CAP_DEFAULT)
    p.add_argument('--convergence-threshold', type=int, default=CONVERGENCE_THRESHOLD_DEFAULT,
                                             help=(
                                                 'Min shared motif-word count for existing-page reuse. '
                                                 'Default=1 (any shared keyword qualifies). '
                                                 'Higher = stricter matching required.'
                                             ))
    p.add_argument('--no-convergence',       action='store_true',
                                             help='Disable contextual reuse check (testing/forced-growth only).')
    p.add_argument('--force-roll',           type=int, default=None,
                                             help='Force branch roll 0|1 (testing only)')
    p.add_argument('--force-orientation',    default=None,
                                             choices=['vertical', 'horizontal'],
                                             help='Force orientation (testing only)')
    p.add_argument('--force-insertion-index', type=int, default=None,
                                             help='Force insertion index (testing only)')
    p.add_argument('--links-note',           default='new entry')

    args = p.parse_args()

    # Validate fragment href before doing any file I/O
    validate_fragment_href(args.fragment_href)

    # Resolve paths
    target_page = Path(args.target_page) if args.target_page else INDEX_HTML
    if not target_page.is_absolute():
        target_page = REPO_ROOT / target_page
    if not target_page.exists():
        print(f'ERROR: target page not found: {target_page}', file=sys.stderr)
        return 1

    body_html = ''
    if args.body_file:
        bp = Path(args.body_file)
        if not bp.is_absolute():
            bp = REPO_ROOT / bp
        if bp.exists():
            body_html = bp.read_text(encoding='utf-8').strip()
        else:
            print(f'WARNING: body file not found: {bp}', file=sys.stderr)

    # Normalise image_source: treat empty as 'library'
    image_source = args.image_source if args.image_source else 'library'

    entry_id   = make_entry_id(args.title)
    branch_log = load_branch_log()
    summary: list = []

    branch_resolve(
        entry_id=entry_id,
        title=args.title,
        teaser=args.teaser,
        fragment_href=args.fragment_href,
        posted_date=args.posted_date,
        timestamp=args.timestamp,
        body_html=body_html,
        image_web_path=args.image_web_path,
        image_source=image_source,           # v4: track origin for depth guard
        target_page=target_page,
        depth=0,
        depth_cap=args.depth_cap,
        convergence_threshold=args.convergence_threshold,
        no_convergence=args.no_convergence,
        force_roll=args.force_roll,
        force_orientation=args.force_orientation,
        force_insertion_index=args.force_insertion_index,
        links_note=args.links_note,
        branch_log=branch_log,
        summary=summary,
        is_root=True,
        orientation=None,  # let it roll fresh
    )

    save_branch_log(branch_log)

    # Pull the orientation and insertion from the last log entry
    last = branch_log['entries'][-1]
    orient  = last.get('orientation', 'unknown')
    ins_idx = last.get('insertion_index', 'unknown')
    n_exist = last.get('n_existing_at_publish', 'unknown')
    action  = last.get('action', 'unknown')
    img_src = last.get('image_source', 'unknown')
    img_blk = last.get('image_blocked', False)

    print()
    print('branch_publish v4 complete')
    print('=' * 56)
    print(f'  entry:                {args.title}')
    print(f'  depth_cap:            {args.depth_cap}')
    print(f'  orientation:          {orient}')
    print(f'  insertion_index:      {ins_idx}  (out of {n_exist} existing blocks)')
    print(f'  image_source:         {img_src}{"  [BLOCKED at this depth]" if img_blk else ""}')
    print(f'  convergence:          {"disabled (--no-convergence)" if args.no_convergence else f"threshold={args.convergence_threshold}"}')
    print(f'  branch log:           {BRANCH_LOG.relative_to(REPO_ROOT)}')
    print()
    print('  branch path:')
    for line in summary:
        print(line)
    print()
    if action == 'link-existing':
        print('  NOTE: link pointed to an existing contextual page.')
        print('        Branching ended at convergence — no new nodes created.')
    print('  staged: working tree changed. do not git push until reviewed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
