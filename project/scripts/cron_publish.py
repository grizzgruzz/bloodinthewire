#!/usr/bin/env python3
"""
cron_publish.py  v1
===================
Cron orchestration wrapper for the Blood in the Wire automated publish pipeline.

PURPOSE
-------
This script is the entry point for unattended / scheduled runs.  It wraps the
full pipeline (asset selection → voice-request build → post generation →
branch publish) with two top-level probabilistic gates and produces a compact,
auditable trace line for each execution.

GATES (rolled in order)
-----------------------
1. RUN GATE  — should this cron tick do anything at all?
   Default: 50% chance of publishing on any given run.
   Override: --run-probability 0.0..1.0  (0 = never, 1 = always).
   If gate says NO: print a "skip" trace line and exit cleanly (code 0).

2. MEDIA GATE (depth=0 only) — should the surface post include an image?
   Default: 70% chance of including media.
   Override: --media-probability 0.0..1.0
   If gate says YES: call select_asset.py (surface level) to pick an asset.
     - Incoming/ is preferred over library/ (hard rule, enforced by select_asset.py).
     - If no assets exist in either pool, degrades gracefully to text-only.
   If gate says NO: surface post is published without media (text-only card).

TRACE OUTPUT
------------
Each run emits exactly one compact trace line to stdout (plus the normal
branch_publish summary for publish runs).  The trace line is suitable for
cron log capture and post-hoc auditing:

  SKIP  ts=2026-03-18T14:00:00Z  run_gate_roll=0.3241  p_run=0.50
  PUB   ts=2026-03-18T14:00:00Z  run_gate_roll=0.7812  p_run=0.50  media=yes  img_src=incoming  img=<path>
  PUB   ts=2026-03-18T14:00:00Z  run_gate_roll=0.6511  p_run=0.50  media=no

Trace lines are also appended to project/cron-trace.log for permanent record.

MODES
-----
This script can operate in two modes:

  FULL mode (default): runs the entire automated pipeline.
    Requires that select_asset.py (asset selection), build_voice_request.py,
    and generate_post.py are available and can run autonomously.
    NOTE: The generate_post.py step requires pre-existing voice draft output
    from wirevoice-core.  In a fully automated context this step would call
    a wirevoice-core API / subprocess.  In the current workflow, voice
    generation is a manual human-in-the-loop step.

  ASSEMBLE mode (--assemble-only): runs only the asset selection and
    branch publish steps, using an already-existing voice draft file
    provided via --voice-draft-file.  Suitable for wiring into a cron
    that calls out to an LLM API externally and passes the result back.

  DRY RUN (--dry-run): gate rolls happen and are traced, but NO files are
    written (no asset consumed, no post published, no HTML modified).

TYPICAL CRON USAGE
------------------
  # Run every 6 hours; 50% run gate; publish with --assemble-only if a draft exists
  0 */6 * * * cd /home/gruzz/bloodinthewire && \
      python3 project/scripts/cron_publish.py \
          --assemble-only \
          --voice-draft-file project/content/drafts/voice-draft-current.txt \
          >> project/cron-trace.log 2>&1

  # Always run (testing/forced)
  python3 project/scripts/cron_publish.py --run-probability 1.0 --dry-run

USAGE
-----
  python3 cron_publish.py [options]

Options:
  --run-probability FLOAT    P(run on this tick). Default: 0.5
  --media-probability FLOAT  P(include media at surface). Default: 0.7
  --voice-draft-file PATH    Required for --assemble-only mode
  --title TITLE              Post title (required in assemble-only mode)
  --teaser TEASER            One-line teaser (required in assemble-only mode)
  --fragment-href HREF       Relative href to fragment page
  --timestamp HH:MM          Optional timestamp
  --posted-date YYYY-MM-DD   Post date (default: today)
  --links-note NOTE          Short note for related list (default: auto)
  --depth-cap N              Max branching depth (default: 30)
  --assemble-only            Skip voice generation; use existing draft file
  --dry-run                  Roll gates but do not write any files
  --force-run                Force run gate (ignore --run-probability)
  --force-media              Force media gate hit (ignore --media-probability)
  --no-media                 Force media gate miss (skip media selection)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).resolve().parent
REPO_ROOT    = SCRIPT_DIR.parent.parent          # /home/gruzz/bloodinthewire
PROJECT_DIR  = SCRIPT_DIR.parent                 # project/
CRON_LOG     = PROJECT_DIR / 'cron-trace.log'
BRANCH_LOG   = PROJECT_DIR / 'branch-log.json'
ASSETS_DIR   = PROJECT_DIR / 'assets'
INCOMING_DIR = ASSETS_DIR / 'incoming'
LIBRARY_DIR  = ASSETS_DIR / 'library'
VOICE_DRAFT_CURRENT = PROJECT_DIR / 'content' / 'drafts' / 'voice-draft-current.txt'

# ── Helpers ───────────────────────────────────────────────────────────────────

def ts_utc() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def today() -> str:
    return time.strftime('%Y-%m-%d')


def _run_py(args: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    """Run a Python 3 subprocess with the current interpreter."""
    cmd = [sys.executable] + args
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True)
    return subprocess.run(cmd)


def _has_incoming_assets() -> bool:
    """Return True if there are eligible images in incoming/."""
    if not INCOMING_DIR.is_dir():
        return False
    exts = {'.jpg', '.jpeg', '.png'}
    return any(
        f.is_file() and f.suffix.lower() in exts
        for f in INCOMING_DIR.iterdir()
    )


def _has_library_assets() -> bool:
    """Return True if there are eligible images in library/."""
    if not LIBRARY_DIR.is_dir():
        return False
    exts = {'.jpg', '.jpeg', '.png'}
    return any(
        f.is_file() and f.suffix.lower() in exts
        for f in LIBRARY_DIR.iterdir()
    )


def append_trace(line: str) -> None:
    """Append a trace line to cron-trace.log."""
    try:
        CRON_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CRON_LOG.open('a', encoding='utf-8') as fh:
            fh.write(line + '\n')
    except Exception as exc:
        print(f'[cron_publish] WARNING: could not write to cron-trace.log: {exc}',
              file=sys.stderr)


def auto_commit_push(commit_message: str) -> tuple[bool, str]:
    """
    Stage site/runtime outputs, commit if anything changed, and push.

    Returns: (ok, status_text)
    """
    add_cmd = [
        'git', 'add',
        'index.html',
        'fragments',
        'nodes',
        'project/branch-log.json',
        'project/cron-trace.log',
        'project/assets/web',
        'project/assets/published',
        'project/assets/library',
    ]
    add = subprocess.run(add_cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    if add.returncode != 0:
        return False, f'git_add_failed:{add.stderr.strip() or add.stdout.strip()}'

    has_changes = subprocess.run(
        ['git', 'diff', '--cached', '--quiet'],
        cwd=str(REPO_ROOT),
    )
    if has_changes.returncode == 0:
        return True, 'nothing_to_commit'

    commit = subprocess.run(
        ['git', 'commit', '-m', commit_message],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    if commit.returncode != 0:
        return False, f'git_commit_failed:{commit.stderr.strip() or commit.stdout.strip()}'

    push = subprocess.run(
        ['git', 'push'],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    if push.returncode != 0:
        return False, f'git_push_failed:{push.stderr.strip() or push.stdout.strip()}'

    return True, 'pushed'


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Cron orchestration wrapper for Blood in the Wire publish pipeline.',
    )
    parser.add_argument('--run-probability',   type=float, default=0.5,
                        help='P(run on this tick). Default: 0.5')
    parser.add_argument('--media-probability', type=float, default=0.7,
                        help='P(include media at surface). Default: 0.7')
    parser.add_argument('--voice-draft-file',  default='',
                        help='Path to existing strict-format voice draft (required with --assemble-only).')
    parser.add_argument('--title',             default='',
                        help='Post title (required in --assemble-only mode if not in draft).')
    parser.add_argument('--teaser',            default='',
                        help='One-line teaser (required in --assemble-only mode if not in draft).')
    parser.add_argument('--fragment-href',     default='',
                        help='Relative href to fragment page.')
    parser.add_argument('--timestamp',         default='',
                        help='HH:MM timestamp (optional).')
    parser.add_argument('--posted-date',       default='',
                        help='Post date YYYY-MM-DD (default: today).')
    parser.add_argument('--links-note',        default='',
                        help='Short note for related-list entry (default: auto).')
    parser.add_argument('--depth-cap',         type=int, default=30,
                        help='Max branching depth (default: 30).')
    parser.add_argument('--assemble-only',     action='store_true',
                        help='Skip voice generation; use --voice-draft-file directly.')
    parser.add_argument('--dry-run',           action='store_true',
                        help='Roll gates but do not write any files.')
    parser.add_argument('--force-run',         action='store_true',
                        help='Force run gate (ignore --run-probability).')
    parser.add_argument('--force-media',       action='store_true',
                        help='Force media gate hit.')
    parser.add_argument('--no-media',          action='store_true',
                        help='Force media gate miss (publish text-only).')
    parser.add_argument('--auto-push',         action='store_true',
                        help='After successful publish, auto-commit and push site/runtime outputs.')
    args = parser.parse_args()

    now = ts_utc()
    posted_date = args.posted_date or today()
    links_note  = args.links_note  or f'auto-publish {posted_date}'

    # ── GATE 1: RUN GATE ──────────────────────────────────────────────────────
    run_roll = random.random()
    if args.force_run:
        run_gate = True
    else:
        run_gate = run_roll < args.run_probability

    if not run_gate:
        trace = (
            f'SKIP  ts={now}'
            f'  run_gate_roll={run_roll:.4f}'
            f'  p_run={args.run_probability:.2f}'
        )
        print(trace)
        append_trace(trace)
        if args.auto_push:
            ok, push_state = auto_commit_push(f'Auto publish tick: skipped {now}')
            print(f'PUSH  ts={ts_utc()}  status={push_state}')
            if not ok:
                return 1
        return 0

    # ── GATE 2: MEDIA GATE (depth=0 surface only) ─────────────────────────────
    media_roll = random.random()
    if args.force_media:
        media_gate = True
    elif args.no_media:
        media_gate = False
    else:
        media_gate = media_roll < args.media_probability

    # Select asset if media gate hit
    image_web_path = ''
    image_source   = ''
    media_status   = 'no'

    if media_gate:
        # Try to get an asset.  Prefer incoming/ (handled by select_asset.py --level surface).
        # If no assets available at all, degrade to text-only gracefully.
        has_assets = _has_incoming_assets() or _has_library_assets()

        if not has_assets:
            print('[cron_publish] INFO: media gate hit but no assets available — degrading to text-only.',
                  file=sys.stderr)
            media_gate = False
            media_status = 'no (no assets)'
        elif args.dry_run:
            # In dry-run, just note what would happen
            img_src_label = 'incoming' if _has_incoming_assets() else 'library'
            image_source  = img_src_label
            media_status  = f'yes (dry-run, would use {img_src_label})'
        else:
            # Run select_asset.py --level surface
            result = _run_py([
                str(SCRIPT_DIR / 'select_asset.py'),
                '--level', 'surface',
            ])
            if result.returncode != 0:
                print(f'[cron_publish] WARNING: select_asset.py failed (rc={result.returncode}).',
                      file=sys.stderr)
                print(result.stderr, file=sys.stderr)
                print('[cron_publish] INFO: degrading to text-only.', file=sys.stderr)
                media_status = 'no (select_asset failed)'
            else:
                image_web_path = result.stdout.strip()
                # Determine source from select_asset.py stderr hints
                # select_asset logs SOURCE=incoming or SOURCE=library to stderr
                if 'SOURCE=incoming' in result.stderr:
                    image_source = 'incoming'
                else:
                    image_source = 'library'
                media_status = f'yes  img_src={image_source}  img={Path(image_web_path).name}'

    # ── RESOLVE VOICE DRAFT ───────────────────────────────────────────────────
    if args.assemble_only or args.voice_draft_file:
        draft_file = args.voice_draft_file or str(VOICE_DRAFT_CURRENT)
        draft_path = Path(draft_file)
        if not draft_path.is_absolute():
            draft_path = REPO_ROOT / draft_path

        if not draft_path.is_file():
            trace = (
                f'ERROR ts={now}'
                f'  run_gate_roll={run_roll:.4f}  p_run={args.run_probability:.2f}'
                f'  media={media_status}'
                f'  err=voice_draft_not_found:{draft_path}'
            )
            print(trace, file=sys.stderr)
            append_trace(trace)
            return 1

        # Parse title/teaser from draft if not provided on command line
        title  = args.title
        teaser = args.teaser
        if not title or not teaser:
            try:
                import re as _re
                text = draft_path.read_text(encoding='utf-8')
                if not title:
                    m = _re.search(r'^TITLE:\s*(.+)$', text, _re.MULTILINE)
                    if m:
                        title = m.group(1).strip()
                if not teaser:
                    # Use EVIDENCE_LINE as teaser if no teaser given
                    m = _re.search(r'^EVIDENCE_LINE:\s*(.+)$', text, _re.MULTILINE)
                    if m:
                        teaser = m.group(1).strip()
            except Exception:
                pass

        if not title:
            trace = (
                f'ERROR ts={now}'
                f'  run_gate_roll={run_roll:.4f}'
                f'  err=no_title_could_not_parse_draft'
            )
            print(trace, file=sys.stderr)
            append_trace(trace)
            return 1

        if not teaser:
            teaser = title  # fallback: use title as teaser

    else:
        # Full mode: voice generation is a manual/external step.
        # In current workflow, wirevoice-core is human-in-the-loop.
        # This path prints a clear message explaining next steps.
        trace = (
            f'SKIP  ts={now}'
            f'  run_gate_roll={run_roll:.4f}  p_run={args.run_probability:.2f}'
            f'  reason=no_voice_draft_and_not_assemble_only'
            f'  hint=use_--voice-draft-file_or_--assemble-only'
        )
        print(trace)
        append_trace(trace)
        print(
            '\n[cron_publish] INFO: Full automated pipeline requires a pre-generated voice draft.\n'
            '  Use --assemble-only --voice-draft-file <path> to publish from an existing draft.\n'
            '  Or set VOICE_DRAFT_CURRENT in project/content/drafts/ for default file path.',
            file=sys.stderr,
        )
        return 0

    # ── DRY RUN SUMMARY ───────────────────────────────────────────────────────
    if args.dry_run:
        trace = (
            f'DRY   ts={now}'
            f'  run_gate_roll={run_roll:.4f}  p_run={args.run_probability:.2f}'
            f'  media_gate_roll={media_roll:.4f}  p_media={args.media_probability:.2f}'
            f'  media={media_status}'
            f'  title={title!r}'
            f'  depth_cap={args.depth_cap}'
        )
        print(trace)
        append_trace(trace)
        print(f'\n[cron_publish] DRY RUN complete — no files modified.\n', file=sys.stderr)
        return 0

    # ── BRANCH PUBLISH ────────────────────────────────────────────────────────
    bp_args = [
        str(SCRIPT_DIR / 'branch_publish.py'),
        '--title',       title,
        '--teaser',      teaser,
        '--posted-date', posted_date,
        '--links-note',  links_note,
        '--depth-cap',   str(args.depth_cap),
    ]
    if args.fragment_href:
        bp_args += ['--fragment-href', args.fragment_href]
    if args.timestamp:
        bp_args += ['--timestamp', args.timestamp]
    if image_web_path:
        bp_args += ['--image-web-path', image_web_path]
    if image_source:
        bp_args += ['--image-source', image_source]

    # Change to repo root so branch_publish.py resolves paths correctly
    bp_result = subprocess.run(
        [sys.executable] + bp_args,
        cwd=str(REPO_ROOT),
    )

    if bp_result.returncode != 0:
        trace = (
            f'ERROR ts={now}'
            f'  run_gate_roll={run_roll:.4f}  p_run={args.run_probability:.2f}'
            f'  media={media_status}'
            f'  title={title!r}'
            f'  err=branch_publish_failed_rc={bp_result.returncode}'
        )
        print(trace, file=sys.stderr)
        append_trace(trace)
        return bp_result.returncode

    # ── SUCCESS TRACE ─────────────────────────────────────────────────────────
    trace = (
        f'PUB   ts={now}'
        f'  run_gate_roll={run_roll:.4f}  p_run={args.run_probability:.2f}'
        f'  media_gate_roll={media_roll:.4f}  p_media={args.media_probability:.2f}'
        f'  media={media_status}'
        f'  title={title!r}'
        f'  depth_cap={args.depth_cap}'
    )
    print(trace)
    append_trace(trace)

    if args.auto_push:
        safe_title = ''.join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()[:80]
        commit_msg = f'Auto publish: {posted_date} - {safe_title or "untitled"}'
        ok, push_state = auto_commit_push(commit_msg)
        push_trace = f'PUSH  ts={ts_utc()}  status={push_state}'
        print(push_trace)
        if not ok:
            return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
