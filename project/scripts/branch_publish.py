#!/usr/bin/env python3
"""
branch_publish.py  v1
=====================
Branching-publish engine for bloodinthewire.

OVERVIEW
--------
When a new post is ready to publish, this script decides (by random coin
flip, stored for reproducibility) whether the content appears:

  • INLINE on the current page (roll=1):
      → A rich cascade card is inserted into index.html / the target page.

  • AS A LINK (roll=0):
      → A lean link card is inserted into the target page, and a new
        destination page (node or full content page) is generated.
        The destination is then recursively resolved up to --depth-cap rolls.

Rolls are saved in project/branch-log.json so pages are never re-rolled.

USAGE
-----
  python branch_publish.py \\
      --title "entry_0007 :: something" \\
      --teaser "one-line teaser" \\
      --posted-date 2026-03-18 \\
      --fragment-href "fragments/entry-0007.html" \\
      [--timestamp "14:22"] \\
      [--body-file path/to/body.html] \\
      [--image-web-path project/assets/web/img.jpg] \\
      [--target-page index.html]            # defaults to index.html
      [--depth-cap 5]                       # max branching depth (default 5)
      [--force-roll 1]                      # override roll (testing only)
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

DEPTH_CAP_DEFAULT = 5
CASCADE_POSITIONS = ['cp-a', 'cp-b', 'cp-c', 'cp-d', 'cp-e', 'cp-f', 'cp-g']


# ── Branch log ────────────────────────────────────────────────────────────────

def load_branch_log() -> dict:
    if BRANCH_LOG.exists():
        try:
            return json.loads(BRANCH_LOG.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'entries': [], 'meta': {'version': 1}}


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
) -> str:
    """Rich inline card — full teaser or body visible."""
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

    return (
        f'    <!-- branch: inline  depth={depth}  seed={roll_seed} -->\n'
        f'    <section class="cascade-block cascade-rich {cascade_pos}"'
        f' data-entry="{_html.escape(entry_id)}"'
        f' data-type="inline"'
        f' data-depth="{depth}"'
        f' data-branch-seed="{roll_seed}">\n'
        f'      <h2>{_html.escape(title)}</h2>\n'
        f'{ts_line}'
        f'{body_section}'
        f'{img_section}'
        f'{link_line}'
        f'      <p class="stamp">posted: {_html.escape(posted_date)}</p>\n'
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
) -> str:
    """Lean link card — minimal, just label + hyperlink."""
    card_class = 'cascade-node' if is_node else 'cascade-link'
    link_text  = 'open node' if is_node else 'open entry'
    return (
        f'    <!-- branch: link  depth={depth}  seed={roll_seed} -->\n'
        f'    <section class="cascade-block {card_class} {cascade_pos}"'
        f' data-entry="{_html.escape(entry_id)}"'
        f' data-type="link"'
        f' data-depth="{depth}"'
        f' data-branch-seed="{roll_seed}">\n'
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
      <p class="stamp">CONTENT NODE // depth={depth}</p>
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

def insert_cascade_card(page_path: Path, card_html: str) -> None:
    """Insert a cascade card at the top of CASCADE:START block."""
    src = page_path.read_text(encoding='utf-8')
    marker = '    <!-- CASCADE:START -->'
    if marker not in src:
        raise RuntimeError(f"No CASCADE:START marker found in {page_path}")
    insert_pos = src.index(marker) + len(marker) + 1
    src = src[:insert_pos] + '\n' + card_html + src[insert_pos:]
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
    target_page: Path,
    depth: int,
    depth_cap: int,
    force_roll: int | None,
    links_note: str,
    branch_log: dict,
    summary: list[str],
    is_root: bool = True,
) -> None:
    """
    Recursively resolve branch decisions and write pages.

    At each call:
      - Roll 0/1 (or use depth_cap override)
      - Roll=1: insert rich card inline on target_page
      - Roll=0: insert link card on target_page, create destination page,
                recurse into destination

    Rolls are stored in branch_log for reproducibility.
    """
    # At cap → force inline
    effective_roll = 1 if depth >= depth_cap else roll(force_roll if is_root else None)

    cascade_pos = pick_cascade_pos(target_page)

    log_record: dict = {
        'entry_id':      entry_id,
        'title':         title,
        'depth':         depth,
        'roll':          effective_roll,
        'target_page':   str(target_page.relative_to(REPO_ROOT)),
        'timestamp_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'posted_date':   posted_date,
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
            image_web_path=image_web_path,
            cascade_pos=cascade_pos,
            depth=depth,
            roll_seed=effective_roll,
        )
        insert_cascade_card(target_page, card)

        frag_label = Path(fragment_href).stem if fragment_href else entry_id
        insert_links_entry(target_page, fragment_href or '#', frag_label, links_note)

        log_record['action']  = 'inline'
        log_record['page']    = str(target_page.relative_to(REPO_ROOT))
        summary.append(
            f'  depth={depth}  INLINE → {target_page.relative_to(REPO_ROOT)}'
            f'  pos={cascade_pos}'
        )

    else:
        # ── LINK + RECURSE ───────────────────────────────────────────────────
        node_slug = make_node_slug(entry_id, depth)
        NODES_DIR.mkdir(parents=True, exist_ok=True)
        node_path = NODES_DIR / f'{node_slug}.html'

        # Recursive roll for the destination
        dest_roll = roll()
        deeper_depth = depth + 1

        if dest_roll == 1 or deeper_depth >= depth_cap:
            # Destination is a fresh content page
            node_html = make_content_page(
                node_slug=node_slug,
                entry_title=title,
                teaser=teaser,
                fragment_href=fragment_href,
                body_html=body_html,
                image_web_path=image_web_path,
                timestamp=timestamp,
                posted_date=posted_date,
                depth=deeper_depth,
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
        node_href = f'nodes/{node_slug}.html'

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
        )
        insert_cascade_card(target_page, card)
        insert_links_entry(target_page, node_href, node_slug, links_note)

        log_record['action']    = 'link'
        log_record['dest_page'] = node_href
        log_record['dest_type'] = dest_type
        log_record['dest_roll'] = dest_roll
        summary.append(
            f'  depth={depth}  LINK  → {node_href}  ({dest_type})  pos={cascade_pos}'
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
                image_web_path=image_web_path,
                target_page=node_path,
                depth=deeper_depth,
                depth_cap=depth_cap,
                force_roll=None,  # only override at root
                links_note=links_note,
                branch_log=branch_log,
                summary=summary,
                is_root=False,
            )

    log_entry(branch_log, log_record)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description='Branching publish engine for bloodinthewire.',
    )
    p.add_argument('--title',          required=True,  help='Entry title (e.g. "entry_0007 :: something")')
    p.add_argument('--teaser',         required=True,  help='One-line teaser')
    p.add_argument('--posted-date',    required=True,  help='YYYY-MM-DD')
    p.add_argument('--fragment-href',  default='',     help='Relative href to fragment page (optional)')
    p.add_argument('--timestamp',      default='',     help='HH:MM timestamp or omit')
    p.add_argument('--body-file',      default='',     help='Path to HTML body fragment file (optional)')
    p.add_argument('--image-web-path', default='',     help='Relative path to web-ready image (optional)')
    p.add_argument('--target-page',    default='',     help='Target page to insert into (default: index.html)')
    p.add_argument('--depth-cap',      type=int, default=DEPTH_CAP_DEFAULT,
                                                       help=f'Max branch depth (default: {DEPTH_CAP_DEFAULT})')
    p.add_argument('--force-roll',     type=int, default=None,
                                                       help='Force a specific roll value 0|1 (for testing)')
    p.add_argument('--links-note',     default='new entry',
                                                       help='Short annotation for links list')

    args = p.parse_args()

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

    entry_id    = make_entry_id(args.title)
    branch_log  = load_branch_log()
    summary: list[str] = []

    branch_resolve(
        entry_id=entry_id,
        title=args.title,
        teaser=args.teaser,
        fragment_href=args.fragment_href,
        posted_date=args.posted_date,
        timestamp=args.timestamp,
        body_html=body_html,
        image_web_path=args.image_web_path,
        target_page=target_page,
        depth=0,
        depth_cap=args.depth_cap,
        force_roll=args.force_roll,
        links_note=args.links_note,
        branch_log=branch_log,
        summary=summary,
        is_root=True,
    )

    save_branch_log(branch_log)

    print()
    print('branch_publish complete')
    print('=' * 48)
    print(f'  entry:       {args.title}')
    print(f'  depth_cap:   {args.depth_cap}')
    print(f'  branch log:  {BRANCH_LOG.relative_to(REPO_ROOT)}')
    print()
    print('  branch path:')
    for line in summary:
        print(line)
    print()
    print('  staged: working tree changed. do not git push until reviewed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
