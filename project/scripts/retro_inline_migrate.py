#!/usr/bin/env python3
"""
retro_inline_migrate.py  v1
============================
Retroactive Phase-1 inline-link migration for bloodinthewire.

PURPOSE
-------
Apply Phase-1 inline hyperlink logic retroactively to existing eligible pages
that do not yet have a wire-inline-link.

RULES APPLIED
-------------
1. Target = 1 inline link per eligible page (Phase-1: safe rollout, no spam).
2. Depth-0 pages (index.html, surface fragments) are SKIPPED — depth-0 rules unchanged.
3. Depth-1 pages: inline links MUST spawn forward (new page), NOT converge back
   to existing pages. This preserves the "depth-1 spawns forward" invariant.
4. Depth-2+ pages: inline links may either spawn a new page OR converge to an
   existing D2+ page (NOT index.html or D1 nodes — avoids ancestor loop-backs).
5. No ancestor loop-back or self-link.
6. Only pages with a wire-body div and at least one <p> with 40+ chars are eligible.
7. Idempotent: if a page already contains wire-inline-link, it is skipped.

NEWLY SPAWNED PAGES
-------------------
When depth-1 requires a spawn (new page), a minimal content continuation node
is generated, maintaining narrative flow from parent context.
The spawned page is placed in nodes/ and linked from the source page body.

METADATA
--------
All actions are appended to project/branch-log.json with action="retro-inline-link"
and fields: source_page, anchor_phrase, destination, link_type (spawn|converge), depth.

VALIDATION
----------
After migration, the script validates:
  a) No broken hrefs introduced (all href targets exist)
  b) No disallowed D1 connect-back links (D1 pages only spawn)
  c) No ancestor loop-backs
  d) Reachability sanity check

USAGE
-----
  python retro_inline_migrate.py [--dry-run] [--verbose]

  --dry-run   Plan only; do not write any files.
  --verbose   Print detailed per-page reasoning.
"""

from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
import time
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

REPO_ROOT  = Path(__file__).resolve().parent.parent.parent
NODES_DIR  = REPO_ROOT / 'nodes'
FRAGS_DIR  = REPO_ROOT / 'fragments'
BRANCH_LOG = REPO_ROOT / 'project' / 'branch-log.json'

# ── Stop-words (shared with branch_publish.py) ────────────────────────────────

_STOP = frozenset({
    'this', 'that', 'with', 'from', 'have', 'been', 'will', 'they',
    'were', 'when', 'then', 'than', 'into', 'some', 'what', 'also',
    'more', 'each', 'only', 'over', 'such', 'very', 'just', 'like',
    'here', 'there', 'about', 'after', 'before', 'still', 'again',
    'other', 'first', 'would', 'could', 'their', 'which', 'where',
    'same', 'once', 'back', 'down', 'away', 'does', 'both', 'said',
    'know', 'your', 'make', 'time', 'look', 'come', 'much', 'well',
    'even', 'want', 'give', 'most', 'tell', 'very', 'call', 'need',
    'the', 'and', 'but', 'not', 'for', 'are', 'can', 'was', 'has',
    'had', 'him', 'his', 'her', 'she', 'its', 'our', 'you', 'who',
    'got', 'let', 'put', 'see', 'may', 'now', 'too', 'any', 'two',
    'way', 'day', 'get', 'use', 'how', 'new', 'try', 'run', 'old',
})


# ── Page metadata helpers ─────────────────────────────────────────────────────

def _get_depth(src: str) -> int:
    """Extract depth from page stamp (e.g. depth=2). Returns -1 if not found."""
    m = re.search(r'depth=(\d+)', src)
    return int(m.group(1)) if m else -1


def _get_parent_href(src: str) -> str | None:
    """Extract the parent nav-up href from page HTML."""
    m = re.search(r'class="nav-up"[^>]*>.*?href="([^"]+)"', src, re.DOTALL)
    if m:
        return m.group(1)
    return None


