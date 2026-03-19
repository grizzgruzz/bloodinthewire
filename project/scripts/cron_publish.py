#!/usr/bin/env python3
"""
cron_publish.py  v3
===================
Cron orchestration wrapper for the Blood in the Wire automated publish pipeline.

PURPOSE
-------
This script is the entry point for unattended / scheduled runs.  It wraps the
full pipeline (asset selection → voice-request build → LLM draft generation →
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
   Note: in AUTO-GENERATE mode (default), generate_draft.py always selects
   an asset (consuming it from incoming/) so the narrative is paired with
   the note context.  The media gate controls whether that image is SHOWN
   in the published card.  The asset is always consumed when run gate passes.

TRACE OUTPUT
------------
Each run emits exactly one compact trace line to stdout (plus the normal
branch_publish summary for publish runs).  The trace line is suitable for
cron log capture and post-hoc auditing:

  SKIP       ts=... run_gate_roll=0.3241 p_run=0.50
  SKIP_RPT   ts=... reason=anti_repeat_guard  title=<stale title>
  PUB        ts=... media=yes  img_src=incoming  img=<path>  title=<title>
  PUB        ts=... media=no   title=<title>

Trace lines are also appended to project/cron-trace.log for permanent record.

MODES
-----
This script operates in three modes:

  AUTO-GENERATE mode (default): full autonomous pipeline.
    1. Calls generate_draft.py, which:
       - Calls select_asset.py (incoming/ preferred) to consume next asset.
       - Calls build_voice_request.py to assemble the Gemini prompt (includes
         VOICE_BIBLE, GENERATOR_PROMPT, and paired note sidecar).
       - Sends prompt to Google Gemini API.
       - Validates response against GENERATOR_PROMPT strict format.
       - Runs anti-repeat guard against recent branch-log entries.
       - Writes fresh draft to project/content/drafts/voice-draft-current.txt.
    2. Rolls media gate to decide whether to show image in published card.
    3. Calls branch_publish.py with fresh title + (image or text-only).
    This mode NEVER reuses a stale voice-draft-current.txt.

  ASSEMBLE mode (--assemble-only): skip narrative generation; use an
    already-existing voice draft file provided via --voice-draft-file.
    Suitable for manual/human-in-the-loop workflows or fallback.
    Note: this mode DOES reuse a static draft file — use only when
    explicitly providing a fresh draft externally.

  DRY RUN (--dry-run): gate rolls happen and are traced, but NO files are
    written (no asset consumed, no post published, no HTML modified).

TYPICAL CRON USAGE
------------------
  # Auto-generate mode (recommended): fresh narrative every run
  0 */6 * * * cd /home/gruzz/bloodinthewire && \
      python3 project/scripts/cron_publish.py --auto-push \
          >> project/cron-trace.log 2>&1

  # Always run (testing/forced dry-run)
  python3 project/scripts/cron_publish.py --run-probability 1.0 --dry-run

VALIDATOR GATE (v3)
-------------------
After branch_publish.py completes and BEFORE any git push, validate_site.py
is run as a mandatory gate.  If validation fails:
  - Push is blocked (invalid output is never pushed).
  - Auto-repair is attempted for deterministic failures (broken img tags).
  - If repair succeeds and re-validation passes → push proceeds normally.
  - If repair fails or issues are non-deterministic → rollback (git checkout)
    of all site changes, log VALIDATE_FAIL trace line, exit non-zero.
  - health_report.py records validation failures in the daily health log.

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
        'project/logs',
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


def run_validator_gate(
    new_pages: list[str],
    auto_repair: bool = True,
) -> tuple[bool, list[str], int]:
    """
    Run validate_site.py as the post-publish gate before any git push.

    Returns: (passed, failures, repairs_made)
    """
    validate_script = SCRIPT_DIR / 'validate_site.py'
    cmd = [sys.executable, str(validate_script)]
    if new_pages:
        cmd += ['--new-pages'] + new_pages
    if auto_repair:
        cmd += ['--auto-repair']

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))

    failures = []
    repairs_made = 0

    for line in result.stdout.splitlines():
        if line.startswith('VALIDATE_OK'):
            # parse repairs count
            for part in line.split():
                if part.startswith('repairs='):
                    try:
                        repairs_made = int(part.split('=', 1)[1])
                    except ValueError:
                        pass
        elif line.startswith('VALIDATE_FAIL'):
            pass  # summary line, not a failure detail
        elif line.strip().startswith('FAIL:'):
            failures.append(line.strip()[5:].strip())

    if result.returncode == 0:
        return True, failures, repairs_made
    else:
        return False, failures, repairs_made


def rollback_site_changes() -> tuple[bool, str]:
    """
    Rollback all uncommitted changes to public site files after a validation failure.

    Runs: git checkout -- index.html fragments/ nodes/ project/assets/web/
    Only rolls back the generated/published files, not config or logs.

    Returns: (ok, status_text)
    """
    rollback_cmd = [
        'git', 'checkout', '--',
        'index.html',
        'fragments',
        'nodes',
        'project/assets/web',
    ]
    result = subprocess.run(
        rollback_cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False, f'rollback_failed:{result.stderr.strip() or result.stdout.strip()}'
    return True, 'rolled_back'


def run_health_report(
    date_str: str,
    validation_failures: list[str],
    force: bool = False,
) -> None:
    """Run health_report.py to append/update the daily health log."""
    health_script = SCRIPT_DIR / 'health_report.py'
    if not health_script.exists():
        return
    cmd = [sys.executable, str(health_script), '--date', date_str]
    if force:
        cmd.append('--force')
    if validation_failures:
        cmd += ['--validation-failures'] + validation_failures
    try:
        subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30)
    except Exception as exc:
        print(f'[cron_publish] WARNING: health_report.py failed: {exc}', file=sys.stderr)


def _get_new_pages_from_log(pre_entry_count: int) -> list[str]:
    """
    Return list of newly created page paths (relative to repo root) by
    comparing branch-log entry count before and after branch_publish.
    """
    new_pages = []
    try:
        data = json.loads(BRANCH_LOG.read_text(encoding='utf-8'))
        entries = data.get('entries', [])
        for entry in entries[pre_entry_count:]:
            dest = entry.get('dest_page', '')
            if dest and dest not in new_pages:
                new_pages.append(dest)
    except Exception:
        pass
    return new_pages


def _count_branch_log_entries() -> int:
    """Return current count of entries in branch-log.json."""
    try:
        data = json.loads(BRANCH_LOG.read_text(encoding='utf-8'))
        return len(data.get('entries', []))
    except Exception:
        return 0


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

    # ── RESOLVE VOICE DRAFT + ASSET ───────────────────────────────────────────
    image_web_path = ''
    image_source   = ''
    media_status   = 'no'
    title  = args.title
    teaser = args.teaser

    if args.assemble_only or args.voice_draft_file:
        # ── ASSEMBLE MODE: use existing static draft file ─────────────────────
        # NOTE: This mode reuses a static draft. Use only when providing a
        # fresh draft externally. For autonomous cron, use AUTO-GENERATE mode.

        # Asset selection (same as before — separate from draft)
        if media_gate:
            has_assets = _has_incoming_assets() or _has_library_assets()
            if not has_assets:
                print('[cron_publish] INFO: media gate hit but no assets — degrading to text-only.',
                      file=sys.stderr)
                media_gate = False
                media_status = 'no (no assets)'
            elif args.dry_run:
                img_src_label = 'incoming' if _has_incoming_assets() else 'library'
                image_source  = img_src_label
                media_status  = f'yes (dry-run, would use {img_src_label})'
            else:
                result = _run_py([str(SCRIPT_DIR / 'select_asset.py'), '--level', 'surface'])
                if result.returncode != 0:
                    print(f'[cron_publish] WARNING: select_asset.py failed — text-only.',
                          file=sys.stderr)
                    media_status = 'no (select_asset failed)'
                else:
                    image_web_path = result.stdout.strip()
                    image_source = 'incoming' if 'SOURCE=incoming' in result.stderr else 'library'
                    media_status = f'yes  img_src={image_source}  img={Path(image_web_path).name}'

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

        if not title or not teaser:
            try:
                import re as _re
                text = draft_path.read_text(encoding='utf-8')
                if not title:
                    m = _re.search(r'^TITLE:\s*(.+)$', text, _re.MULTILINE)
                    if m:
                        title = m.group(1).strip()
                if not teaser:
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
            teaser = title

    else:
        # ── AUTO-GENERATE MODE: call generate_draft.py for fresh narrative ────
        # generate_draft.py handles:
        #   1. select_asset.py (asset selection + note pairing)
        #   2. build_voice_request.py (prompt assembly)
        #   3. Gemini API call (narrative generation)
        #   4. Anti-repeat guard (title similarity vs recent entries)
        #   5. Writes fresh voice-draft-current.txt

        # Determine if we have assets to generate with
        has_assets = _has_incoming_assets() or _has_library_assets()

        if args.dry_run:
            img_src_label = 'incoming' if _has_incoming_assets() else 'library'
            media_status = (
                f'yes (dry-run, would use {img_src_label})'
                if (media_gate and has_assets)
                else 'no (dry-run)'
            )
            trace = (
                f'DRY   ts={now}'
                f'  run_gate_roll={run_roll:.4f}  p_run={args.run_probability:.2f}'
                f'  media_gate_roll={media_roll:.4f}  p_media={args.media_probability:.2f}'
                f'  media={media_status}'
                f'  mode=auto-generate'
                f'  depth_cap={args.depth_cap}'
            )
            print(trace)
            append_trace(trace)
            print('[cron_publish] DRY RUN complete — no files modified.', file=sys.stderr)
            return 0

        if not has_assets:
            print('[cron_publish] INFO: no assets in incoming/ or library/ — cannot auto-generate.',
                  file=sys.stderr)
            trace = (
                f'SKIP  ts={now}'
                f'  run_gate_roll={run_roll:.4f}  p_run={args.run_probability:.2f}'
                f'  reason=no_assets_for_auto_generate'
            )
            print(trace)
            append_trace(trace)
            return 0

        print('[cron_publish] AUTO-GENERATE: calling generate_draft.py ...', file=sys.stderr)
        gd_result = _run_py([str(SCRIPT_DIR / 'generate_draft.py')])

        if gd_result.returncode == 2:
            # SKIP_REPEAT: anti-repeat guard triggered
            # Extract title from stdout if available
            skip_title = ''
            for line in gd_result.stdout.splitlines():
                if line.startswith('DRAFT_TITLE='):
                    skip_title = line.split('=', 1)[1].strip()
            trace = (
                f'SKIP_RPT  ts={now}'
                f'  run_gate_roll={run_roll:.4f}  p_run={args.run_probability:.2f}'
                f'  reason=anti_repeat_guard'
                f'  title={skip_title!r}'
            )
            print(trace)
            append_trace(trace)
            print('[cron_publish] SKIP: anti-repeat guard prevented stale concept reuse.',
                  file=sys.stderr)
            return 0

        if gd_result.returncode != 0:
            trace = (
                f'ERROR ts={now}'
                f'  run_gate_roll={run_roll:.4f}  p_run={args.run_probability:.2f}'
                f'  err=generate_draft_failed_rc={gd_result.returncode}'
            )
            print(trace, file=sys.stderr)
            append_trace(trace)
            print(f'[cron_publish] generate_draft.py stderr:\n{gd_result.stderr[:500]}',
                  file=sys.stderr)
            return 1

        # Parse generate_draft.py stdout for title, evidence, image path
        draft_image_web_path = ''
        for line in gd_result.stdout.splitlines():
            if line.startswith('DRAFT_TITLE='):
                if not title:
                    title = line.split('=', 1)[1].strip()
            elif line.startswith('DRAFT_EVIDENCE='):
                if not teaser:
                    teaser = line.split('=', 1)[1].strip()
            elif line.startswith('IMAGE_WEB_PATH='):
                draft_image_web_path = line.split('=', 1)[1].strip()

        # Determine image source from generate_draft.py stderr
        if 'SOURCE=incoming' in gd_result.stderr:
            image_source = 'incoming'
        else:
            image_source = 'library'

        # Apply media gate: if gate HIT, use the image generate_draft selected
        # If gate MISS, publish text-only (asset was still consumed for note pairing)
        if media_gate and draft_image_web_path:
            image_web_path = draft_image_web_path
            media_status = f'yes  img_src={image_source}  img={Path(image_web_path).name}'
        else:
            media_status = f'no (media_gate={media_gate})'

        if not title:
            # Fallback: try to read from the written draft file
            try:
                import re as _re
                text = VOICE_DRAFT_CURRENT.read_text(encoding='utf-8')
                m = _re.search(r'^TITLE:\s*(.+)$', text, _re.MULTILINE)
                if m:
                    title = m.group(1).strip()
                if not teaser:
                    m = _re.search(r'^EVIDENCE_LINE:\s*(.+)$', text, _re.MULTILINE)
                    if m:
                        teaser = m.group(1).strip()
            except Exception:
                pass

        if not title:
            trace = (
                f'ERROR ts={now}'
                f'  run_gate_roll={run_roll:.4f}'
                f'  err=no_title_after_generate_draft'
            )
            print(trace, file=sys.stderr)
            append_trace(trace)
            return 1

        if not teaser:
            teaser = title

    # ── DRY RUN SUMMARY (assemble mode only reaches here with --dry-run) ─────
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
        print('[cron_publish] DRY RUN complete — no files modified.', file=sys.stderr)
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

    # Snapshot branch-log entry count BEFORE branch_publish runs
    pre_entry_count = _count_branch_log_entries()

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
        run_health_report(posted_date, [f'branch_publish_failed rc={bp_result.returncode}'])
        return bp_result.returncode

    # Detect newly created pages for reachability check
    new_pages = _get_new_pages_from_log(pre_entry_count)

    # ── VALIDATOR GATE (v3) ────────────────────────────────────────────────────
    # Run validate_site.py BEFORE any git push. If validation fails:
    #   - auto-repair deterministic issues (broken img tags), re-validate once
    #   - if still failing: rollback site changes, log VALIDATE_FAIL, block push
    validation_failures: list[str] = []
    validator_passed = True

    print('[cron_publish] Running post-publish validator gate ...', file=sys.stderr)
    passed, failures, repairs = run_validator_gate(new_pages=new_pages, auto_repair=True)

    if not passed:
        validation_failures = failures
        val_fail_trace = (
            f'VALIDATE_FAIL  ts={ts_utc()}'
            f'  title={title!r}'
            f'  failures={len(failures)}'
            f'  repairs_attempted={repairs}'
            f'  details={";".join(failures[:5])!r}'
        )
        print(val_fail_trace, file=sys.stderr)
        append_trace(val_fail_trace)

        print('[cron_publish] Validator gate FAILED. Rolling back site changes.', file=sys.stderr)
        rb_ok, rb_status = rollback_site_changes()
        rollback_trace = (
            f'ROLLBACK  ts={ts_utc()}'
            f'  status={rb_status}'
            f'  reason=validation_failed'
        )
        print(rollback_trace, file=sys.stderr)
        append_trace(rollback_trace)

        run_health_report(posted_date, validation_failures, force=True)

        trace = (
            f'ERROR ts={now}'
            f'  run_gate_roll={run_roll:.4f}  p_run={args.run_probability:.2f}'
            f'  media={media_status}'
            f'  title={title!r}'
            f'  err=validation_failed_rollback={rb_status}'
        )
        print(trace, file=sys.stderr)
        append_trace(trace)
        return 1

    if repairs > 0:
        print(f'[cron_publish] Validator: {repairs} auto-repair(s) applied and verified.', file=sys.stderr)

    print('[cron_publish] Validator gate PASSED.', file=sys.stderr)
    validator_passed = True

    # ── SUCCESS TRACE ─────────────────────────────────────────────────────────
    trace = (
        f'PUB   ts={now}'
        f'  run_gate_roll={run_roll:.4f}  p_run={args.run_probability:.2f}'
        f'  media_gate_roll={media_roll:.4f}  p_media={args.media_probability:.2f}'
        f'  media={media_status}'
        f'  title={title!r}'
        f'  depth_cap={args.depth_cap}'
        f'  validator=ok'
        f'  repairs={repairs}'
        f'  new_pages={len(new_pages)}'
    )
    print(trace)
    append_trace(trace)

    # Run health report (nightly — idempotent, won't overwrite existing)
    run_health_report(posted_date, [])

    if args.auto_push:
        safe_title = ''.join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()[:80]
        commit_msg = f'Auto publish: {posted_date} - {safe_title or "untitled"}'
        # Also stage health report and validate log
        ok, push_state = auto_commit_push(commit_msg)
        push_trace = f'PUSH  ts={ts_utc()}  status={push_state}'
        print(push_trace)
        append_trace(push_trace)
        if not ok:
            return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
