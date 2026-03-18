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
    Validate that an image_web_path resolves to an existing file.

    If the path is non-empty but does not resolve to an existing file,
    emits a warning to stderr and returns an empty string so no dead
    <img> tag is rendered.

    Accepts paths relative to REPO_ROOT (e.g. "project/assets/web/foo.png")
    or absolute paths.

    Returns the original path unchanged if it resolves, empty string if not.
    """
    if not image_web_path:
        return image_web_path

    candidate = Path(image_web_path)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / image_web_path

    if candidate.exists():
        return image_web_path

    print(
        f'[branch_publish] IMAGE-GUARD: image path "{image_web_path}" does not '
        f'resolve to an existing file. Suppressing to avoid dead <img> tag.',
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

    # Validate image path — suppress dead refs before any HTML is written (v8)
    image_web_path = validate_image_web_path(image_web_path)

    # Apply depth-based media source guard before any HTML is written
    effective_image = image_allowed_at_depth(image_web_path, image_source, depth)
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
                    orientation=orientation, # propagate from root roll
                    parent_href=recursive_parent_href,  # v7: up-nav for children of this node
                    ancestry_chain=updated_ancestry,    # v8: anti-backlink guard
                )

    log_entry(branch_log, log_record)


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
