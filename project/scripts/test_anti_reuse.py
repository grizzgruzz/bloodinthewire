#!/usr/bin/env python3
"""
test_anti_reuse.py - Controlled test for the v9 image anti-reuse policy.

Tests:
  1. Branch-local guard: a depth=1 call using an image already in ancestor_images
     should be rejected and replaced with a fresh library asset (or text-only).
  2. Surface-to-deep guard: a depth=1 call using an image that appears in the
     last N surface branch-log entries should be rejected similarly.
  3. Clean path: a depth=1 call using an image NOT in the deny set should pass
     through unchanged.

Exit code: 0 on pass, 1 on any assertion failure.
"""

import sys
import os
from pathlib import Path

# ── Make scripts importable ───────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from branch_publish import (
    _anti_reuse_deep_image,
    _recent_surface_image_basenames,
    _pick_fresh_library_image,
    REPO_ROOT,
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

errors = []

def assert_eq(label, got, expected):
    if got == expected:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}")
        print(f"         expected: {expected!r}")
        print(f"         got:      {got!r}")
        errors.append(label)

def assert_truthy(label, val):
    if val:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: expected truthy, got {val!r}")
        errors.append(label)

def assert_falsy(label, val):
    if not val:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: expected falsy, got {val!r}")
        errors.append(label)

# ── Build a synthetic branch_log with recent surface entries ──────────────────
FAKE_IMG_A = "project/assets/web/fakeimgA_20260318-120000.jpg"
FAKE_IMG_B = "project/assets/web/fakeimgB_20260318-130000.png"
FAKE_IMG_C = "project/assets/web/fakeimgC_20260318-140000.jpg"

branch_log_synthetic = {
    "entries": [
        # Recent surface entries (depth=0) with images
        {"depth": 0, "image_web_path": FAKE_IMG_A, "image_source": "incoming"},
        {"depth": 0, "image_web_path": FAKE_IMG_B, "image_source": "library"},
        {"depth": 0, "image_web_path": "",          "image_source": "library"},   # no image
        {"depth": 0, "image_web_path": FAKE_IMG_C, "image_source": "incoming"},
    ]
}

print()
print("=" * 64)
print("Test 1: _recent_surface_image_basenames")
print("=" * 64)
result = _recent_surface_image_basenames(branch_log_synthetic, cooldown=10)
# Should contain basenames of A, B, C (not empty string)
assert_truthy("fakeimgA in surface basenames",  "fakeimgA_20260318-120000.jpg" in result)
assert_truthy("fakeimgB in surface basenames",  "fakeimgB_20260318-130000.png" in result)
assert_truthy("fakeimgC in surface basenames",  "fakeimgC_20260318-140000.jpg" in result)
assert_eq    ("size = 3 (empty path excluded)", len(result), 3)

print()
print("=" * 64)
print("Test 2: Branch-local guard — reused ancestor image is rejected")
print("=" * 64)
# Simulate: depth=1 candidate is the same as an ancestor (branch-local guard)
ancestor_imgs = frozenset({"fakeimgA_20260318-120000.jpg"})
empty_log     = {"entries": []}   # no surface history → surface-to-deep won't trigger
result = _anti_reuse_deep_image(
    image_web_path=FAKE_IMG_A,
    depth=1,
    ancestor_images=ancestor_imgs,
    branch_log=empty_log,
)
# The candidate was in ancestor_images → should be rejected.
# Result will be either a fresh library path or empty string (text-only).
assert_truthy("candidate rejected (result != FAKE_IMG_A)", result != FAKE_IMG_A)
print(f"         fallback result: {result!r}")

print()
print("=" * 64)
print("Test 3: Surface-to-deep guard — recent surface image rejected at depth=1")
print("=" * 64)
# Candidate is FAKE_IMG_B which appears in branch_log_synthetic's surface entries.
# ancestor_images is empty (no branch-local match), but surface-to-deep should catch it.
result = _anti_reuse_deep_image(
    image_web_path=FAKE_IMG_B,
    depth=1,
    ancestor_images=frozenset(),   # no branch-local ancestor match
    branch_log=branch_log_synthetic,
    cooldown=10,
)
assert_truthy("candidate rejected by surface-to-deep (result != FAKE_IMG_B)", result != FAKE_IMG_B)
print(f"         fallback result: {result!r}")

print()
print("=" * 64)
print("Test 4: Clean path — fresh image not in deny set passes through unchanged")
print("=" * 64)
# A completely different fake image not in any deny set.
FRESH_IMG = "project/assets/web/COMPLETELY_NEW_IMAGE_20260318-150000.jpg"
result = _anti_reuse_deep_image(
    image_web_path=FRESH_IMG,
    depth=1,
    ancestor_images=frozenset(),
    branch_log=empty_log,
)
assert_eq("fresh image unchanged", result, FRESH_IMG)

print()
print("=" * 64)
print("Test 5: depth=0 is never subject to anti-reuse guard")
print("=" * 64)
# Even if the image appears in the deny set, depth=0 bypasses the guard.
result = _anti_reuse_deep_image(
    image_web_path=FAKE_IMG_A,
    depth=0,
    ancestor_images=frozenset({"fakeimgA_20260318-120000.jpg"}),
    branch_log=branch_log_synthetic,
)
assert_eq("depth=0 passes through unchanged", result, FAKE_IMG_A)

print()
print("=" * 64)
print("Test 6: Cooldown window respected — old surface image NOT in deny set")
print("=" * 64)
# Only cooldown=1 recent surface entry: only fakeimgC should be blocked
result_deny = _recent_surface_image_basenames(branch_log_synthetic, cooldown=1)
# cooldown=1 → only the last surface entry with an image is included
assert_truthy("fakeimgC in cooldown=1 set",  "fakeimgC_20260318-140000.jpg" in result_deny)
assert_truthy("fakeimgA NOT in cooldown=1 set", "fakeimgA_20260318-120000.jpg" not in result_deny)
assert_eq("cooldown=1 set size = 1", len(result_deny), 1)

print()
print("=" * 64)
if errors:
    print(f"RESULT: {len(errors)} test(s) FAILED: {errors}")
    sys.exit(1)
else:
    print(f"RESULT: ALL TESTS PASSED")
    sys.exit(0)
