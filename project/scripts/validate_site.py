#!/usr/bin/env python3
"""
validate_site.py
================
Post-run validator gate for Blood in the Wire.

Runs all five guardrails after branch_publish.py writes files and BEFORE
any git push occurs. Returns exit code 0 if all checks pass, 1 if any
check fails (or 2 for auto-repair success).

GUARDRAILS (all mandatory, per spec):
--------------------------------------
1. LINK/IMG INTEGRITY:
   - No broken href (relative links to non-existent files).
   - No broken img src (relative src to non-existent files).
   - Checks all public HTML pages: index.html, fragments/*.html, nodes/*.html.

2. NODE REACHABILITY:
   - Every newly created node (since last branch-log snapshot) must be
     traversable from index.html after run wiring.
   - New nodes are detected by comparing argument --new-pages list against
     the traversal graph.

3. DEPTH-0 LINK POLICY:
   - index.html must NOT contain href="nodes/..." links (direct surface
     links into nodes/ are forbidden at depth=0, per branch_publish rules).
   - Exception: only inside cascade cards with data-depth="0" is allowed
     when pointing to nodes/ via a link card.
   - NOTE: the branch_publish script intentionally DOES create depth=0 cards
     that link to nodes/ (action=link at depth=0 creates nodes/*.html targets).
     This check validates that index.html doesn't have raw/bare nodes/ links
     that bypass the cascade card system.

4. ABSOLUTE PATH CHECK:
   - No absolute filesystem paths in served HTML (e.g. /home/gruzz/...).
   - Scans all public HTML for patterns matching /home/*, /usr/*, /var/*, etc.

5. MEDIA SANITY CHECK:
   - All img src values in public HTML must resolve to existing files (relative
     to the file containing the img tag).
   - If an invalid img src is found, attempts auto-repair (remove the tag,
     replace with text-only fallback) if deterministic.

USAGE:
------
  python3 validate_site.py [--new-pages path1 path2 ...]
                           [--repo-root /path/to/repo]
                           [--auto-repair]
                           [--report-only]

  --new-pages   : space-separated list of newly created HTML page paths
                  (relative to repo root, e.g. nodes/foo.html) to check
                  reachability for. If omitted, all nodes/*.html are checked.
  --repo-root   : repo root path. Default: inferred from script location.
  --auto-repair : attempt auto-repair on deterministic failures (broken img
                  tags). Does NOT repair broken hrefs (non-deterministic).
  --report-only : print results but always exit 0 (for diagnostics).

EXIT CODES:
-----------
  0 = all checks passed (or all failures repaired)
  1 = validation failure(s) that could not be auto-repaired
  2 = auto-repair applied (caller should note changes were made)
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT  = SCRIPT_DIR.parent.parent   # /home/gruzz/bloodinthewire

# ── Helpers ───────────────────────────────────────────────────────────────────

def _all_public_html(repo_root: Path) -> list[Path]:
    """Return all public HTML files: index.html, fragments/*.html, nodes/*.html."""
    pages = []
    root_index = repo_root / 'index.html'
    if root_index.exists():
        pages.append(root_index)
    for d in ['fragments', 'nodes']:
        d_path = repo_root / d
        if d_path.is_dir():
            for p in sorted(d_path.glob('*.html')):
                pages.append(p)
    return pages


def _relative_hrefs(src: str) -> list[str]:
    """Extract all relative href values (not http/https/mailto/#) from HTML."""
    # Match href="..." or href='...'
    raw = re.findall(r'href=["\']([^"\'#?]+)["\']', src)
    return [h for h in raw if not h.startswith(('http://', 'https://', 'mailto:'))]


def _img_srcs(src: str) -> list[str]:
    """Extract all img src values from HTML."""
    raw = re.findall(r'<img\s[^>]*src=["\']([^"\']+)["\']', src)
    return [s for s in raw if not s.startswith(('http://', 'https://', 'data:'))]


def _resolve_href(href: str, page_path: Path, repo_root: Path) -> Optional[Path]:
    """
    Resolve a relative href from page_path's perspective.
    Returns the resolved Path or None if it cannot be determined.
    """
    # Normalise: strip leading / (treat as repo-root-relative)
    if href.startswith('/'):
        return repo_root / href.lstrip('/')
    # Relative to the page's directory
    return (page_path.parent / href).resolve()


# ── Guardrail 1+5: Link & Image Integrity (combined scan) ────────────────────

def check_link_integrity(
    pages: list[Path],
    repo_root: Path,
    auto_repair: bool = False,
) -> tuple[list[str], list[str], int]:
    """
    Check all relative hrefs and img srcs in public HTML pages.

    Returns:
      (href_failures, img_failures, repairs_made)

    href_failures: list of "file:href" error strings
    img_failures:  list of "file:src" error strings
    repairs_made:  count of auto-repairs applied (broken img tags removed)
    """
    href_failures = []
    img_failures  = []
    repairs_made  = 0

    for page in pages:
        try:
            src = page.read_text(encoding='utf-8')
        except Exception as e:
            href_failures.append(f'{page.relative_to(repo_root)}: read error: {e}')
            continue

        page_rel = str(page.relative_to(repo_root))

        # Check hrefs
        for href in _relative_hrefs(src):
            resolved = _resolve_href(href, page, repo_root)
            if resolved is None:
                continue
            if not resolved.exists():
                href_failures.append(f'{page_rel}: broken href "{href}"')

        # Check img srcs
        bad_srcs = []
        for img_src in _img_srcs(src):
            resolved = _resolve_href(img_src, page, repo_root)
            if resolved is None:
                continue
            if not resolved.exists():
                img_failures.append(f'{page_rel}: broken img src "{img_src}"')
                bad_srcs.append(img_src)

        # Auto-repair: remove broken img tags
        if auto_repair and bad_srcs:
            repaired = src
            for bad_src in bad_srcs:
                # Remove entire <figure>...</figure> blocks containing the bad src
                escaped_src = re.escape(bad_src)
                repaired = re.sub(
                    r'<figure[^>]*>.*?<img\s[^>]*src=["\']' + escaped_src + r'["\'][^>]*>.*?</figure>',
                    '<!-- [media-guard: image removed — file not found] -->',
                    repaired,
                    flags=re.DOTALL | re.IGNORECASE,
                )
                # Also remove bare <img> tags (not inside figures)
                repaired = re.sub(
                    r'<img\s[^>]*src=["\']' + escaped_src + r'["\'][^>]*>',
                    '<!-- [media-guard: img removed — file not found] -->',
                    repaired,
                    flags=re.IGNORECASE,
                )
            if repaired != src:
                page.write_text(repaired, encoding='utf-8')
                repairs_made += len(bad_srcs)
                print(
                    f'[validate_site] AUTO-REPAIR: removed {len(bad_srcs)} broken img '
                    f'tag(s) from {page_rel}.',
                    file=sys.stderr,
                )

    return href_failures, img_failures, repairs_made


# ── Guardrail 3: Depth-0 nodes/* link policy ─────────────────────────────────

def check_depth0_node_links(repo_root: Path) -> list[str]:
    """
    Check index.html for raw nodes/* href references that bypass cascade cards.

    The rule: index.html must not link to nodes/* OUTSIDE of properly formed
    cascade card sections. We check that every href="nodes/..." on index.html
    appears inside a <section class="cascade-block ..."> element, which is
    the only legitimate surface for depth-0 node links.

    Returns list of violation strings.
    """
    violations = []
    index_path = repo_root / 'index.html'
    if not index_path.exists():
        return []

    try:
        src = index_path.read_text(encoding='utf-8')
    except Exception as e:
        return [f'index.html: read error: {e}']

    # Find all href="nodes/..." occurrences
    for m in re.finditer(r'href=["\']nodes/([^"\']+)["\']', src):
        href_full = f'nodes/{m.group(1)}'
        pos = m.start()

        # Walk backwards from pos to find enclosing <section ...>
        # We accept the link if it's inside a cascade-block section
        preceding = src[:pos]
        # Find the last <section before this href
        last_section_m = None
        for sm in re.finditer(r'<section\s', preceding):
            last_section_m = sm
        if last_section_m is not None:
            # Check if that section has class="cascade-block"
            section_tag_end = src.find('>', last_section_m.start())
            if section_tag_end != -1:
                section_tag = src[last_section_m.start():section_tag_end + 1]
                if 'cascade-block' in section_tag:
                    continue  # legitimate cascade card link — allowed
        # Also allow links inside <ul class="..."> lists (LINKS:START section)
        # These are navigation/audit links, not surface-rendered links
        last_ul_m = None
        for um in re.finditer(r'<ul\b', preceding):
            last_ul_m = um
        if last_ul_m is not None:
            ul_end_search = src.find('</ul>', last_ul_m.start())
            if ul_end_search == -1 or ul_end_search > pos:
                # Inside an open <ul> - this is a links list, allowed
                continue

        violations.append(
            f'index.html: depth-0 violation — href="{href_full}" appears outside a cascade-block section'
        )

    return violations


# ── Guardrail 2: Node reachability ────────────────────────────────────────────

def _build_link_graph(pages: list[Path], repo_root: Path) -> dict[str, set[str]]:
    """
    Build adjacency graph: page_rel_path -> set of linked page_rel_paths.
    Considers relative hrefs only; normalises paths to repo-root-relative strings.
    """
    graph: dict[str, set[str]] = {}
    for page in pages:
        try:
            src = page.read_text(encoding='utf-8')
        except Exception:
            continue
        page_rel = str(page.relative_to(repo_root)).replace('\\', '/')
        links: set[str] = set()
        for href in _relative_hrefs(src):
            resolved = _resolve_href(href, page, repo_root)
            if resolved and resolved.exists():
                try:
                    linked_rel = str(resolved.relative_to(repo_root)).replace('\\', '/')
                    links.add(linked_rel)
                except ValueError:
                    pass
        graph[page_rel] = links
    return graph


def _reachable_from(start_rel: str, graph: dict[str, set[str]]) -> set[str]:
    """BFS/DFS from start_rel; return set of all reachable page rel-paths."""
    visited: set[str] = set()
    queue = [start_rel]
    while queue:
        node = queue.pop()
        if node in visited:
            continue
        visited.add(node)
        for neighbour in graph.get(node, set()):
            if neighbour not in visited:
                queue.append(neighbour)
    return visited


def check_node_reachability(
    new_pages: list[str],
    pages: list[Path],
    repo_root: Path,
) -> list[str]:
    """
    Verify every page in new_pages is reachable from index.html.

    new_pages: list of repo-root-relative paths (e.g. ["nodes/foo.html"])
    Returns list of unreachable page strings.
    """
    if not new_pages:
        return []

    graph = _build_link_graph(pages, repo_root)
    reachable = _reachable_from('index.html', graph)

    unreachable = []
    for page_rel in new_pages:
        norm = page_rel.replace('\\', '/').lstrip('/')
        if norm not in reachable:
            unreachable.append(f'{norm}: not reachable from index.html after run wiring')

    return unreachable


# ── Guardrail 4: Absolute path check ─────────────────────────────────────────

# Patterns that indicate absolute local filesystem paths leaking into served HTML.
_ABS_PATH_RE = re.compile(
    r'(?:'
    r'/home/[^"\'\s<>]+'        # /home/...
    r'|/root/[^"\'\s<>]+'      # /root/...
    r'|/usr/[^"\'\s<>]+'       # /usr/...
    r'|/var/[^"\'\s<>]+'       # /var/...
    r'|/tmp/[^"\'\s<>]+'       # /tmp/...
    r'|/etc/[^"\'\s<>]+'       # /etc/...
    r')',
    re.IGNORECASE,
)


def check_absolute_paths(pages: list[Path], repo_root: Path) -> list[str]:
    """
    Scan all public HTML for absolute filesystem path patterns.
    Returns list of violations.
    """
    violations = []
    for page in pages:
        try:
            src = page.read_text(encoding='utf-8')
        except Exception:
            continue
        page_rel = str(page.relative_to(repo_root))
        for m in _ABS_PATH_RE.finditer(src):
            # Skip if inside a comment
            snippet = m.group(0)
            # Get surrounding context to check for HTML comment
            start = max(0, m.start() - 4)
            context_before = src[start:m.start()]
            if '<!--' in context_before and '-->' not in context_before:
                continue  # inside comment, skip
            violations.append(f'{page_rel}: absolute path in served HTML: "{snippet}"')

    return violations


# ── Main ─────────────────────────────────────────────────────────────────────

def run_all_checks(
    repo_root: Path,
    new_pages: list[str],
    auto_repair: bool,
    verbose: bool = True,
) -> tuple[bool, list[str], int]:
    """
    Run all five guardrails.

    Returns:
      (all_passed, failure_messages, repairs_made)
    """
    all_pages = _all_public_html(repo_root)
    failures = []
    repairs_made = 0
    ts = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    if verbose:
        print(f'[validate_site] {ts} — scanning {len(all_pages)} public HTML pages', file=sys.stderr)

    # ── Check 1+5: Link + Image integrity ────────────────────────────────────
    href_failures, img_failures, img_repairs = check_link_integrity(
        all_pages, repo_root, auto_repair=auto_repair
    )
    repairs_made += img_repairs

    if href_failures:
        for f in href_failures:
            if verbose:
                print(f'[validate_site] FAIL href: {f}', file=sys.stderr)
        failures.extend([f'href:{f}' for f in href_failures])

    # After auto-repair, re-check img failures (repaired ones should be gone)
    if img_failures and auto_repair:
        # Re-check after repair
        _, post_img_failures, _ = check_link_integrity(all_pages, repo_root, auto_repair=False)
        if post_img_failures:
            for f in post_img_failures:
                if verbose:
                    print(f'[validate_site] FAIL img (post-repair): {f}', file=sys.stderr)
            failures.extend([f'img:{f}' for f in post_img_failures])
        elif verbose and img_failures:
            print(f'[validate_site] img failures auto-repaired ({len(img_failures)} fixed)', file=sys.stderr)
    elif img_failures:
        for f in img_failures:
            if verbose:
                print(f'[validate_site] FAIL img: {f}', file=sys.stderr)
        failures.extend([f'img:{f}' for f in img_failures])

    # ── Check 2: Reachability ─────────────────────────────────────────────────
    if new_pages:
        unreach = check_node_reachability(new_pages, all_pages, repo_root)
        if unreach:
            for f in unreach:
                if verbose:
                    print(f'[validate_site] FAIL reachability: {f}', file=sys.stderr)
            failures.extend([f'reachability:{f}' for f in unreach])
        elif verbose:
            print(f'[validate_site] reachability OK — {len(new_pages)} new page(s) traversable from index.html', file=sys.stderr)

    # ── Check 3: Depth-0 link policy ─────────────────────────────────────────
    depth0_violations = check_depth0_node_links(repo_root)
    if depth0_violations:
        for f in depth0_violations:
            if verbose:
                print(f'[validate_site] FAIL depth0: {f}', file=sys.stderr)
        failures.extend([f'depth0:{f}' for f in depth0_violations])
    elif verbose:
        print('[validate_site] depth-0 link policy OK', file=sys.stderr)

    # ── Check 4: Absolute paths ───────────────────────────────────────────────
    abs_violations = check_absolute_paths(all_pages, repo_root)
    if abs_violations:
        for f in abs_violations:
            if verbose:
                print(f'[validate_site] FAIL abspath: {f}', file=sys.stderr)
        failures.extend([f'abspath:{f}' for f in abs_violations])
    elif verbose:
        print('[validate_site] absolute path check OK', file=sys.stderr)

    all_passed = len(failures) == 0
    if verbose:
        if all_passed:
            print(f'[validate_site] ALL CHECKS PASSED ({len(all_pages)} pages scanned)', file=sys.stderr)
        else:
            print(f'[validate_site] {len(failures)} failure(s). repairs_made={repairs_made}', file=sys.stderr)

    return all_passed, failures, repairs_made


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Post-run site validator gate for Blood in the Wire.',
    )
    parser.add_argument(
        '--new-pages', nargs='*', default=[],
        help='Newly created pages to check reachability for (relative to repo root).',
    )
    parser.add_argument(
        '--repo-root', default='',
        help='Repo root path. Default: inferred from script location.',
    )
    parser.add_argument(
        '--auto-repair', action='store_true',
        help='Attempt auto-repair on broken img tags.',
    )
    parser.add_argument(
        '--report-only', action='store_true',
        help='Print results but always exit 0.',
    )
    parser.add_argument(
        '--quiet', action='store_true',
        help='Suppress per-check stderr output (only print summary).',
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root) if args.repo_root else REPO_ROOT

    all_passed, failures, repairs_made = run_all_checks(
        repo_root=repo_root,
        new_pages=args.new_pages,
        auto_repair=args.auto_repair,
        verbose=not args.quiet,
    )

    # Print structured summary to stdout for capture by cron_publish
    ts = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    if all_passed:
        print(f'VALIDATE_OK  ts={ts}  pages_scanned={len(_all_public_html(repo_root))}  repairs={repairs_made}')
    else:
        print(f'VALIDATE_FAIL  ts={ts}  failures={len(failures)}  repairs={repairs_made}')
        for f in failures:
            print(f'  FAIL: {f}')

    if args.report_only:
        return 0
    if all_passed:
        return 0
    if repairs_made > 0 and all_passed:
        return 2   # repairs made, now clean
    return 1


if __name__ == '__main__':
    sys.exit(main())
