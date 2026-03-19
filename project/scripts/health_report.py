#!/usr/bin/env python3
"""
health_report.py
================
Nightly health report generator for Blood in the Wire.

Produces a compact daily summary appended to project/logs/health-YYYY-MM-DD.log
(one file per day, idempotent — re-runs on same day append nothing new).

REPORT CONTENTS:
----------------
  - Run stats: attempted / published / skipped / failed today
  - Validation failures from cron-trace.log
  - Growth stats: new pages today, max depth in tree, total inline link count
  - Site summary: total pages (index + fragments + nodes), last modified page

USAGE:
------
  python3 health_report.py [--repo-root /path/to/repo] [--date YYYY-MM-DD] [--force]

  --date   : Report for this date (default: today UTC)
  --force  : Overwrite existing daily report (default: skip if exists)

EXIT CODES:
-----------
  0 = report written (or already existed and --force not set)
  1 = error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR  = Path(__file__).resolve().parent
REPO_ROOT   = SCRIPT_DIR.parent.parent   # /home/gruzz/bloodinthewire
PROJECT_DIR = REPO_ROOT / 'project'
CRON_LOG    = PROJECT_DIR / 'cron-trace.log'
BRANCH_LOG  = PROJECT_DIR / 'branch-log.json'
LOGS_DIR    = PROJECT_DIR / 'logs'

# ── Helpers ───────────────────────────────────────────────────────────────────

def today_utc() -> str:
    return time.strftime('%Y-%m-%d', time.gmtime())


def _all_public_html(repo_root: Path) -> list[Path]:
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


def parse_cron_trace(date_str: str) -> dict:
    """
    Parse cron-trace.log for entries on the given date.

    Returns dict with keys:
      attempted, published, skipped, errors, validation_failures, lines
    """
    stats = {
        'attempted': 0,
        'published': 0,
        'skipped': 0,
        'errors': 0,
        'validation_failures': 0,
        'lines': [],
    }
    if not CRON_LOG.is_file():
        return stats

    try:
        lines = CRON_LOG.read_text(encoding='utf-8').splitlines()
    except Exception:
        return stats

    for line in lines:
        # Filter to today's entries by ts=YYYY-MM-DD prefix in the line
        if f'ts={date_str}' not in line:
            continue
        stats['lines'].append(line.strip())
        upper = line.upper()
        if line.startswith('PUB '):
            stats['attempted'] += 1
            stats['published'] += 1
        elif line.startswith('SKIP') or line.startswith('SKIP_RPT'):
            stats['attempted'] += 1
            stats['skipped'] += 1
        elif line.startswith('DRY '):
            stats['attempted'] += 1
            stats['skipped'] += 1
        elif line.startswith('ERROR'):
            stats['attempted'] += 1
            stats['errors'] += 1
        elif 'VALIDATE_FAIL' in upper:
            stats['validation_failures'] += 1

    return stats


def parse_branch_log_today(date_str: str) -> dict:
    """
    Parse branch-log.json for entries with posted_date or timestamp_utc matching today.

    Returns dict with keys:
      new_pages, max_depth, all_depths, entries
    """
    result = {
        'new_pages': [],
        'max_depth': 0,
        'all_depths': [],
        'entries': [],
    }
    if not BRANCH_LOG.is_file():
        return result

    try:
        data = json.loads(BRANCH_LOG.read_text(encoding='utf-8'))
    except Exception:
        return result

    for entry in data.get('entries', []):
        # Match by posted_date or timestamp_utc date portion
        posted = entry.get('posted_date', '')
        ts_utc = entry.get('timestamp_utc', '')
        ts_date = ts_utc[:10] if ts_utc else ''
        if date_str not in (posted, ts_date):
            continue

        result['entries'].append(entry)
        depth = entry.get('depth', 0)
        result['all_depths'].append(depth)
        if depth > result['max_depth']:
            result['max_depth'] = depth

        # Track newly created pages
        dest_page = entry.get('dest_page', '')
        if dest_page and dest_page not in result['new_pages']:
            result['new_pages'].append(dest_page)

    return result


def count_inline_links(repo_root: Path) -> int:
    """Count all wire-inline-link anchors across all public HTML pages."""
    total = 0
    for page in _all_public_html(repo_root):
        try:
            src = page.read_text(encoding='utf-8')
        except Exception:
            continue
        total += len(re.findall(r'class="wire-inline-link"', src))
    return total


def get_max_depth_in_tree(repo_root: Path) -> int:
    """Find maximum data-depth value in any public HTML page."""
    max_depth = 0
    for page in _all_public_html(repo_root):
        try:
            src = page.read_text(encoding='utf-8')
        except Exception:
            continue
        for m in re.finditer(r'data-depth="(\d+)"', src):
            d = int(m.group(1))
            if d > max_depth:
                max_depth = d
        # Also check header stamps like depth=N
        for m in re.finditer(r'//\s*depth=(\d+)', src):
            d = int(m.group(1))
            if d > max_depth:
                max_depth = d
    return max_depth


def generate_report(
    repo_root: Path,
    date_str: str,
    validation_failures_today: list[str],
) -> str:
    """Generate the compact daily health report text."""
    cron_stats = parse_cron_trace(date_str)
    branch_stats = parse_branch_log_today(date_str)
    all_pages = _all_public_html(repo_root)
    inline_count = count_inline_links(repo_root)
    tree_max_depth = get_max_depth_in_tree(repo_root)

    # Count pages by type
    n_total = len(all_pages)
    n_nodes = len([p for p in all_pages if 'nodes/' in str(p)])
    n_frags = len([p for p in all_pages if 'fragments/' in str(p)])

    # Last modified page
    last_mod_page = ''
    last_mod_ts = 0
    for p in all_pages:
        try:
            mtime = p.stat().st_mtime
            if mtime > last_mod_ts:
                last_mod_ts = mtime
                last_mod_page = str(p.relative_to(repo_root))
        except Exception:
            pass

    lines = [
        f'# bloodinthewire health report — {date_str}',
        f'# generated: {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}',
        '',
        '## run stats',
        f'  attempted:            {cron_stats["attempted"]}',
        f'  published:            {cron_stats["published"]}',
        f'  skipped:              {cron_stats["skipped"]}',
        f'  errors:               {cron_stats["errors"]}',
        f'  validation_failures:  {cron_stats["validation_failures"]}',
        '',
        '## site growth',
        f'  total pages:          {n_total}  (index=1, fragments={n_frags}, nodes={n_nodes})',
        f'  new pages today:      {len(branch_stats["new_pages"])}',
        f'  tree max depth:       {tree_max_depth}',
        f'  inline link count:    {inline_count}',
        f'  last modified:        {last_mod_page}',
        '',
    ]

    if branch_stats['new_pages']:
        lines.append('## new pages today')
        for p in branch_stats['new_pages']:
            lines.append(f'  + {p}')
        lines.append('')

    if validation_failures_today:
        lines.append('## validation failures')
        for f in validation_failures_today:
            lines.append(f'  ! {f}')
        lines.append('')
    else:
        lines.append('## validation: OK (no failures)')
        lines.append('')

    if cron_stats['lines']:
        lines.append('## cron activity today')
        for l in cron_stats['lines']:
            lines.append(f'  {l}')
        lines.append('')

    return '\n'.join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Nightly health report for Blood in the Wire.',
    )
    parser.add_argument('--repo-root', default='',
                        help='Repo root path. Default: inferred.')
    parser.add_argument('--date', default='',
                        help='Date to report on (YYYY-MM-DD). Default: today UTC.')
    parser.add_argument('--force', action='store_true',
                        help='Overwrite existing daily report.')
    parser.add_argument('--validation-failures', nargs='*', default=[],
                        help='Validation failure strings to include in report.')
    args = parser.parse_args()

    repo_root = Path(args.repo_root) if args.repo_root else REPO_ROOT
    date_str = args.date or today_utc()

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOGS_DIR / f'health-{date_str}.log'

    if report_path.exists() and not args.force:
        print(
            f'[health_report] Report for {date_str} already exists: {report_path}. '
            f'Use --force to overwrite.',
            file=sys.stderr,
        )
        return 0

    try:
        report_text = generate_report(
            repo_root=repo_root,
            date_str=date_str,
            validation_failures_today=args.validation_failures,
        )
    except Exception as exc:
        print(f'[health_report] ERROR generating report: {exc}', file=sys.stderr)
        return 1

    try:
        report_path.write_text(report_text, encoding='utf-8')
        print(f'[health_report] Report written → {report_path}', file=sys.stderr)
        print(f'HEALTH_REPORT_OK  date={date_str}  path={report_path}')
    except Exception as exc:
        print(f'[health_report] ERROR writing report: {exc}', file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