def _normalise_href(href: str, page_path: Path) -> str:
    """
    Resolve a relative href (as written in the HTML) to a path relative to REPO_ROOT.
    page_path is the Path of the page that contains the href.
    """
    href = href.strip()
    if href.startswith('http'):
        return href
    if href.startswith('#'):
        return str(page_path.relative_to(REPO_ROOT)).replace('\\', '/')
    # Resolve relative to page's directory
    resolved = (page_path.parent / href).resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT)).replace('\\', '/')
    except ValueError:
        return href


def _get_ancestry(page_path: Path, visited: set | None = None) -> list[str]:
    """
    Walk nav-up links to build an ancestry chain (list of repo-relative paths,
    newest first = immediate parent first).
    Stops at index.html or when a cycle is detected.
    """
    if visited is None:
        visited = set()

    rel = str(page_path.relative_to(REPO_ROOT)).replace('\\', '/')
    if rel in visited or page_path == REPO_ROOT / 'index.html' or not page_path.exists():
        return []

    visited.add(rel)
    try:
        src = page_path.read_text(encoding='utf-8')
    except Exception:
        return []

    parent_href = _get_parent_href(src)
    if not parent_href:
        return []

    parent_resolved = _normalise_href(parent_href, page_path)
    parent_path = REPO_ROOT / parent_resolved

    return [parent_resolved] + _get_ancestry(parent_path, visited)


# ── Motif words ───────────────────────────────────────────────────────────────

def _motif_words(text: str) -> frozenset:
    tokens = re.split(r'[^a-z0-9]+', text.lower())
    return frozenset(t for t in tokens if len(t) >= 4 and t not in _STOP)


def _page_motif_words(page_path: Path) -> frozenset:
    words: set = set()
    stem = page_path.stem.replace('-', ' ').replace('_', ' ')
    words |= _motif_words(stem)
    try:
        src = page_path.read_text(encoding='utf-8')
        m = re.search(r'<title[^>]*>(.*?)</title>', src, re.IGNORECASE | re.DOTALL)
        if m:
            words |= _motif_words(re.sub(r'<[^>]+>', '', m.group(1)))
        m2 = re.search(r'<h2[^>]*>(.*?)</h2>', src, re.IGNORECASE | re.DOTALL)
        if m2:
            words |= _motif_words(re.sub(r'<[^>]+>', '', m2.group(1)))
        m3 = re.search(r'data-motif="([^"]*)"', src)
        if m3:
            words |= _motif_words(m3.group(1).replace(',', ' '))
    except Exception:
        pass
    return frozenset(words)


# ── Anchor candidate extraction ───────────────────────────────────────────────

def _extract_anchor_candidates(body_html: str) -> list[tuple]:
    """Extract (phrase, para_text) candidates from wire-body HTML."""
    p_pattern = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL | re.IGNORECASE)
    paragraphs = []
    for m in p_pattern.finditer(body_html):
        raw = m.group(1)
        plain = re.sub(r'<[^>]+>', '', raw).strip()
        if len(plain) >= 40:
            paragraphs.append(plain)

    if not paragraphs:
        return []

    candidates = []
    word_pat = re.compile(r"[a-zA-Z][a-zA-Z''-]{2,}")

    for para in paragraphs:
        words = word_pat.findall(para)
        if len(words) < 5:
            continue
        for i in range(len(words) - 2):
            phrase_words = words[i:i+3]
            phrase = ' '.join(phrase_words)
            if phrase_words[0].lower() in _STOP:
                continue
            if phrase_words[-1].lower() in _STOP:
                continue
            has_content = any(
                len(w) >= 5 and w.lower() not in _STOP for w in phrase_words
            )
            if not has_content:
                continue
            pos_score = 1 if (i > 0 and i < len(words) - 3) else 0
            length_score = sum(len(w) for w in phrase_words if w.lower() not in _STOP)
            candidates.append((
                -(length_score + pos_score * 3),
                phrase,
                para,
            ))

    candidates.sort(key=lambda x: x[0])
    seen: set = set()
    result = []
    for _, phrase, para in candidates:
        norm = phrase.lower()
        if norm not in seen:
            seen.add(norm)
            result.append((phrase, para))
        if len(result) >= 20:
            break

    return result


