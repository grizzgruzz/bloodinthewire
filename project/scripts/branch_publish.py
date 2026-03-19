#!/usr/bin/env python3
"""
branch_publish.py  v8
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
    (Superseded by v6 zone roll: top/middle/bottom.)

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

v6 additions
------------
  • THREE-ZONE PLACEMENT ROLL for successful publishes:
      Each insertion now rolls one of {top, middle, bottom} and maps to a
      deterministic insertion_index for that zone.
      - top    -> insertion_index=0
      - middle -> insertion_index=floor(n_existing/2)
      - bottom -> insertion_index=n_existing
  • The chosen insertion_zone is stored in branch-log.json for auditability.
  • This replaces the old uniform random insertion_index across all positions.

v8 additions
------------
  • ANTI-BACKLINK RULE (hard): when roll=0 (link-out) and convergence is
    attempted, candidate pages that appear in the current branch's ancestry
    chain are excluded from selection.  This prevents:
      - Immediate predecessor loop-backs (A→B→A)
      - Ping-pong between two nodes (A↔B repeated)
      - Any ancestor in the current branch being reused as a pivot target
    Blocked candidates are logged to stderr so the audit trail is clear.
    The ancestry chain is threaded through recursive calls and maintained
    newest-first; each level adds its own target_page before passing down.
  • IMAGE-PATH SANITY CHECK: validate_image_web_path() is called at the
    top of branch_resolve before any HTML is written.  If the image path
    does not resolve to an existing file, it is suppressed to empty string
    so no dead <img> tag is ever rendered.  Suppression is logged to stderr.

v7 additions
------------
  • DEPTH_CAP_DEFAULT raised to 30 (from 5) to prevent runaway depth while
    allowing genuinely deep trees.
  • DEPTH PROGRESSION WEIGHTING (hard rule) when hyperlink=yes at depth>=1:
      - depth 1 decision (first deeper): 70% new page / 30% link-out existing
      - depth 2 decision (next):         60% new page / 40% link-out existing
      - depth >=3 (all deeper levels):   50% / 50%
    Previously, link-out (convergence) was purely relevance-score-driven.
    Now, at depth>=1, a probability gate controls whether convergence is even
    attempted: if the gate says "new page", skip convergence and always create
    a fresh page.  The gate roll is logged as 'depth_gate_roll'.
  • STATIC NAVIGATION SEMANTICS added to generated node and content pages:
      - Junction nodes: header now shows a "navigate up" link to parent page
        and footer always includes parent + home links.
      - Content pages: same — header stamp includes parent link, footer has
        parent and home affordances.
      - Terminal branches (content pages, convergence targets) get sensible
        back-navigation in their footers (already present for content pages;
        now made explicit for node pages too).
  • Node/content page generators accept parent_href parameter to wire up
    actual up-links rather than always defaulting to index.html.
  • All changes are backward-compatible with existing pages; old pages are
    not retroactively rewritten.

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
      [--depth-cap 30]                      # max branching depth (default 30)
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
  - Insertion zone is rolled once (top/middle/bottom) and mapped to a
    deterministic insertion_index. Both are stored for reproducibility.
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

DEPTH_CAP_DEFAULT           = 30
CONVERGENCE_THRESHOLD_DEFAULT = 1   # min shared motif words to reuse existing page

# Depth progression weighting (v7):
# When hyperlink=yes (roll=0) at depth>=1, a probability gate controls whether
# convergence (link-out to existing page) is even attempted.  If the gate says
# "new page", convergence is skipped and a fresh page is always created.
#
# P(new page) by depth:
#   depth 1 (first deeper decision): 70% new page / 30% allow convergence
#   depth 2 (next):                  60% new page / 40% allow convergence
#   depth >=3 (all deeper levels):   50% / 50%
#
# At depth 0 (surface), this gate is NOT applied — convergence runs normally.
def _depth_new_page_probability(depth: int) -> float:
    """Return P(new page) for the depth progression weighting gate."""
    if depth <= 0:
        return 0.0   # not used at surface; convergence always runs normally
    if depth == 1:
        return 0.70
    if depth == 2:
        return 0.60
    return 0.50
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


def roll_insertion_index(n_existing: int, force: int | None = None) -> tuple[int, str]:
    """
    Roll insertion placement as one of three zones: top | middle | bottom.

    Returns: (insertion_index, insertion_zone)
      - top    -> 0
      - middle -> floor(n_existing / 2)
      - bottom -> n_existing

    If force is provided, clamps and uses the explicit insertion index and
    marks insertion_zone='forced'.
    """
    if force is not None:
        forced = max(0, min(int(force), n_existing))
        return forced, 'forced'

    zone = random.choice(['top', 'middle', 'bottom'])
    if n_existing <= 0:
        # All zones map to index 0 on an empty page; zone still rolled for auditability.
        return 0, zone

    if zone == 'top':
        return 0, zone
    if zone == 'middle':
        return n_existing // 2, zone
    return n_existing, zone


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
    ancestry_chain: list | None = None,
):
    """
    Select the most contextually relevant existing page for a new post.

    Scores each candidate by the number of shared motif words between the new
    post (title + teaser) and the candidate page's motif word set.

    Returns the highest-scoring (page_path, score) tuple if score >= threshold.
    Returns None if no candidate meets the threshold.

    Ties broken by candidate filename (alphabetical, deterministic — not random).
    Relevance preference: shared motif / tone / evidence class / semantic fit.

    ANTI-BACKLINK RULE (v8):
    ancestry_chain is a list of normalised page paths (relative to repo root)
    representing the current branch's ancestry, newest-first.  Any candidate
    whose path matches an ancestor is excluded from convergence selection.
    This prevents pivot loops (A→B→A, A→B→C→B, etc.).
    Blocked candidates are logged to stderr for auditability.
    """
    ancestry_norm: set = set()
    if ancestry_chain:
        for a in ancestry_chain:
            ancestry_norm.add(str(a).replace('\\', '/').lstrip('/'))

    post_words = _motif_words(title + ' ' + teaser)
    if not post_words:
        return None

    scored = []
    for page, page_words in candidates:
        page_norm = str(page).replace('\\', '/')
        # Strip repo root prefix for comparison
        try:
            page_rel = str(page.relative_to(REPO_ROOT)).replace('\\', '/')
        except ValueError:
            page_rel = page_norm

        # Anti-backlink guard: skip any candidate that is in the ancestry chain
        if page_rel in ancestry_norm:
            print(
                f'[branch_publish] ANTI-BACKLINK: candidate "{page_rel}" blocked '
                f'— it is an ancestor of the current branch. Skipping.',
                file=sys.stderr,
            )
            continue

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
    parent_href: str = '../index.html',
) -> str:
    """Generate a node junction page shell.

    parent_href: relative link back to the page that spawned this node.
                 Defaults to '../index.html' (root).  Used to wire up
                 static up-navigation (House-of-Leaves feel).

    The node is marked with data-node-status="pending" until content arrives
    via a deeper branch roll. This ensures no node shell is silently orphaned:
    the status attribute is machine-readable for consistency checks, and the
    visible notice tells readers the thread continues.
    """
    # Navigation: up goes to parent; home always goes to entrypoint
    parent_label = 'return to parent' if parent_href != '../index.html' else 'return to entrypoint'
    nav_up = f'<p class="nav-up"><a href="{_html.escape(parent_href)}">[up] {parent_label}</a></p>'
    nav_home = '' if parent_href == '../index.html' else '<p class="nav-home"><a href="../index.html">[home] return to entrypoint</a></p>'
    # Separator: only render nav_home block if non-empty (don't double up index link)
    footer_home = f'\n      {nav_home}' if nav_home else ''

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
      {nav_up}
      <h2>{_html.escape(node_slug)}</h2>
      <p class="sub">branch junction // follow the threads</p>
      <hr />
    </header>

    <div class="node-shell" data-node-status="pending">
      <p class="node-label">NODE :: {_html.escape(node_slug)} // generated: {posted_date}</p>
      <!-- CASCADE:START -->
      <!-- future posts may land here via deeper branch rolls -->
      <!-- node pending: thread continues — documentation in progress -->
      <!-- CASCADE:END -->
      <p class="tiny-note node-pending-notice">// thread continues — documentation in progress</p>
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
      {nav_up}{footer_home}
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
    parent_href: str = '../index.html',
) -> str:
    """Generate a fresh content page (roll=1 at deeper depth).

    parent_href: relative link back to the page that spawned this content node.
                 Used to wire up static up-navigation (House-of-Leaves feel).
    """
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

    # Navigation: up to parent; home only if parent isn't already index
    parent_label = 'return to parent' if parent_href != '../index.html' else 'return to entrypoint'
    nav_up = f'<p class="nav-up"><a href="{_html.escape(parent_href)}">[up] {parent_label}</a></p>'
    nav_home = '' if parent_href == '../index.html' else '<p class="nav-home"><a href="../index.html">[home] return to entrypoint</a></p>'
    footer_home = f'\n      {nav_home}' if nav_home else ''

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
      {nav_up}
      <h2>{_html.escape(entry_title)}</h2>
      <hr />
    </header>

{ts_block}{body_block}{img_block}{frag_link}
    <footer>
      <hr />
      {nav_up}{footer_home}
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


# ── Image path sanity check ───────────────────────────────────────────────────

def validate_image_web_path(image_web_path: str) -> str:
    """
    Validate and normalise an image_web_path to a web-relative form.

    MEDIA PATH HARDENING (v8+):
      - If the path is an absolute filesystem path, attempt to convert it to
        a web-relative path by stripping the REPO_ROOT prefix.
      - If the absolute path cannot be made relative to REPO_ROOT, reject it
        entirely (return empty string) so no broken <img> tag is rendered.
      - Only web-relative paths (no leading '/') are returned.
      - If the resolved file does not exist, suppress and log to stderr.

    Returns a normalised web-relative path if valid, empty string if not.
    """
    if not image_web_path:
        return image_web_path

    candidate = Path(image_web_path)

    # Sanitise absolute filesystem paths (e.g. /home/gruzz/bloodinthewire/...)
    if candidate.is_absolute():
        try:
            rel = candidate.relative_to(REPO_ROOT)
            normalised = str(rel).replace('\\', '/')
            print(
                f'[branch_publish] MEDIA-GUARD: absolute image path "{image_web_path}" '
                f'normalised to web-relative "{normalised}".',
                file=sys.stderr,
            )
            image_web_path = normalised
            candidate = REPO_ROOT / image_web_path
        except ValueError:
            print(
                f'[branch_publish] MEDIA-GUARD: absolute image path "{image_web_path}" '
                f'is outside REPO_ROOT and cannot be made web-relative. Suppressing.',
                file=sys.stderr,
            )
            return ''
    else:
        candidate = REPO_ROOT / image_web_path

    if candidate.exists():
        return image_web_path

    print(
        f'[branch_publish] IMAGE-GUARD: image path "{image_web_path}" does not '
        f'resolve to an existing file. Suppressing to avoid dead <img> tag.',
        file=sys.stderr,
    )
    return ''


# ── Image anti-reuse policy ───────────────────────────────────────────────────
#
# v9 IMAGE ANTI-REUSE POLICY (hard rules for depth >= 1):
#
# 1. Branch-local guard:
#    Within a single branch chain, depth>=1 must not reuse any ancestor image
#    already shown in that chain (especially the depth=0 surface image).
#
# 2. Surface-to-deep guard:
#    For depth>=1, avoid images that are currently used on recent homepage
#    entries (last ANTI_REUSE_COOLDOWN_DEFAULT depth=0 branch-log entries).
#
# 3. Selection: for depth>=1, prefer fresh library assets not in deny-set.
#    If no eligible image remains, degrade to text-only (do not force reuse).
#
# Logging: any rejected candidate is logged to stderr with the reason code.

import re as _re  # needed by anti-reuse helpers (stdlib, safe duplicate guard)
import shutil as _shutil  # safe duplicate guard

ANTI_REUSE_COOLDOWN_DEFAULT = 10  # last N surface images form the deny set


def _library_stem_from_web_basename(web_basename: str) -> str:
    """
    Extract the original library stem from a web-ready asset basename.

    Web asset names are formatted as ``{original_stem}_{YYYYMMDD}-{HHMMSS}{ext}``.
    This function strips the timestamp suffix to recover the original library
    filename stem, enabling cross-format comparison between web/ basenames
    and library/ filenames.
    """
    stem = Path(web_basename).stem   # e.g. 'Basement_20260318-170223'
    m = _re.match(r'^(.+)_(\d{8}-\d{6})$', stem)
    if m:
        return m.group(1)
    return stem


def _recent_surface_image_basenames(branch_log: dict, cooldown: int = ANTI_REUSE_COOLDOWN_DEFAULT) -> frozenset:
    """
    Return a frozenset of web image basenames from the most recent `cooldown`
    depth=0 (surface) entries in branch_log that have a non-empty image_web_path.

    Used to build the surface-to-deep deny set: depth>=1 candidates whose
    basename appears here are considered "recently seen at the surface" and
    are eligible for rejection by the anti-reuse guard.
    """
    surface_entries = [
        e for e in branch_log.get('entries', [])
        if e.get('depth') == 0 and e.get('image_web_path')
    ]
    recent = surface_entries[-cooldown:]
    return frozenset(Path(e['image_web_path']).name for e in recent)


def _pick_fresh_library_image(deny_basenames: frozenset) -> str:
    """
    Select a fresh library image whose original stem is NOT represented by
    any basename in deny_basenames.

    Archives a copy to assets/published/ and writes a metadata-stripped
    copy to assets/web/.  Returns a web-relative path string, or empty
    string if no eligible candidate exists.

    This is the fallback path invoked when the proposed deep-level image
    is rejected by the anti-reuse guard (branch-local or surface-to-deep).
    """
    assets_dir    = REPO_ROOT / 'project' / 'assets'
    library_dir   = assets_dir / 'library'
    published_dir = assets_dir / 'published'
    web_dir       = assets_dir / 'web'
    accepted_ext  = frozenset({'.jpg', '.jpeg', '.png'})

    if not library_dir.is_dir():
        return ''

    # Build set of original stems that are already "used" (deny_basenames are web basenames)
    deny_stems = {_library_stem_from_web_basename(b) for b in deny_basenames}

    candidates = sorted(
        f for f in library_dir.iterdir()
        if f.is_file()
        and f.suffix.lower() in accepted_ext
        and f.stem not in deny_stems
    )

    if not candidates:
        return ''

    chosen = candidates[0]

    # Archive copy to published/
    published_dir.mkdir(parents=True, exist_ok=True)
    web_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime('%Y%m%d-%H%M%S')
    published_name = f'{chosen.stem}__{ts}{chosen.suffix}'
    published_path = published_dir / published_name
    _shutil.copy2(chosen, published_path)

    # Strip metadata to web/ (Pillow preferred, plain copy as fallback)
    web_name = f'{chosen.stem}_{ts}{chosen.suffix}'
    web_path = web_dir / web_name
    stripped = False
    try:
        from PIL import Image as _PILImage  # type: ignore[import]
        ext = chosen.suffix.lower()
        with _PILImage.open(published_path) as img:
            if ext in ('.jpg', '.jpeg'):
                if img.mode in ('RGBA', 'P', 'LA'):
                    img = img.convert('RGB')
                img.save(web_path, format='JPEG', quality=92, optimize=True,
                         exif=b'', icc_profile=None)
            else:
                data = img.tobytes()
                clean = _PILImage.frombytes(img.mode, img.size, data)
                clean.save(web_path, format='PNG', optimize=True)
        stripped = True
    except Exception:
        pass
    if not stripped:
        _shutil.copy2(published_path, web_path)

    # Return web-relative path (forward-slash, relative to REPO_ROOT)
    web_rel = str(web_path.relative_to(REPO_ROOT)).replace('\\', '/')
    return web_rel


def _anti_reuse_deep_image(
    image_web_path: str,
    depth: int,
    ancestor_images: frozenset,
    branch_log: dict,
    cooldown: int = ANTI_REUSE_COOLDOWN_DEFAULT,
) -> str:
    """
    IMAGE ANTI-REUSE POLICY — v9 guard applied at depth >= 1.

    Evaluates two guards in order:

    1. Branch-local guard:
       Rejects the candidate if its web basename is already present in
       ancestor_images (images used at shallower depths in this branch chain).
       This prevents the same library asset from appearing on both the surface
       card (depth=0) and any deeper content page in the same branch.

    2. Surface-to-deep guard:
       Rejects the candidate if its web basename appears in the most recent
       `cooldown` surface (depth=0) branch-log entries.  This prevents a
       library image used at the surface in a PREVIOUS branch from reappearing
       on deep pages of the current branch.

    On rejection: attempts to pick a fresh library image not in the deny set
    via _pick_fresh_library_image().  If no fresh library image is available,
    degrades to text-only (returns empty string) — never forces reuse.

    All rejections are logged to stderr with the reason code.

    Parameters
    ----------
    image_web_path  : candidate image (web-relative path or empty string)
    depth           : current branch depth (must be >= 1 to trigger)
    ancestor_images : frozenset of web basenames used by ancestor depths
                      in the current branch chain (tracks branch-local reuse)
    branch_log      : the live branch-log dict (for surface-to-deep lookup)
    cooldown        : number of recent surface entries to check (default 10)

    Returns
    -------
    Effective image path to use — either the original (if not blocked),
    a fresh library path (if blocked and a fresh asset was found), or
    empty string (if blocked and no fresh asset available).
    """
    if depth < 1 or not image_web_path:
        return image_web_path

    img_basename  = Path(image_web_path).name
    surface_deny  = _recent_surface_image_basenames(branch_log, cooldown)
    full_deny     = ancestor_images | surface_deny

    if img_basename not in full_deny:
        return image_web_path  # not in deny set — allowed

    # Identify which guard(s) triggered
    guard_reasons = []
    if img_basename in ancestor_images:
        guard_reasons.append('branch-local (ancestor reuse)')
    if img_basename in surface_deny:
        guard_reasons.append(f'surface-to-deep cooldown (last {cooldown} surface images)')
    reason_str = ' + '.join(guard_reasons)

    print(
        f'[branch_publish] IMAGE-ANTI-REUSE: depth={depth} — candidate '
        f'"{img_basename}" REJECTED ({reason_str}). '
        f'deny_set_size={len(full_deny)}. Seeking fresh library asset.',
        file=sys.stderr,
    )

    # Try to pick a fresh library asset not in the deny set
    fresh = _pick_fresh_library_image(full_deny)
    if fresh:
        fresh_basename = Path(fresh).name
        print(
            f'[branch_publish] IMAGE-ANTI-REUSE: selected fresh library image '
            f'"{fresh_basename}" as depth={depth} replacement.',
            file=sys.stderr,
        )
        return fresh

    # No fresh library image available — degrade to text-only
    print(
        f'[branch_publish] IMAGE-ANTI-REUSE: no fresh library image available '
        f'(all {len(full_deny)} deny-set entries exhaust library). '
        f'Degrading to text-only at depth={depth}.',
        file=sys.stderr,
    )
    return ''


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
    # parent_href for up-navigation in generated pages (v7)
    parent_href: str = '../index.html',
    # ancestry_chain: list of normalised page paths (relative to repo root),
    # newest-first.  Used by anti-backlink guard (v8) to prevent pivot loops.
    ancestry_chain: list | None = None,
    # ancestor_images: frozenset of web basenames already shown in this branch chain.
    # Used by v9 image anti-reuse guard to block same-asset reuse at depth>=1.
    ancestor_images: frozenset | None = None,
) -> None:
    """
    Recursively resolve branch decisions and write pages.

    At each call:
      - Roll 0/1 (or use depth_cap override)
      - Roll orientation (vertical|horizontal) — only at root, then propagated
      - Roll insertion index — once per call, stored in log
      - Roll=1: insert rich card inline on target_page
      - Roll=0:
          v7 DEPTH PROGRESSION GATE (depth>=1 only):
            A probability gate controls whether convergence is even attempted:
            depth=1 → 70% skip-convergence/new-page, 30% allow convergence
            depth=2 → 60% skip, 40% allow
            depth>=3 → 50% skip, 50% allow
            At depth=0 (surface), gate is not applied; original behaviour preserved.
          v3 CONVERGENCE CHECK (if gate allows):
            Scan for a contextually relevant existing page. If found
            (score >= convergence_threshold), insert a lean link card pointing
            to it and STOP (no new pages created, no recursion).
          If gate blocked convergence OR no relevant page found:
            Insert link card on target_page, create destination page,
            recurse into destination.

    All rolls are stored in branch_log for reproducibility.

    DEPTH-AWARE IMAGE RULE (v4):
      image_web_path from incoming/ is only used at depth=0 (surface/front-page).
      At depth>0, if image_source=='incoming', the image is suppressed and a
      warning is emitted.  Only library assets may appear on deeper pages.

    STATIC NAVIGATION (v7):
      parent_href is threaded into generated node/content pages so up-links
      always point to the actual parent page (not just index.html).
    """
    # Initialise ancestry chain (v8 anti-backlink guard)
    if ancestry_chain is None:
        ancestry_chain = []
    # Record this target_page in ancestry (normalised, relative to repo root)
    try:
        current_page_rel = str(target_page.relative_to(REPO_ROOT)).replace('\\', '/')
    except ValueError:
        current_page_rel = str(target_page).replace('\\', '/')
    updated_ancestry = [current_page_rel] + ancestry_chain

    # Initialise ancestor_images tracking (v9 image anti-reuse guard)
    if ancestor_images is None:
        ancestor_images = frozenset()

    # Validate image path — suppress dead refs before any HTML is written (v8)
    image_web_path = validate_image_web_path(image_web_path)

    # Apply depth-based media source guard before any HTML is written
    effective_image = image_allowed_at_depth(image_web_path, image_source, depth)

    # Apply image anti-reuse guard (v9) — depth>=1 only.
    # Rejects any candidate that is already in the ancestor chain or
    # among recent surface entries; falls back to fresh library asset
    # or text-only. depth=0 is never subject to this guard.
    if depth >= 1 and effective_image:
        effective_image = _anti_reuse_deep_image(
            image_web_path=effective_image,
            depth=depth,
            ancestor_images=ancestor_images,
            branch_log=branch_log,
        )
    # At cap → force inline
    effective_roll = 1 if depth >= depth_cap else roll(force_roll if is_root else None)

    # Orientation: roll once at root, propagate to recursions
    if orientation is None:
        orientation = roll_orientation(force_orientation if is_root else None)

    # Count existing blocks on this page to determine insertion index range
    n_existing = count_cascade_blocks(target_page)
    ins_idx, ins_zone = roll_insertion_index(
        n_existing,
        force_insertion_index if is_root else None,
    )

    cascade_pos = pick_cascade_pos(target_page)

    # Build updated ancestor_images set for child calls (v9 anti-reuse guard).
    # Include the effective_image used at this depth so deeper recursions know
    # what images have already been shown in this branch chain.
    if effective_image:
        updated_ancestor_images = ancestor_images | frozenset({Path(effective_image).name})
    else:
        updated_ancestor_images = ancestor_images

    log_record: dict = {
        'entry_id':        entry_id,
        'title':           title,
        'depth':           depth,
        'roll':            effective_roll,
        'orientation':     orientation,
        'insertion_index': ins_idx,
        'insertion_zone':  ins_zone,
        'n_existing_at_publish': n_existing,
        'target_page':    str(target_page.relative_to(REPO_ROOT)),
        'timestamp_utc':  time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'posted_date':    posted_date,
        'image_source':   image_source,
        'image_blocked':  (image_source == 'incoming' and depth > 0),
        'image_web_path': effective_image,   # actual image used on card (empty = no media shown)
        'image_anti_reuse_applied': (depth >= 1 and bool(image_web_path) and effective_image != image_web_path),
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
            f'  pos={cascade_pos}  orient={orientation}  insert={ins_zone}@{ins_idx}/{n_existing}'
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
        #
        # v7 DEPTH PROGRESSION GATE (depth>=1 only):
        # At depth>=1, a probability gate is applied BEFORE the convergence check.
        # If the gate says "new page", skip convergence entirely and always
        # create a fresh page.  The gate probability scales with depth:
        #   depth=1: 70% new page / 30% allow convergence
        #   depth=2: 60% new page / 40% allow convergence
        #   depth>=3: 50% / 50%
        # At depth=0 (surface), gate is not applied (original behaviour).

        # Apply depth progression gate
        depth_gate_blocked = False
        depth_gate_roll    = None
        if depth >= 1 and not no_convergence:
            p_new = _depth_new_page_probability(depth)
            depth_gate_roll = random.random()
            if depth_gate_roll < p_new:
                depth_gate_blocked = True  # skip convergence → always new page

        convergence_result = None
        if not no_convergence and not depth_gate_blocked:
            exclude_norm = set()
            exclude_norm.add(
                str(target_page.relative_to(REPO_ROOT)).replace('\\', '/')
            )
            if fragment_href:
                exclude_norm.add(fragment_href.lstrip('/').replace('\\', '/'))

            existing_candidates = find_existing_contextual_pages(
                REPO_ROOT, exclude_norm
            )

            # DEPTH-0 POLICY (hard rule): at the surface (depth==0), convergence
            # links must NEVER point to nodes/* pages — only fragments/* and root
            # .html pages are allowed.  Filter out any nodes/* candidates here.
            if depth == 0:
                filtered_candidates = []
                for cand_page, cand_words in existing_candidates:
                    try:
                        cand_rel = str(cand_page.relative_to(REPO_ROOT)).replace('\\', '/')
                    except ValueError:
                        cand_rel = str(cand_page).replace('\\', '/')
                    if cand_rel.startswith('nodes/'):
                        print(
                            f'[branch_publish] DEPTH0-POLICY: candidate "{cand_rel}" '
                            f'blocked at depth=0 — nodes/* links are not allowed at surface.',
                            file=sys.stderr,
                        )
                    else:
                        filtered_candidates.append((cand_page, cand_words))
                existing_candidates = filtered_candidates

            convergence_result = select_relevant_existing_page(
                title, teaser, existing_candidates,
                threshold=convergence_threshold,
                ancestry_chain=updated_ancestry,   # v8: anti-backlink guard
            )

        # Record gate result for auditability
        if depth_gate_roll is not None:
            log_record['depth_gate_p_new'] = round(_depth_new_page_probability(depth), 2)
            log_record['depth_gate_roll']  = round(depth_gate_roll, 4)
            log_record['depth_gate_blocked_convergence'] = depth_gate_blocked

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
                f'  pos={cascade_pos}  orient={orientation}  insert={ins_zone}@{ins_idx}/{n_existing}'
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

            # Compute the up-link for the new page: points back to current target_page
            # (relative path from nodes/ directory where all generated pages live)
            new_page_parent_href = _dest_href_from_target(target_page, REPO_ROOT, node_path)

            if dest_roll == 1 or deeper_depth >= depth_cap:
                # Destination is a fresh content page
                # v4: deeper pages must never receive incoming/-sourced images
                deeper_image = image_allowed_at_depth(image_web_path, image_source, deeper_depth)
                # v9: apply anti-reuse guard to content page image
                if deeper_depth >= 1 and deeper_image:
                    deeper_image = _anti_reuse_deep_image(
                        image_web_path=deeper_image,
                        depth=deeper_depth,
                        ancestor_images=updated_ancestor_images,
                        branch_log=branch_log,
                    )

                # Phase 1 inline-link injection (post-generation pass, depth>=1)
                # Inserts exactly ONE obvious inline hyperlink in the body text.
                # Records metadata in log_record for auditability.
                # node_path is the destination page being written; use its
                # parent (node_path) as the page context for href calculation.
                inline_body_html = body_html
                inline_meta: dict = {'link_type': 'none', 'inline_skipped': True}
                if deeper_depth >= 1 and body_html.strip():
                    inline_body_html, inline_meta = inject_inline_links(
                        body_html=body_html,
                        depth=deeper_depth,
                        title=title,
                        teaser=teaser,
                        fragment_href=fragment_href,
                        target_page=node_path,
                        ancestry_chain=updated_ancestry,
                        convergence_threshold=convergence_threshold,
                    )
                log_record.update({
                    'inline_link_type':    inline_meta.get('link_type', 'none'),
                    'inline_anchor_text':  inline_meta.get('inline_anchor_text', ''),
                    'inline_dest_page':    inline_meta.get('inline_dest_page', ''),
                    'inline_dest_href':    inline_meta.get('inline_dest_href', ''),
                    'inline_skipped':      inline_meta.get('inline_skipped', True),
                })

                node_html = make_content_page(
                    node_slug=node_slug,
                    entry_title=title,
                    teaser=teaser,
                    fragment_href=fragment_href,
                    body_html=inline_body_html,   # Phase 1: inline-link injected
                    image_web_path=deeper_image,  # v4: depth-guarded
                    timestamp=timestamp,
                    posted_date=posted_date,
                    depth=deeper_depth,
                    orientation=orientation,
                    parent_href=new_page_parent_href,  # v7: up-navigation
                )
                dest_type = 'content'
            else:
                # Destination is a junction node (can receive future posts)
                node_html = make_node_page(
                    node_slug=node_slug,
                    entry_title=title,
                    posted_date=posted_date,
                    depth=deeper_depth,
                    parent_href=new_page_parent_href,  # v7: up-navigation
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
                f'  pos={cascade_pos}  orient={orientation}  insert={ins_zone}@{ins_idx}/{n_existing}'
            )

            # If destination is a node (junction), recursively plant the content there
            if dest_type == 'node':
                # Children of node_path will also live in nodes/, so their parent href
                # is computed as: from nodes/<child>.html back to nodes/<node_path>.html
                # = ../nodes/<node_path.name>  (up from nodes/ then back into nodes/)
                # This is what _dest_href_from_target returns when both are in nodes/.
                recursive_parent_href = _dest_href_from_target(node_path, REPO_ROOT, node_path)
                # _dest_href_from_target(node_path, REPO_ROOT, node_path) returns '../nodes/<name>'
                # which is correct for a sibling in nodes/ navigating up to this node.
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
                    orientation=orientation,              # propagate from root roll
                    parent_href=recursive_parent_href,    # v7: up-nav for children of this node
                    ancestry_chain=updated_ancestry,      # v8: anti-backlink guard
                    ancestor_images=updated_ancestor_images,  # v9: image anti-reuse guard
                )

    log_entry(branch_log, log_record)


# ── Phase 1: Inline-link injector ────────────────────────────────────────────
#
# PHASE 1 INLINE-LINK BRANCHING
# ==============================
# After text generation and before page write, a post-generation pass inserts
# ONE inline hyperlink inside the body_html of newly generated pages at depth>=1.
#
# Rules:
#   • Phase 1: exactly 1 inline link per eligible page (safe rollout).
#   • Depth < 1 pages are NOT modified (inline links only on deeper content).
#   • Anchor text selection: choose a meaningful multi-word phrase (2-4 words)
#     from the body text, filtered against stop-words. Prefer nouns and
#     descriptive phrases over verbs and fragments.
#   • Destination: use the same motif-word overlap logic as convergence, but
#     with a SEPARATE selection that excludes the current page's own
#     fragment_href and target_page from candidates.
#   • Anti-backlink: never link to an ancestor page in the current branch.
#   • CSS class: wire-inline-link (see styles.css) — high-visibility amber
#     underline with hover treatment. Must remain obvious on dark background.
#   • The injected link is a <a href="..." class="wire-inline-link"> tag
#     inserted around the selected anchor phrase inside a <p> in wire-body.
#   • Metadata is recorded in the branch-log entry as:
#       link_type=inline_word
#       inline_anchor_text=<phrase>
#       inline_dest_page=<dest>
#       inline_dest_href=<href>
#   • If no eligible destination or no eligible anchor phrase is found,
#     the injector skips gracefully — it never forces a broken link.
#
# Styling requirements (HARD, never soften):
#   • colour: #f5c842 (amber) — max contrast on dark background
#   • text-decoration: underline, 2px thick
#   • font-weight: bold
#   • hover/focus: white text, amber background highlight
#   • class: wire-inline-link on every injected anchor
#   • ::after content: ' ↗' (small arrow, scannable)
#
# See styles.css block "INLINE PROSE LINKS (Phase 1 inline-link branching)"
# for full CSS implementation.

# Stop-word list for anchor phrase selection (same set as motif-word filter
# plus additional function words that make bad anchor text)
_ANCHOR_STOP_WORDS = frozenset({
    'this', 'that', 'with', 'from', 'have', 'been', 'will', 'they',
    'were', 'when', 'then', 'than', 'into', 'some', 'what', 'also',
    'more', 'each', 'only', 'over', 'such', 'very', 'just', 'like',
    'here', 'there', 'about', 'after', 'before', 'still', 'again',
    'other', 'first', 'would', 'could', 'their', 'which', 'where',
    'same', 'once', 'back', 'down', 'away', 'does', 'both', 'said',
    'know', 'that', 'have', 'from', 'they', 'been', 'will', 'were',
    'your', 'with', 'what', 'when', 'make', 'like', 'time', 'just',
    'into', 'look', 'come', 'much', 'then', 'well', 'also', 'back',
    'even', 'want', 'give', 'most', 'tell', 'very', 'call', 'need',
    # small words
    'the', 'and', 'but', 'not', 'for', 'are', 'can', 'was', 'has',
    'had', 'him', 'his', 'her', 'she', 'its', 'our', 'you', 'who',
    'got', 'let', 'put', 'see', 'may', 'now', 'too', 'any', 'two',
    'way', 'day', 'get', 'use', 'how', 'new', 'try', 'run', 'old',
})


def _extract_anchor_candidates(body_html: str) -> list:
    """
    Extract candidate anchor phrases from body_html (text inside <p> tags).

    Returns a list of (phrase, paragraph_text, paragraph_index) tuples,
    sorted by candidate quality score (best first).

    Candidate phrases are multi-word sequences (2-4 words) where:
      - At least one word is >= 5 chars and not a stop word
      - The phrase does not start or end with a stop word
      - The phrase is not all-caps abbreviation
      - The phrase occurs inside a paragraph of >= 40 chars (not a stub)
    """
    # Strip all HTML tags to get plain text paragraphs
    p_pattern = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL | re.IGNORECASE)
    paragraphs = []
    for m in p_pattern.finditer(body_html):
        raw = m.group(1)
        plain = re.sub(r'<[^>]+>', '', raw).strip()
        if len(plain) >= 40:
            paragraphs.append((plain, m.start()))

    if not paragraphs:
        return []

    candidates = []
    word_pattern = re.compile(r"[a-zA-Z][a-zA-Z''-]{2,}")

    for para_idx, (para_text, para_start) in enumerate(paragraphs):
        words = word_pattern.findall(para_text)
        if len(words) < 5:
            continue

        # Slide a 3-word window across the paragraph
        for i in range(len(words) - 2):
            phrase_words = words[i:i+3]
            phrase = ' '.join(phrase_words)

            # Reject phrases starting or ending with stop words
            if phrase_words[0].lower() in _ANCHOR_STOP_WORDS:
                continue
            if phrase_words[-1].lower() in _ANCHOR_STOP_WORDS:
                continue

            # Require at least one content word >= 5 chars
            has_content_word = any(
                len(w) >= 5 and w.lower() not in _ANCHOR_STOP_WORDS
                for w in phrase_words
            )
            if not has_content_word:
                continue

            # Prefer phrases from paragraph middle (avoid opener/closing clichés)
            pos_score = 1 if (i > 0 and i < len(words) - 3) else 0

            # Score = sum of content word lengths (longer = more specific)
            length_score = sum(
                len(w) for w in phrase_words
                if w.lower() not in _ANCHOR_STOP_WORDS
            )

            candidates.append((
                -(length_score + pos_score * 3),  # negative for sort ascending = best first
                phrase,
                para_text,
                para_idx,
            ))

    # Sort: best (longest content, middle-of-para) first; deduplicate
    candidates.sort(key=lambda x: x[0])
    seen_phrases = set()
    result = []
    for score, phrase, para, para_idx in candidates:
        norm = phrase.lower()
        if norm not in seen_phrases:
            seen_phrases.add(norm)
            result.append((phrase, para, para_idx))
        if len(result) >= 20:
            break

    return result


def inject_inline_links(
    body_html: str,
    depth: int,
    title: str,
    teaser: str,
    fragment_href: str,
    target_page: Path,
    ancestry_chain: list | None,
    convergence_threshold: int = 1,
) -> tuple:
    """
    Phase 1 inline-link injection.

    Inserts exactly ONE inline <a class="wire-inline-link"> hyperlink inside
    the generated body_html at depth >= 1.

    Returns (modified_body_html, metadata_dict).

    metadata_dict contains:
      link_type         = 'inline_word' (or 'none' if not injected)
      inline_anchor_text = phrase wrapped in <a>
      inline_dest_page   = dest page path (relative to repo root)
      inline_dest_href   = href inserted into anchor tag
      inline_skipped     = True if injection was skipped (no eligible target/anchor)

    If injection cannot be performed (no eligible anchor, no eligible
    destination, or depth < 1), returns (body_html, {'link_type': 'none', ...}).

    IMPORTANT: This function NEVER inserts a link that:
      - Points to a page in the ancestry chain (anti-backlink rule)
      - Points to the source fragment_href itself (self-link guard)
      - Points to the current target_page (current-page guard)
      - Has no eligible anchor phrase in body_html
    """
    empty_meta = {
        'link_type': 'none',
        'inline_anchor_text': '',
        'inline_dest_page': '',
        'inline_dest_href': '',
        'inline_skipped': True,
    }

    if depth < 1 or not body_html or not body_html.strip():
        return body_html, empty_meta

    # Build exclusion set: current target + fragment_href + ancestry
    exclude_norm: set = set()
    try:
        tp_rel = str(target_page.relative_to(REPO_ROOT)).replace('\\', '/')
        exclude_norm.add(tp_rel)
    except ValueError:
        pass
    if fragment_href:
        exclude_norm.add(fragment_href.lstrip('/').replace('\\', '/'))
    if ancestry_chain:
        for a in ancestry_chain:
            exclude_norm.add(str(a).replace('\\', '/').lstrip('/'))

    # Find eligible destination pages
    candidates = find_existing_contextual_pages(REPO_ROOT, exclude_norm)
    if not candidates:
        print(
            f'[inline_inject] depth={depth}: no eligible destination pages. Skipping.',
            file=sys.stderr,
        )
        return body_html, empty_meta

    # Score by motif overlap with title+teaser
    result = select_relevant_existing_page(
        title, teaser, candidates,
        threshold=convergence_threshold,
        ancestry_chain=ancestry_chain or [],
    )
    if result is None:
        print(
            f'[inline_inject] depth={depth}: no contextually relevant destination found. Skipping.',
            file=sys.stderr,
        )
        return body_html, empty_meta

    dest_page, score = result
    dest_rel = str(dest_page.relative_to(REPO_ROOT)).replace('\\', '/')
    # href relative to target_page's location (content pages are in nodes/)
    dest_href = _dest_href_from_target(dest_page, REPO_ROOT, target_page)

    # Extract anchor phrase candidates from body_html
    anchor_candidates = _extract_anchor_candidates(body_html)
    if not anchor_candidates:
        print(
            f'[inline_inject] depth={depth}: no anchor phrase candidates in body. Skipping.',
            file=sys.stderr,
        )
        return body_html, empty_meta

    # Pick the best anchor phrase (first = highest scored)
    anchor_phrase, para_text, _ = anchor_candidates[0]

    # Build the replacement: wrap anchor_phrase in <a class="wire-inline-link">
    # We need to find the FIRST occurrence of anchor_phrase inside a <p> tag
    # and wrap it without breaking any existing HTML tags.
    #
    # Strategy: work on <p> plain-text content only. Find the paragraph
    # containing anchor_phrase, then do a targeted replacement just on that
    # first occurrence within the first <p> that contains it.

    link_tag = (
        f'<a href="{_html.escape(dest_href)}" '
        f'class="wire-inline-link" '
        f'title="follow thread">'
        f'{_html.escape(anchor_phrase)}'
        f'</a>'
    )

    # Replace the first occurrence of anchor_phrase in body_html
    # Safety: only replace once, and only if not already inside an <a> tag.
    # We check by ensuring the match position is not inside an existing href.
    modified = body_html
    search_phrase = re.escape(anchor_phrase)
    # Find first occurrence not inside an existing <a>...</a>
    in_anchor_re = re.compile(r'<a\b[^>]*>.*?</a>', re.DOTALL | re.IGNORECASE)
    anchor_spans = [(m.start(), m.end()) for m in in_anchor_re.finditer(modified)]

    match = re.search(search_phrase, modified, re.IGNORECASE)
    if match:
        # Check not inside existing anchor
        ms, me = match.start(), match.end()
        inside_anchor = any(s <= ms and me <= e for s, e in anchor_spans)
        if not inside_anchor:
            modified = modified[:ms] + link_tag + modified[me:]
        else:
            print(
                f'[inline_inject] depth={depth}: anchor "{anchor_phrase}" '
                f'is already inside an <a> tag. Skipping.',
                file=sys.stderr,
            )
            return body_html, empty_meta
    else:
        print(
            f'[inline_inject] depth={depth}: anchor phrase "{anchor_phrase}" '
            f'not found in body_html after candidate extraction. Skipping.',
            file=sys.stderr,
        )
        return body_html, empty_meta

    meta = {
        'link_type': 'inline_word',
        'inline_anchor_text': anchor_phrase,
        'inline_dest_page': dest_rel,
        'inline_dest_href': dest_href,
        'inline_skipped': False,
        'inline_dest_motif_score': score,
    }

    print(
        f'[inline_inject] depth={depth}: injected link '
        f'anchor="{anchor_phrase}" → dest="{dest_rel}" (score={score})',
        file=sys.stderr,
    )

    return modified, meta


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description='Branching publish engine for bloodinthewire (v7).',
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
        parent_href='../index.html',  # root invocations parent is always index
    )

    save_branch_log(branch_log)

    # Pull the orientation and insertion from the last log entry
    last = branch_log['entries'][-1]
    orient  = last.get('orientation', 'unknown')
    ins_idx = last.get('insertion_index', 'unknown')
    ins_zone = last.get('insertion_zone', 'unknown')
    n_exist = last.get('n_existing_at_publish', 'unknown')
    action  = last.get('action', 'unknown')
    img_src = last.get('image_source', 'unknown')
    img_blk = last.get('image_blocked', False)

    print()
    print('branch_publish v8 complete')
    print('=' * 56)
    print(f'  entry:                {args.title}')
    print(f'  depth_cap:            {args.depth_cap}')
    print(f'  orientation:          {orient}')
    print(f'  insertion_roll:       {ins_zone} -> index {ins_idx}  (out of {n_exist} existing blocks)')
    print(f'  image_source:         {img_src}{"  [BLOCKED at this depth]" if img_blk else ""}')
    print(f'  convergence:          {"disabled (--no-convergence)" if args.no_convergence else f"threshold={args.convergence_threshold}"}')
    print(f'  depth_progression:    70/30->60/40->50/50 (gate applied at depth>=1)')
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
