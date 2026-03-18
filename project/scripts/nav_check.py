#!/usr/bin/env python3
"""
nav_check.py
============
Consistency report for bloodinthewire nav affordances.

Reports for every public HTML page (nodes/*.html, fragments/*.html):
  - Whether a nav-up link is present
  - Whether a nav-down link is present
  - Whether the page is a pending/empty junction node
  - Any pages intentionally without down-links and why

Run from repo root:
  python3 project/scripts/nav_check.py
"""

from __future__ import annotations
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

def check_page(path: Path) -> dict:
    try:
        src = path.read_text(encoding='utf-8')
    except Exception as e:
        return {'path': str(path.relative_to(REPO_ROOT)), 'error': str(e)}

    rel = str(path.relative_to(REPO_ROOT)).replace('\\', '/')

    # nav-up: class="nav-up" present anywhere
    has_nav_up = bool(re.search(r'class="nav-up"', src))

    # nav-down: class="nav-down" present anywhere
    has_nav_down = bool(re.search(r'class="nav-down"', src))

    # Is this a junction node? (has CASCADE:START and LINKS:START markers)
    is_node = 'CASCADE:START' in src and 'LINKS:START' in src

    # Is the cascade region empty? (only comments between CASCADE:START and CASCADE:END)
    cascade_empty = False
    cascade_content_count = 0
    if is_node:
        m = re.search(r'<!-- CASCADE:START -->(.*?)<!-- CASCADE:END -->', src, re.DOTALL)
        if m:
            region = m.group(1)
            # Strip comments and whitespace
            stripped = re.sub(r'<!--.*?-->', '', region, flags=re.DOTALL).strip()
            cascade_empty = not stripped
            cascade_content_count = len(re.findall(r'<section\s+class="cascade-block', region))

    # data-node-status
    m_status = re.search(r'data-node-status="([^"]*)"', src)
    node_status = m_status.group(1) if m_status else None

    # Reason for no down-link
    no_down_reason = None
    if not has_nav_down:
        if is_node and cascade_empty:
            no_down_reason = 'junction node — empty, pending future posts'
        elif is_node and not cascade_empty:
            no_down_reason = 'junction node — has cascade content but no explicit nav-down (content embedded inline)'
        elif 'CONTENT NODE' in src:
            no_down_reason = 'content node — terminal, no child expected'
        else:
            no_down_reason = 'terminal fragment — no down-link (intentional or unlinked leaf)'

    return {
        'path': rel,
        'is_node': is_node,
        'node_status': node_status,
        'cascade_empty': cascade_empty if is_node else None,
        'cascade_blocks': cascade_content_count if is_node else None,
        'has_nav_up': has_nav_up,
        'has_nav_down': has_nav_down,
        'no_down_reason': no_down_reason if not has_nav_down else None,
    }


def main():
    pages = []

    frags_dir = REPO_ROOT / 'fragments'
    nodes_dir = REPO_ROOT / 'nodes'

    if frags_dir.exists():
        for p in sorted(frags_dir.glob('*.html')):
            # Skip raw article fragments (no <html> tag)
            try:
                src = p.read_text(encoding='utf-8')
                if not src.strip().startswith('<!doctype') and '<html' not in src[:200]:
                    pages.append({'path': str(p.relative_to(REPO_ROOT)).replace('\\','/'),
                                  'note': 'SKIP — raw embeddable fragment (no <html> wrapper)',
                                  'has_nav_up': None, 'has_nav_down': None,
                                  'is_node': False, 'no_down_reason': 'raw fragment — nav not applicable'})
                    continue
            except Exception:
                pass
            pages.append(check_page(p))

    if nodes_dir.exists():
        for p in sorted(nodes_dir.glob('*.html')):
            if p.name == '.gitkeep':
                continue
            pages.append(check_page(p))

    # Print report
    print()
    print('bloodinthewire nav consistency report')
    print('=' * 60)
    print(f'  pages scanned: {len(pages)}')
    print()

    nav_up_ok = [r for r in pages if r.get('has_nav_up') is True]
    nav_down_ok = [r for r in pages if r.get('has_nav_down') is True]
    no_nav_up = [r for r in pages if r.get('has_nav_up') is False]
    skip_pages = [r for r in pages if r.get('has_nav_up') is None]

    print(f'  nav-up present:  {len(nav_up_ok)} / {len(pages) - len(skip_pages)} applicable')
    print(f'  nav-down present: {len(nav_down_ok)} / {len(pages) - len(skip_pages)} applicable')
    print(f'  skipped (raw fragments): {len(skip_pages)}')
    print()

    print('  PAGES DETAIL:')
    print('  ' + '-' * 56)
    for r in pages:
        path = r['path']
        if r.get('has_nav_up') is None:
            note = r.get('note', '')
            print(f'  {path}')
            print(f'    → {note}')
            continue

        up_mark = '✓' if r.get('has_nav_up') else '✗'
        dn_mark = '✓' if r.get('has_nav_down') else '–'
        print(f'  {path}')
        print(f'    nav-up: {up_mark}  nav-down: {dn_mark}', end='')
        if r.get('is_node'):
            status = r.get('node_status') or 'unknown'
            empty = r.get('cascade_empty')
            blocks = r.get('cascade_blocks', 0)
            print(f'  [node status={status} cascade_empty={empty} blocks={blocks}]', end='')
        print()
        if not r.get('has_nav_down') and r.get('no_down_reason'):
            print(f'    no-down reason: {r["no_down_reason"]}')
        if not r.get('has_nav_up'):
            print(f'    ⚠ MISSING nav-up')

    print()
    if no_nav_up:
        print(f'  ⚠ {len(no_nav_up)} page(s) missing nav-up:')
        for r in no_nav_up:
            print(f'    - {r["path"]}')
    else:
        print('  ✓ All applicable pages have nav-up.')
    print()


if __name__ == '__main__':
    main()