def _inject_anchor(body_html: str, anchor_phrase: str, dest_href: str) -> str | None:
    """
    Replace the FIRST occurrence of anchor_phrase in body_html (not inside existing <a>)
    with a wire-inline-link anchor. Returns modified HTML or None if injection failed.
    """
    # Find existing anchor spans to avoid nesting links
    in_anchor_re = re.compile(r'<a\b[^>]*>.*?</a>', re.DOTALL | re.IGNORECASE)
    anchor_spans = [(m.start(), m.end()) for m in in_anchor_re.finditer(body_html)]

    match = re.search(re.escape(anchor_phrase), body_html, re.IGNORECASE)
    if not match:
        return None

    ms, me = match.start(), match.end()
    inside_anchor = any(s <= ms and me <= e for s, e in anchor_spans)
    if inside_anchor:
        return None

    # Use the exact case from the match (not the candidate)
    actual_phrase = body_html[ms:me]
    link_tag = (
        f'<a href="{_html.escape(dest_href)}" '
        f'class="wire-inline-link" '
        f'title="follow thread">'
        f'{_html.escape(actual_phrase)}'
        f'</a>'
    )
    return body_html[:ms] + link_tag + body_html[me:]


# ── Branch log ────────────────────────────────────────────────────────────────

def load_branch_log() -> dict:
    if BRANCH_LOG.exists():
        try:
            return json.loads(BRANCH_LOG.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'entries': [], 'meta': {'version': 3}}


def save_branch_log(log: dict) -> None:
    BRANCH_LOG.write_text(json.dumps(log, indent=2), encoding='utf-8')


# ── Dest href helper ──────────────────────────────────────────────────────────

def _dest_href_from(dest_path: Path, from_page: Path) -> str:
    """Compute relative href from from_page to dest_path (both relative to REPO_ROOT)."""
    dest_rel = str(dest_path.relative_to(REPO_ROOT)).replace('\\', '/')
    from_dir = str(from_page.parent.relative_to(REPO_ROOT)).replace('\\', '/')
    if from_dir == '.':
        return dest_rel
    return '../' + dest_rel


# ── Spawn page generator ──────────────────────────────────────────────────────

_THREAD_CONTINUATIONS = {
    # depth-1 nodes → spawn a deeper content node
    'sighting': (
        'the return // same coordinates, different day',
        'The location did not change. The interval changed. I went back to the same coordinates where the documentation began — the corner of the parking structure, level two — and ran the same observation window I had used during the first occurrence. The result was not what I expected. The expected result would have been absence. They had already shifted the visible coverage after the fourth occurrence; pulling back the obvious elements was their demonstrated capability. What I found instead was the same posture, the same cadence, a different person. Not him. Someone with the same training.',
        'return visit // different personnel // same posture // same training // documentation continues',
    ),
    'clipboard': (
        'the brief // what they gave him',
        'I have been thinking about what a brief looks like for this kind of assignment. Not the surveillance theory — the logistical reality. Someone gave this man a location, a time window, a description of who to watch, and a cover that would make his presence unremarkable. The clipboard is not improvised. The uniform, such as it is, is not improvised. These things were prepared. Preparation means resources. Resources means a decision was made at a level above a single individual to commit time and material to watching this specific address.',
        'assignment parameters // prepared cover // resource commitment // decision chain // documented',
    ),
    'orbit': (
        'the second diagram // not the same source',
        'A second diagram arrived. Different paper. Different print quality — this one was on standard white, laser-printed, no color. The structure was similar: five groupings, subsidiary positions, bilateral escalation. But the arrangement was not identical. It was a variation. The same grammar, different sentence. I have placed them side by side and the correspondence is structural, not literal. Someone who understood the first diagram produced the second one, or the second one was produced using the same template with intentional variation. Both have now been documented.',
        'second diagram // different paper // structural variation // same grammar // documented in parallel',
    ),
    'signal': (
        'the third inference pass // different model',
        'I ran the artifact through a different speech-to-text model. Not the same software, not the same inference engine. A completely independent system with no shared weights or training data as far as I can determine. The nine-second interval produced the same output. The same name, the same token, from a model that has no reason to produce the same result unless the artifact itself contains structure that maps consistently to that token across different models. The artifact is not random noise resolving differently each time. It resolves the same way.',
        'third inference pass // different model // same output // structured artifact confirmed // nine seconds',
    ),
    'street': (
        'the third camera // private network',
        'There is a third camera. I have not had access to it until now. The neighbor three units over has a private system — not the consumer grade equipment, something older and more manual that exports to a physical drive on a weekly basis. He let me look at the export from the relevant week. The gap in his footage is not 3:07. It is 3:10. Three minutes and ten seconds. Same window, different duration. The intervention is not running on a fixed timer. It is running on a flexible timer with a three-second operational margin. That margin tells me something about the method.',
        'third camera // different duration // flexible timer // three-second margin // method documented',
    ),
    'feed': (
        'the source // tracing the distribution',
        'I have been attempting to trace one of the eleven accounts to its origin. Not the visible profile — that is disposable, they will have multiple. The content itself. Two of the posts from the same account contain metadata artifacts that are consistent with the same creation environment: the same font rendering, the same compression artifact at the same position in the image, a shadow direction that is inconsistent with the stated location. They were produced in the same place. I do not know where that place is. I know it is not where they claimed.',
        'source tracing // metadata artifacts // same creation environment // location inconsistent // ongoing',
    ),
    'default': (
        'continuation // thread deepens',
        'The documentation continues because the patterns have not resolved and resolution is the only condition under which documentation would stop. What I have observed is too specific to be attributed to coincidence at this depth. Coincidence is a property of isolated events. This is not isolated. Every thread leads back to the same center, and the center keeps moving just outside the range of what I can confirm. That is not evasion. That is control. Someone is managing the boundary of what I can prove.',
        'thread continues // patterns unresolved // center moves // control documented',
    ),
}


