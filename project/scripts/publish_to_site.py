#!/usr/bin/env python3
"""
publish_to_site.py

Insert a new post reference into index.html in newest-first order.
- Adds a <section class="post"> block at the TOP of post list (after header)
- Adds a related-fragments <li> at the TOP of the list
"""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

INDEX_PATH = Path('/home/gruzz/bloodinthewire/index.html')


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--title', required=True)
    p.add_argument('--timestamp', default='OMIT')
    p.add_argument('--teaser', required=True)
    p.add_argument('--fragment-href', required=True, help='e.g. fragments/sighting-0009.html')
    p.add_argument('--posted-date', required=True, help='YYYY-MM-DD')
    p.add_argument('--links-note', default='new entry')
    args = p.parse_args()

    src = INDEX_PATH.read_text(encoding='utf-8')

    ts_line = ''
    if args.timestamp and args.timestamp.upper() != 'OMIT':
        ts_line = f'<p><strong>{html.escape(args.timestamp)}</strong> — {html.escape(args.teaser)}</p>'
    else:
        ts_line = f'<p>{html.escape(args.teaser)}</p>'

    post_block = (
        '    <section class="post">\n'
        f'      <h2>{html.escape(args.title)}</h2>\n'
        f'      {ts_line}\n'
        f'      <p><a href="{html.escape(args.fragment_href)}">open entry</a></p>\n'
        f'      <p class="stamp">posted: {html.escape(args.posted_date)}</p>\n'
        '    </section>\n\n'
    )

    # Insert new post BEFORE first existing post section (newest first)
    m = re.search(r'\n\s*<section class="post">', src)
    if m:
        insert_at = m.start() + 1
    else:
        # fallback: before links section
        m2 = re.search(r'\n\s*<section class="links">', src)
        if not m2:
            raise SystemExit('Could not find insertion point in index.html')
        insert_at = m2.start() + 1

    src = src[:insert_at] + post_block + src[insert_at:]

    # Insert related fragment link at TOP of list
    li = f'        <li><a href="{html.escape(args.fragment_href)}">{html.escape(Path(args.fragment_href).stem)}</a> ({html.escape(args.links_note)})</li>\n'
    ul_match = re.search(r'(<section class="links">[\s\S]*?<ul>\n)', src)
    if ul_match:
        pos = ul_match.end(1)
        src = src[:pos] + li + src[pos:]

    INDEX_PATH.write_text(src, encoding='utf-8')
    print(str(INDEX_PATH))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