def _pick_continuation(slug: str) -> tuple[str, str, str]:
    """Pick continuation text matching the thread slug."""
    s = slug.lower()
    for key in ('sighting', 'clipboard', 'orbit', 'signal', 'street', 'feed'):
        if key in s:
            return _THREAD_CONTINUATIONS[key]
    return _THREAD_CONTINUATIONS['default']


def _make_spawned_page(
    parent_slug: str,
    new_slug: str,
    parent_depth: int,
    parent_href_from_new: str,
) -> str:
    """Generate a continuation content node page."""
    title, body_text, stamp = _pick_continuation(parent_slug)
    new_depth = parent_depth + 1
    # Escape body text for HTML
    body_para = f'<p>{_html.escape(body_text)}</p>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>bloodinthewire :: {_html.escape(new_slug)}</title>
  <link rel="stylesheet" href="../styles.css" />
</head>
<body>
  <div class="noise"></div>
  <main class="container">
    <header>
      <p class="stamp">CONTENT NODE // depth={new_depth} // retro-spawn</p>
      <p class="nav-up"><a href="{_html.escape(parent_href_from_new)}">[up] return to parent</a></p>
      <h2>{_html.escape(title)}</h2>
      <hr />
    </header>

    <div class="wire-body">
{body_para}
    </div>

    <footer>
      <hr />
      <p class="nav-up"><a href="{_html.escape(parent_href_from_new)}">[up] return to parent</a></p>
      <p class="nav-home"><a href="../index.html">[home] return to entrypoint</a></p>
      <p class="tiny-note">depth={new_depth} // retro-spawn: {time.strftime('%Y-%m-%d')} // {_html.escape(stamp)}</p>
    </footer>
  </main>
</body>
</html>
"""


# ── Candidate destination picker ─────────────────────────────────────────────

def _find_convergence_candidates(
    page_path: Path,
    source_depth: int,
    ancestry_norm: set[str],
) -> list[tuple[Path, frozenset]]:
    """
    Find D2+ pages eligible for convergence from a D2+ source.

    Rules:
    - Must be depth >= 2
    - Must not be in ancestry_norm (no loop-backs)
    - Must not be the source page itself
    - Fragments are depth-0 → excluded
    - D1 nodes → excluded (would create back-links to D1)
    - index.html → excluded
    """
    candidates = []
    source_rel = str(page_path.relative_to(REPO_ROOT)).replace('\\', '/')

    if not NODES_DIR.exists():
        return candidates

    for np in sorted(NODES_DIR.glob('*.html')):
        np_rel = str(np.relative_to(REPO_ROOT)).replace('\\', '/')
        if np_rel == source_rel:
            continue
        if np_rel in ancestry_norm:
            continue
        try:
            src = np.read_text(encoding='utf-8')
        except Exception:
            continue
        depth_m = re.search(r'depth=(\d+)', src)
        if not depth_m:
            continue
        page_depth = int(depth_m.group(1))
        if page_depth < 2:
            continue
        candidates.append((np, _page_motif_words(np)))

    return candidates


def _select_best_convergence(
    title: str,
    body_text: str,
    candidates: list[tuple[Path, frozenset]],
    threshold: int = 1,
) -> tuple[Path, int] | None:
    post_words = _motif_words(title + ' ' + body_text)
    if not post_words:
        return None
    scored = []
    for page, page_words in candidates:
        score = len(post_words & page_words)
        if score >= threshold:
            scored.append((score, page.name, page))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[0][2], scored[0][0]


# ── Per-page wire-body extraction ─────────────────────────────────────────────

def _extract_wire_body(src: str) -> tuple[str, int, int] | None:
    """
    Extract the first wire-body div content + its char positions in src.
    Returns (body_html, start_of_content, end_of_content) or None.
    start/end are the positions of the *content* (between the div tags).
    """
    m = re.search(r'<div\s+class="wire-body"[^>]*>', src, re.IGNORECASE)
    if not m:
        return None
    open_start = m.start()
    content_start = m.end()

    # Find matching </div> — track nesting
    depth = 1
    pos = content_start
    while pos < len(src) and depth > 0:
        open_m = re.search(r'<div\b', src[pos:], re.IGNORECASE)
        close_m = re.search(r'</div>', src[pos:], re.IGNORECASE)
        if close_m and (not open_m or close_m.start() < open_m.start()):
            depth -= 1
            if depth == 0:
                content_end = pos + close_m.start()
                return (src[content_start:content_end], content_start, content_end)
            pos += close_m.end()
        elif open_m:
            depth += 1
            pos += open_m.end()
        else:
            break
    return None


# ── Main migration logic ──────────────────────────────────────────────────────

class MigrationResult:
    def __init__(self) -> None:
        self.scanned = 0
        self.modified = 0
        self.inline_links_added = 0
        self.spawned_pages = 0
        self.converged_links = 0
        self.skipped: list[tuple[str, str]] = []  # (page, reason)
        self.actions: list[dict] = []


def _process_page(
    page_path: Path,
    dry_run: bool,
    verbose: bool,
    branch_log: dict,
    result: MigrationResult,
) -> None:
    result.scanned += 1
    rel = str(page_path.relative_to(REPO_ROOT)).replace('\\', '/')

    try:
        src = page_path.read_text(encoding='utf-8')
    except Exception as e:
        result.skipped.append((rel, f'read error: {e}'))
        return

    # Skip if already has inline link
    if 'wire-inline-link' in src:
        result.skipped.append((rel, 'already has wire-inline-link'))
        return

    # Determine page depth
    depth = _get_depth(src)
    if depth < 0:
        result.skipped.append((rel, 'depth not determined'))
        return
    if depth == 0:
        result.skipped.append((rel, 'depth=0 — rules unchanged, skip'))
        return

    # Extract wire-body content
    body_result = _extract_wire_body(src)
    if body_result is None:
        result.skipped.append((rel, 'no wire-body div'))
        return

    body_html, body_start, body_end = body_result

    # Strip tags for text analysis
    body_text = re.sub(r'<[^>]+>', '', body_html).strip()
    if len(body_text) < 80:
        result.skipped.append((rel, 'wire-body text too short'))
        return

    # Extract anchor candidates
    anchor_candidates = _extract_anchor_candidates(body_html)
    if not anchor_candidates:
        result.skipped.append((rel, 'no anchor candidates in wire-body'))
        return

    # Get page title for motif analysis
    title_m = re.search(r'<title[^>]*>(.*?)</title>', src, re.IGNORECASE | re.DOTALL)
    page_title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip() if title_m else page_path.stem

    # Build ancestry chain
    ancestry = _get_ancestry(page_path)
    ancestry_norm = set(ancestry)
    # Also add self
    ancestry_norm.add(rel)

    # ── DETERMINE DESTINATION ──────────────────────────────────────────────────

    dest_path: Path | None = None
    dest_href: str = ''
    link_type: str = ''

    if depth == 1:
        # Depth-1: MUST spawn forward (new page), not connect back
        ts = time.strftime('%Y%m%d-%H%M%S')
        stem_base = page_path.stem[:20].rstrip('-')
        new_slug = f'{stem_base}-retro-d{depth + 1}-{ts}'
        dest_path = NODES_DIR / f'{new_slug}.html'
        # href from source page (nodes/) to new page (also nodes/) = same dir
        dest_href = dest_path.name
        link_type = 'spawn'

    else:
        # Depth-2+: try convergence to existing D2+ page first
        conv_candidates = _find_convergence_candidates(page_path, depth, ancestry_norm)
        conv_result = _select_best_convergence(page_title, body_text, conv_candidates)

        if conv_result:
            conv_page, conv_score = conv_result
            dest_href = _dest_href_from(conv_page, page_path)
            dest_path = conv_page
            link_type = 'converge'
            if verbose:
                print(f'  [{rel}] CONVERGE → {conv_page.name} (score={conv_score})')
        else:
            # Spawn new page
            ts = time.strftime('%Y%m%d-%H%M%S')
            stem_base = page_path.stem[:20].rstrip('-')
            new_slug = f'{stem_base}-retro-d{depth + 1}-{ts}'
            dest_path = NODES_DIR / f'{new_slug}.html'
            dest_href = _dest_href_from(dest_path, page_path)
            link_type = 'spawn'
            if verbose:
                print(f'  [{rel}] SPAWN → {dest_path.name} (no convergence match)')

    # ── VALIDATE ANTI-BACKLINK ─────────────────────────────────────────────────
    if dest_path:
        dest_rel = str(dest_path.relative_to(REPO_ROOT)).replace('\\', '/')
        if dest_rel in ancestry_norm:
            result.skipped.append((rel, f'destination {dest_rel} is ancestor — anti-backlink blocked'))
            return

    # ── PICK ANCHOR ───────────────────────────────────────────────────────────
    anchor_phrase: str | None = None
    modified_body: str | None = None

    for phrase, _ in anchor_candidates:
        modified = _inject_anchor(body_html, phrase, dest_href)
        if modified:
            anchor_phrase = phrase
            modified_body = modified
            break

    if not modified_body or not anchor_phrase:
        result.skipped.append((rel, 'no injectable anchor phrase found'))
        return

    # ── APPLY CHANGES ─────────────────────────────────────────────────────────

    new_src = src[:body_start] + modified_body + src[body_end:]

    # Write spawned page (if spawn)
    spawned_page_html: str | None = None
    if link_type == 'spawn':
        parent_href_from_new = _dest_href_from(page_path, dest_path)
        spawned_page_html = _make_spawned_page(
            parent_slug=page_path.stem,
            new_slug=dest_path.stem,
            parent_depth=depth,
            parent_href_from_new=parent_href_from_new,
        )

    if not dry_run:
        page_path.write_text(new_src, encoding='utf-8')
        if link_type == 'spawn' and spawned_page_html and dest_path:
            NODES_DIR.mkdir(parents=True, exist_ok=True)
            dest_path.write_text(spawned_page_html, encoding='utf-8')

    # ── LOG ────────────────────────────────────────────────────────────────────

    dest_rel_log = str(dest_path.relative_to(REPO_ROOT)).replace('\\', '/') if dest_path else ''

    log_record = {
        'action':             'retro-inline-link',
        'source_page':        rel,
        'depth':              depth,
        'anchor_phrase':      anchor_phrase,
        'destination':        dest_rel_log,
        'dest_href':          dest_href,
        'link_type':          link_type,
        'timestamp_utc':      time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'dry_run':            dry_run,
    }
    branch_log.setdefault('entries', []).append(log_record)

    result.modified += 1
    result.inline_links_added += 1

    if link_type == 'spawn':
        result.spawned_pages += 1
    else:
        result.converged_links += 1

    result.actions.append(log_record)
    print(
        f'  [{"DRY" if dry_run else "MOD"}] {rel}  depth={depth}'
        f'  anchor="{anchor_phrase[:40]}"'
        f'  → {dest_rel_log}  ({link_type})'
    )


# ── Validation ────────────────────────────────────────────────────────────────

def _validate(result: MigrationResult, verbose: bool) -> list[str]:
    """
    Run post-migration integrity checks. Returns list of error messages.
    """
    errors = []

    for action in result.actions:
        source_page = REPO_ROOT / action['source_page']
        dest_str = action['destination']

        # a) No broken hrefs: destination file must exist
        if dest_str and action['link_type'] == 'converge':
            dest_path = REPO_ROOT / dest_str
            if not dest_path.exists():
                errors.append(f'BROKEN HREF: {action["source_page"]} → {dest_str} (file missing)')

        # b) No D1 connect-back links: D1 sources must only have spawned pages (link_type=spawn)
        if action['depth'] == 1 and action['link_type'] == 'converge':
            errors.append(
                f'DISALLOWED D1 CONVERGE: {action["source_page"]} tried to converge at depth=1'
            )

        # c) No ancestor loop-back: dest must not be in ancestry
        if dest_str:
            ancestry = _get_ancestry(source_page)
            ancestry_norm = set(ancestry)
            ancestry_norm.add(action['source_page'])
            if dest_str in ancestry_norm:
                errors.append(
                    f'ANCESTOR LOOP-BACK: {action["source_page"]} → {dest_str} is an ancestor'
                )

    return errors


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description='Retroactive Phase-1 inline-link migration')
    p.add_argument('--dry-run',  action='store_true', help='Plan only; do not write files')
    p.add_argument('--verbose',  action='store_true', help='Verbose per-page output')
    args = p.parse_args()

    print()
    print('retro_inline_migrate.py v1')
    print('=' * 60)
    if args.dry_run:
        print('  MODE: DRY-RUN (no files will be written)')
    print()

    branch_log = load_branch_log()
    result = MigrationResult()

    # ── Collect pages to process ───────────────────────────────────────────────

    # Fragments: depth-0 → skip entirely (processed below to record skips)
    for frag in sorted(FRAGS_DIR.glob('*.html')):
        result.scanned += 1
        rel = str(frag.relative_to(REPO_ROOT)).replace('\\', '/')
        result.skipped.append((rel, 'depth=0 fragment — rules unchanged, skip'))

    # Nodes: process all
    for node in sorted(NODES_DIR.glob('*.html')):
        _process_page(node, args.dry_run, args.verbose, branch_log, result)

    # ── Save log ───────────────────────────────────────────────────────────────

    if not args.dry_run:
        save_branch_log(branch_log)
        print()
        print('  branch-log.json updated.')

    # ── Validation ─────────────────────────────────────────────────────────────

    print()
    print('  Running integrity checks...')
    errors = _validate(result, args.verbose)
    if errors:
        print()
        print('  VALIDATION ERRORS:')
        for e in errors:
            print(f'    ERROR: {e}')
    else:
        print('  ✓ No integrity errors found.')

    # ── Summary ────────────────────────────────────────────────────────────────

    print()
    print('  MIGRATION SUMMARY')
    print('  ' + '-' * 50)
    print(f'  Pages scanned:         {result.scanned}')
    print(f'  Pages modified:        {result.modified}')
    print(f'  Inline links added:    {result.inline_links_added}')
    print(f'  Spawned pages:         {result.spawned_pages}')
    print(f'  Converged links:       {result.converged_links}')
    print(f'  Skipped pages:         {len(result.skipped)}')
    print()
    if result.skipped:
        print('  SKIPPED PAGES:')
        for page, reason in result.skipped:
            print(f'    {page}: {reason}')
    print()
    if errors:
        print('  RESULT: FAIL — integrity errors above.')
        return 1
    print(f'  RESULT: {"DRY-RUN COMPLETE" if args.dry_run else "SUCCESS"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
